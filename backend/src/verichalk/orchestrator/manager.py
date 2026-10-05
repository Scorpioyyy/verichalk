"""运行管理器：创建运行、后台执行管线、取消、暂停 / 恢复（检查点）、重启恢复。

约定：
- 每个会话同一时刻最多一个进行中的运行（用户体验上"发送"在运行期间不可用）。
- `run.finished` 事件先于运行状态落盘写入；SSE 以该事件为结束标志，状态落盘后读者看到的一定是终态。
- 取消是协作式的：任务被取消后，已打开的 span 全部闭合，再发出 `run.finished(cancelled)`。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .. import trace
from ..core.config import Settings
from ..core.errors import Conflict, NotFound
from ..core.ids import new_id
from ..domain.events import CheckpointKind, CheckpointRequested, RunFinished, RunPaused, RunStarted, SpanKind
from ..domain.run import Attachment, Message, MessageRole, Run, RunStatus, Session
from ..knowledge import KnowledgeService
from ..llm import Budget, LLMGateway, bind_budget, unbind_budget
from ..stages import RunContext
from ..store import Store
from ..trace import EventBus, StoreSink, Tracer, error_info, use_tracer
from .pipelines import DEFAULT_PIPELINE, PIPELINES, Pipeline, PipelineResult, TurnInput

log = logging.getLogger("verichalk.orchestrator")


class RunManager:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        bus: EventBus,
        kb: KnowledgeService,
        llm: LLMGateway,
        pipelines: dict[str, Pipeline] | None = None,
    ) -> None:
        self.settings, self.store, self.bus, self.kb, self.llm = settings, store, bus, kb, llm
        self.pipelines = pipelines if pipelines is not None else PIPELINES
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._active_by_session: dict[str, str] = {}
        self._checkpoints: dict[str, asyncio.Future[dict[str, Any]]] = {}

    # ---- 生命周期 ----
    async def recover(self) -> int:
        """服务启动时调用：把遗留的进行中运行标记为中断。"""
        return await self.store.runs.mark_interrupted()

    async def shutdown(self) -> None:
        for t in list(self._tasks.values()):
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    # ---- 会话 ----
    async def create_session(self, title: str = "") -> Session:
        return await self.store.sessions.create(title)

    def active_run(self, session_id: str) -> str | None:
        return self._active_by_session.get(session_id)

    # ---- 轮次 ----
    async def start_turn(
        self,
        session_id: str,
        text: str,
        attachments: list[Attachment] | None = None,
        *,
        pipeline: str | None = None,
        tags: list[str] | None = None,
    ) -> Run:
        session = await self.store.sessions.get(session_id)
        if session_id in self._active_by_session:
            raise Conflict(f"会话 {session_id} 已有进行中的运行")
        name = pipeline or DEFAULT_PIPELINE
        if name not in self.pipelines:
            raise NotFound(f"管线不存在：{name}")
        attachments = attachments or []
        now = time.time()
        history = await self.store.messages.list(session_id)
        msg = Message(
            id=new_id("msg"),
            session_id=session_id,
            role=MessageRole.user,
            content=text,
            attachments=[a for a in attachments],
            ts=now,
        )
        run = Run(
            id=new_id("run"),
            session_id=session_id,
            message_id=msg.id,
            pipeline=name,
            created_at=now,
            tags=tags or [],
        )
        msg.run_id = run.id
        await self.store.messages.add(msg)
        await self.store.runs.create(run)
        await self.store.sessions.touch(
            session_id, title=(text.strip()[:24] or "新会话") if not session.title else None
        )
        ctx = RunContext(
            run_id=run.id,
            session_id=session_id,
            settings=self.settings,
            llm=self.llm,
            kb=self.kb,
            store=self.store,
            history=history,
        )
        ctx.ask_fn = lambda kind, prompt, options, payload: self._ask(run, kind, prompt, options, payload)
        self._active_by_session[session_id] = run.id
        task = asyncio.create_task(
            self._execute(run, ctx, TurnInput(text=text, attachments=attachments)), name=run.id
        )
        self._tasks[run.id] = task
        task.add_done_callback(lambda _t, rid=run.id: self._tasks.pop(rid, None))
        return run

    async def wait(self, run_id: str, timeout: float | None = None) -> Run:
        task = self._tasks.get(run_id)
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), timeout)
        return await self.store.runs.get(run_id)

    async def cancel(self, run_id: str) -> bool:
        task = self._tasks.get(run_id)
        if task is None:
            return False
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return True

    async def resume(self, run_id: str, answer: dict[str, Any]) -> None:
        fut = self._checkpoints.get(run_id)
        if fut is None or fut.done():
            raise Conflict("该运行没有等待中的检查点")
        fut.set_result(answer)

    # ---- 执行 ----
    async def _execute(self, run: Run, ctx: RunContext, turn: TurnInput) -> None:
        tracer = Tracer(run.id, StoreSink(self.store, self.bus))
        budget = Budget(self.settings.run_budget_tokens, self.settings.run_budget_cost)
        token = bind_budget(budget)
        status, err, result = "failed", None, PipelineResult()
        try:
            async with use_tracer(tracer):
                run.status, run.started_at = RunStatus.running, time.time()
                await self.store.runs.save(run)
                await tracer.emit(RunStarted(session_id=run.session_id, pipeline=run.pipeline))
                try:
                    async with trace.span(SpanKind.run, run.pipeline):
                        result = await self.pipelines[run.pipeline](ctx, turn)
                    status = "succeeded"
                except asyncio.CancelledError:
                    status = "cancelled"
                except BaseException as e:
                    err = error_info(e)
                    log.warning("run %s failed: %s", run.id, err.code)
                    if not isinstance(e, Exception):
                        raise
                if status == "succeeded" and result.reply_text:
                    await self.store.messages.add(
                        Message(
                            id=result.reply_message_id or new_id("msg"),
                            session_id=run.session_id,
                            role=MessageRole.assistant,
                            content=result.reply_text,
                            run_id=run.id,
                            ts=time.time(),
                        )
                    )
                run.state = ctx.state
                run.error, run.finished_at = err, time.time()
                await asyncio.shield(tracer.emit(RunFinished(status=status, error=err)))
                run.status = RunStatus(status)
                await self.store.runs.save(run)
        finally:
            unbind_budget(token)
            self._active_by_session.pop(run.session_id, None)
            self._checkpoints.pop(run.id, None)
            self.bus.notify(run.id)

    async def _ask(
        self,
        run: Run,
        kind: CheckpointKind,
        prompt: str,
        options: list[dict[str, Any]],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        cp_id = new_id("cp")
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._checkpoints[run.id] = fut
        run.status = RunStatus.awaiting_user
        run.checkpoint = {"id": cp_id, "kind": kind, "prompt": prompt, "options": options, "payload": payload}
        await self.store.runs.save(run)
        tracer = trace.current_tracer()
        await tracer.emit(
            CheckpointRequested(
                checkpoint_id=cp_id,
                kind=kind,
                prompt=prompt,
                options=options,
                payload=payload,
            )
        )
        await tracer.emit(RunPaused(checkpoint_id=cp_id))
        try:
            answer = await fut
        finally:
            self._checkpoints.pop(run.id, None)
        run.status, run.checkpoint = RunStatus.running, None
        await self.store.runs.save(run)
        return answer

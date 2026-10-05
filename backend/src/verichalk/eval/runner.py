"""评测运行器：把用例在进程内跑过完整管线（不经 HTTP），收集事件与指标。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ..core.config import Settings
from ..core.errors import VerichalkError
from ..domain.paper import Item, ItemKind, Paper, Revision, Section
from ..domain.run import Run, RunStatus
from ..metrics import AggregateMetrics, aggregate, compute_run_metrics
from ..orchestrator import Container, build_container
from ..store import Store
from .cases import Case
from .checks import CaseResult, RunResult, evaluate, reply_of

ContainerFactory = Callable[[Settings], Awaitable[Container]]


@dataclass
class SuiteResult:
    suite: str
    split: str
    settings: Settings
    cases: list[CaseResult] = field(default_factory=list)
    started_at: float = 0.0
    duration_s: float = 0.0

    @property
    def aggregate(self) -> AggregateMetrics:
        return aggregate([r.metrics for c in self.cases for r in c.results])


async def default_factory(settings: Settings) -> Container:
    return await build_container(settings, store=await Store.open(":memory:"))


def fixture_paper() -> Paper:
    """ "会话里已有试卷"的夹具：一份 5 道填空题的试卷（内容无关紧要，只用于让路由知道已有题）。"""
    items = [
        Item(id=f"fx{i}", kind=ItemKind.fill, stem=f"第{i}题：$1+1=$____", answer="2") for i in range(1, 6)
    ]
    return Paper(
        id="paper_fixture",
        title="夹具试卷",
        rev=1,
        sections=[Section(id="s1", title="一、填空题", items=items)],
    )


async def _ensure_paper(c: Container, session_id: str) -> None:
    if await c.store.papers.get_current(session_id) is None:
        paper = fixture_paper()
        await c.store.papers.save(
            session_id, paper, Revision(paper_id=paper.id, rev=1, author="agent", ts=time.time())
        )


async def _drive(c: Container, run_id: str, answer: str | None, timeout_s: float) -> Run:
    """等待运行结束；若暂停等待澄清，则按用例给出的回答（缺省"你来定"）继续（每个检查点只回答一次）。"""
    deadline = time.monotonic() + timeout_s
    answered: set[str] = set()
    while True:
        cur = await c.store.runs.get(run_id)
        if cur.status.terminal:
            return cur
        if cur.status == RunStatus.awaiting_user:
            cp = (cur.checkpoint or {}).get("id", "")
            if cp not in answered:
                answered.add(cp)
                await c.manager.resume(run_id, {"text": answer or "你来定"})
            else:
                await asyncio.sleep(0.02)  # 已回答，等待运行恢复
            continue
        if time.monotonic() > deadline:
            raise TimeoutError
        try:
            await c.manager.wait(run_id, timeout=0.05)
        except TimeoutError:
            pass


async def _aux(c: Container, run: Run) -> dict[str, Any]:
    """评分需要的辅助数据：映射到的知识点文本、单元 ID → 标题序号。"""
    aux: dict[str, Any] = {"kp_text": {}, "unit_ordinal": {}}
    for key in ("understand", "understand:clarified"):
        u = run.state.get(key)
        brief = (u or {}).get("brief") or {}
        scope = brief.get("scope") or {}
        ids = ((scope.get("kp_ids") or {}).get("value")) or []
        aux["kp_text"].update(await c.kb.kp_texts(ids))
        g, s = (scope.get("grade") or {}).get("value"), (scope.get("semester") or {}).get("value")
        if g and s:
            for unit in await c.kb.units(c.kb.book_id(g, s)):
                aux["unit_ordinal"][unit.id] = unit.ordinal
    return aux


async def run_case(c: Container, case: Case, run_timeout_s: float = 300.0) -> CaseResult:
    res = CaseResult(case_id=case.id, tags=case.tags)
    try:
        session = await c.manager.create_session(case.id)
        for turn in case.turns:
            if turn.has_paper:
                await _ensure_paper(c, session.id)
            run = await c.manager.start_turn(
                session.id, turn.user, [], pipeline=turn.pipeline, tags=[f"eval:{case.id}", *case.tags]
            )
            done = await _drive(c, run.id, turn.clarify_answer, run_timeout_s)
            events = await c.store.events.list(run.id)
            res.results.append(
                RunResult(
                    run=done,
                    events=events,
                    metrics=compute_run_metrics(events),
                    reply=reply_of(events),
                    turn_expect=turn.expect,
                    aux=await _aux(c, done) if turn.expect else {},
                )
            )
        res.outcomes = evaluate(case.expect, res.results)
    except VerichalkError as e:
        res.error = f"{e.code}: {e}"
    except TimeoutError:
        res.error = "用例超时"
    return res


async def run_suite(
    settings: Settings,
    cases: list[Case],
    *,
    suite: str,
    split: str,
    concurrency: int = 4,
    factory: ContainerFactory = default_factory,
) -> SuiteResult:
    c = await factory(settings)
    # 与真实服务启动一致：先预热（受特性开关 `warmup` 控制，回放 / 评测模式下自动禁用）。
    # 否则首批并发请求会同时新建连接，把一次性的握手开销算进延迟指标里。
    c.warmer.trigger()
    await c.warmer.wait()
    out = SuiteResult(suite=suite, split=split, settings=settings, started_at=time.time())
    sem = asyncio.Semaphore(concurrency)

    async def one(case: Case) -> CaseResult:
        async with sem:
            return await run_case(c, case)

    try:
        out.cases = list(await asyncio.gather(*(one(x) for x in cases)))
    finally:
        out.duration_s = time.time() - out.started_at
        await c.close()
    return out

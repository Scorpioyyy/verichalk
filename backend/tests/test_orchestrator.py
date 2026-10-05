from __future__ import annotations

import asyncio

import pytest

from fakes import FakeTransport, content_chunks, usage
from verichalk import trace
from verichalk.core.config import LLMMode, Settings
from verichalk.core.errors import Conflict, LLMTimeout, NotFound
from verichalk.domain.events import (
    CheckpointRequested,
    MessageDelta,
    MessageDone,
    RunFinished,
    RunPaused,
    RunStarted,
    SpanFinished,
)
from verichalk.domain.run import RunStatus
from verichalk.llm import build_gateway
from verichalk.metrics.completeness import check_trace_completeness
from verichalk.orchestrator import PipelineResult, RunManager, TurnInput
from verichalk.orchestrator.pipelines import diagnostic_pipeline
from verichalk.stages import RunContext
from verichalk.trace import EventBus


def make_manager(store, pipelines, kb=None, llm=None) -> RunManager:
    return RunManager(Settings(), store, EventBus(), kb, llm, pipelines)  # type: ignore[arg-type]


async def events_of(store, run_id):
    return await store.events.list(run_id)


async def test_success_lifecycle_and_event_order(store):
    async def ok(ctx: RunContext, turn: TurnInput) -> PipelineResult:
        await trace.progress("working", 1, 1)
        return PipelineResult("msg_x", f"echo:{turn.text}")

    mgr = make_manager(store, {"ok": ok})
    ses = await mgr.create_session()
    run = await mgr.start_turn(ses.id, "你好", pipeline="ok")
    done = await mgr.wait(run.id, 5)
    assert done.status == RunStatus.succeeded and done.error is None and done.finished_at
    evs = await events_of(store, run.id)
    assert (
        isinstance(evs[0], RunStarted) and isinstance(evs[-1], RunFinished) and evs[-1].status == "succeeded"
    )
    assert check_trace_completeness(evs).ok
    msgs = await store.messages.list(ses.id)
    assert [(m.role.value, m.content) for m in msgs] == [("user", "你好"), ("assistant", "echo:你好")]
    assert (await store.sessions.get(ses.id)).title == "你好"
    assert mgr.active_run(ses.id) is None


async def test_typed_failure_is_recorded_with_user_message(store):
    async def boom(ctx, turn):
        raise LLMTimeout("upstream slow")

    mgr = make_manager(store, {"boom": boom})
    ses = await mgr.create_session()
    run = await mgr.start_turn(ses.id, "x", pipeline="boom")
    done = await mgr.wait(run.id, 5)
    assert done.error is not None
    assert done.status == RunStatus.failed and done.error.code == "llm_timeout" and done.error.user_message
    evs = await events_of(store, run.id)
    assert evs[-1].status == "failed" and evs[-1].error.code == "llm_timeout"
    assert check_trace_completeness(evs).ok


async def test_untyped_exception_becomes_internal_error_without_leak(store, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "abcd1234efgh5678")

    async def boom(ctx, turn):
        raise RuntimeError("bad abcd1234efgh5678")

    mgr = make_manager(store, {"boom": boom})
    ses = await mgr.create_session()
    run = await mgr.start_turn(ses.id, "x", pipeline="boom")
    done = await mgr.wait(run.id, 5)
    assert done.error is not None
    assert done.error.code == "internal_error" and "abcd1234efgh5678" not in done.error.message
    rows = await store.db.fetchall("SELECT json FROM events WHERE run_id=?", (run.id,))
    assert all("abcd1234efgh5678" not in r["json"] for r in rows)


async def test_cancel_closes_spans_and_finishes_cancelled(store):
    started = asyncio.Event()

    async def slow(ctx, turn):
        async with trace.stage_span("slow"):
            started.set()
            await asyncio.sleep(30)
        return PipelineResult()

    mgr = make_manager(store, {"slow": slow})
    ses = await mgr.create_session()
    run = await mgr.start_turn(ses.id, "x", pipeline="slow")
    await started.wait()
    assert await mgr.cancel(run.id) is True
    done = await store.runs.get(run.id)
    assert done.status == RunStatus.cancelled
    evs = await events_of(store, run.id)
    assert evs[-1].status == "cancelled" and check_trace_completeness(evs).ok
    assert {e.status.value for e in evs if isinstance(e, SpanFinished)} == {"cancelled"}
    assert mgr.active_run(ses.id) is None


async def test_one_active_run_per_session(store):
    gate = asyncio.Event()

    async def hold(ctx, turn):
        await gate.wait()
        return PipelineResult()

    mgr = make_manager(store, {"hold": hold})
    ses = await mgr.create_session()
    run = await mgr.start_turn(ses.id, "a", pipeline="hold")
    with pytest.raises(Conflict):
        await mgr.start_turn(ses.id, "b", pipeline="hold")
    gate.set()
    await mgr.wait(run.id, 5)
    run2 = await mgr.start_turn(ses.id, "c", pipeline="hold")  # 结束后可再次发起
    await mgr.wait(run2.id, 5)


async def test_unknown_session_and_pipeline(store):
    mgr = make_manager(store, {"p": lambda c, t: None})
    with pytest.raises(NotFound):
        await mgr.start_turn("ses_nope", "x", pipeline="p")
    ses = await mgr.create_session()
    with pytest.raises(NotFound):
        await mgr.start_turn(ses.id, "x", pipeline="nope")


async def test_checkpoint_pause_and_resume(store):
    async def asks(ctx: RunContext, turn):
        ans = await ctx.ask("clarify", "哪个年级？", [{"id": "g4", "label": "四年级"}])
        return PipelineResult("m", f"选择：{ans['choice']}")

    mgr = make_manager(store, {"asks": asks})
    ses = await mgr.create_session()
    run = await mgr.start_turn(ses.id, "出题", pipeline="asks")
    cur = await store.runs.get(run.id)
    for _ in range(100):  # 等待进入暂停
        cur = await store.runs.get(run.id)
        if cur.status == RunStatus.awaiting_user:
            break
        await asyncio.sleep(0.02)
    assert cur.status == RunStatus.awaiting_user and cur.checkpoint["kind"] == "clarify"
    evs = await events_of(store, run.id)
    assert any(isinstance(e, CheckpointRequested) for e in evs) and any(isinstance(e, RunPaused) for e in evs)
    await mgr.resume(run.id, {"choice": "g4"})
    done = await mgr.wait(run.id, 5)
    assert done.status == RunStatus.succeeded
    assert (await store.messages.list(ses.id))[-1].content == "选择：g4"
    with pytest.raises(Conflict):
        await mgr.resume(run.id, {})


async def test_recover_marks_leftover_runs_interrupted(store):
    mgr = make_manager(store, {})
    ses = await mgr.create_session()
    from verichalk.domain.run import Run

    await store.runs.create(
        Run(id="run_left", session_id=ses.id, pipeline="p", status=RunStatus.running, created_at=1.0)
    )
    assert await mgr.recover() == 1
    assert (await store.runs.get("run_left")).error.code == "interrupted"


async def test_stage_state_snapshot_and_reuse(store):
    from pydantic import BaseModel

    from verichalk.stages import Stage, run_stage

    class N(BaseModel):
        n: int

    calls = []

    class Double(Stage[N, N]):
        name, input_model, output_model = "double", N, N

        async def run(self, ctx, inp):
            calls.append(inp.n)
            return N(n=inp.n * 2)

    async def pipe(ctx: RunContext, turn):
        out = await run_stage(ctx, Double(), N(n=21))
        return PipelineResult(reply_text=str(out.n))

    mgr = make_manager(store, {"pipe": pipe})
    ses = await mgr.create_session()
    run = await mgr.start_turn(ses.id, "x", pipeline="pipe")
    done = await mgr.wait(run.id, 5)
    assert done.state["double"] == {"n": 42} and calls == [21]

    ctx = RunContext(
        run_id="r",
        session_id="s",
        settings=Settings(),
        llm=None,  # type: ignore[arg-type]
        kb=None,  # type: ignore[arg-type]
        store=store,
        state={"double": {"n": 42}},
        reuse={"double"},
    )
    assert (await run_stage(ctx, Double(), N(n=21))).n == 42 and calls == [21]  # 复用快照，未重新执行


async def test_diagnostic_pipeline_end_to_end(store, tmp_path, monkeypatch):
    """网关 → 知识层 → trace → 事件：整条链路用假传输层与真实 chalkbase（词法检索）走通。"""
    import warnings

    for k in ("DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    from verichalk.knowledge import KnowledgeService

    settings = Settings(llm_mode=LLMMode.live, cassette_dir=tmp_path)
    llm = build_gateway(
        settings,
        transport=FakeTransport(content_chunks("找到了小数加减法。", usage=usage(300, 12, cached=256))),
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from chalkbase import Curriculum

        kb = KnowledgeService(Curriculum())  # pyright: ignore[reportCallIssue]
        mgr = RunManager(settings, store, EventBus(), kb, llm, {"diagnostic": diagnostic_pipeline})
        ses = await mgr.create_session()
        run = await mgr.start_turn(ses.id, "小数加减法")
        done = await mgr.wait(run.id, 20)
    assert done.status == RunStatus.succeeded, done.error
    evs = await events_of(store, run.id)
    assert check_trace_completeness(evs).ok
    kinds = [type(e).__name__ for e in evs]
    for must in (
        "RunStarted",
        "Progress",
        "RetrievalResult",
        "LLMCall",
        "MessageDelta",
        "MessageDone",
        "UsageUpdate",
        "RunFinished",
    ):
        assert must in kinds, must
    deltas = "".join(e.text for e in evs if isinstance(e, MessageDelta))
    assert deltas == next(e.text for e in evs if isinstance(e, MessageDone)) == "找到了小数加减法。"
    assert done.state["diagnostic"]["kp_names"]

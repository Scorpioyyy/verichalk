from __future__ import annotations

import asyncio

import pytest

from verichalk import trace
from verichalk.core.errors import LLMTimeout
from verichalk.domain.events import SpanFinished, SpanKind, SpanStarted
from verichalk.domain.paper import Paper, Revision
from verichalk.domain.run import Run, RunStatus
from verichalk.metrics.completeness import check_trace_completeness
from verichalk.trace import use_tracer


async def _events(store, run_id="run_test"):
    return await store.events.list(run_id)


async def test_span_tree_and_seq(traced):
    tracer, store, _ = traced
    async with use_tracer(tracer):
        async with trace.span(SpanKind.stage, "outer"):
            await trace.progress("working", 1, 2)
            async with trace.span(SpanKind.tool, "inner", q="x") as sp:
                sp.set(result=3)
    evs = await _events(store)
    assert [e.seq for e in evs] == list(range(1, len(evs) + 1))
    started = {e.span_id: e for e in evs if isinstance(e, SpanStarted)}
    finished = [e for e in evs if isinstance(e, SpanFinished)]
    inner = next(e for e in finished if e.name == "inner")
    assert inner.attrs == {"q": "x", "result": 3}
    assert started[inner.span_id].parent_id == next(e.span_id for e in finished if e.name == "outer")
    assert check_trace_completeness(evs).ok


async def test_exception_closes_span_with_error(traced):
    tracer, store, _ = traced
    with pytest.raises(LLMTimeout):
        async with use_tracer(tracer):
            async with trace.span(SpanKind.llm, "call"):
                raise LLMTimeout("slow")
    evs = await _events(store)
    fin = next(e for e in evs if isinstance(e, SpanFinished))
    assert fin.status.value == "error" and fin.error and fin.error.code == "llm_timeout"
    assert check_trace_completeness(evs).ok


async def test_cancellation_closes_all_spans(traced):
    tracer, store, _ = traced

    async def work():
        async with use_tracer(tracer):
            async with trace.span(SpanKind.stage, "a"):
                async with trace.span(SpanKind.llm, "b"):
                    await asyncio.sleep(10)

    t = asyncio.create_task(work())
    await asyncio.sleep(0.05)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    await asyncio.sleep(0.05)
    evs = await _events(store)
    assert {e.status.value for e in evs if isinstance(e, SpanFinished)} == {"cancelled"}
    assert check_trace_completeness(evs).ok


async def test_parallel_tasks_inherit_parent_span(traced):
    tracer, store, _ = traced

    async def child(i):
        async with trace.span(SpanKind.stage, f"c{i}"):
            await asyncio.sleep(0.01)

    async with use_tracer(tracer):
        async with trace.span(SpanKind.stage, "parent"):
            await asyncio.gather(*(asyncio.create_task(child(i)) for i in range(5)))
    evs = await _events(store)
    parent_id = next(e.span_id for e in evs if isinstance(e, SpanStarted) and e.name == "parent")
    kids = [e for e in evs if isinstance(e, SpanStarted) and e.name.startswith("c")]
    assert len(kids) == 5 and all(k.parent_id == parent_id for k in kids)
    assert check_trace_completeness(evs).ok


async def test_no_tracer_is_noop():
    async with trace.span(SpanKind.stage, "x") as sp:
        sp.set(a=1)
    await trace.progress("nothing")


async def test_bus_no_lost_wakeup(traced):
    tracer, _store, bus = traced
    ev = bus.current("run_test")
    async with use_tracer(tracer):
        await trace.progress("p")  # 通知发生在 wait 之前
    assert await bus.wait(ev, 0.5) is True


async def test_run_repo_and_interrupted(store):
    ses = await store.sessions.create("t")
    run = Run(id="run_a", session_id=ses.id, pipeline="diag", status=RunStatus.running, created_at=1.0)
    await store.runs.create(run)
    run2 = Run(id="run_b", session_id=ses.id, pipeline="diag", status=RunStatus.succeeded, created_at=2.0)
    await store.runs.create(run2)
    assert await store.runs.mark_interrupted() == 1
    got = await store.runs.get("run_a")
    assert got.status == RunStatus.failed and got.error and got.error.code == "interrupted"
    assert (await store.runs.get("run_b")).status == RunStatus.succeeded


async def test_paper_save_optimistic_concurrency(store):
    from verichalk.core.errors import Conflict

    ses = await store.sessions.create()
    p = Paper(id="p", rev=1)
    await store.papers.save(ses.id, p, Revision(paper_id="p", rev=1, author="agent", ts=1.0))
    with pytest.raises(Conflict):
        await store.papers.save(
            ses.id, Paper(id="p", rev=1), Revision(paper_id="p", rev=1, author="user", ts=2.0)
        )
    await store.papers.save(
        ses.id, Paper(id="p", rev=2), Revision(paper_id="p", rev=2, author="user", ts=3.0)
    )
    assert (await store.papers.get_current(ses.id)).rev == 2
    assert (await store.papers.get_revision(ses.id, 1)).rev == 1
    assert [r.rev for r in await store.papers.list_revisions(ses.id)] == [1, 2]


async def test_events_never_contain_secrets(traced, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "abcd1234efgh5678")
    tracer, store, _ = traced
    async with use_tracer(tracer):
        await trace.progress("leak abcd1234efgh5678")
    rows = await store.db.fetchall("SELECT json FROM events")
    assert all("abcd1234efgh5678" not in r["json"] for r in rows)


async def test_deferred_commit_survives_failed_op_and_close(tmp_path):
    """事件追加延迟提交：随后一次失败的普通写入（回滚）不能连带丢掉已追加的事件；关闭后数据在磁盘上。"""
    from verichalk.core.errors import Conflict
    from verichalk.store import Store

    path = tmp_path / "t.db"
    s = await Store.open(path)
    ses = await s.sessions.create()
    for i in range(1, 6):
        await s.events.append(
            "run_x", i, float(i), "progress", None, None, "user", '{"type":"progress","label":"x"}'
        )
    with pytest.raises(Conflict):  # 版本冲突 → 事务回滚
        await s.papers.save(
            ses.id, Paper(id="p", rev=5), Revision(paper_id="p", rev=5, author="user", ts=1.0)
        )
    assert await s.events.count("run_x") == 5
    await s.close()
    s2 = await Store.open(path)
    assert await s2.events.count("run_x") == 5
    await s2.close()

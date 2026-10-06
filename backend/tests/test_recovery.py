"""F5 恢复能力（metrics.md，闸门 = 100%）：服务重启后，会话、消息、试卷（含修订历史）与事件日志都在；
被重启打断的运行有明确的"已中断"结果，事件流可以从任意位置续传，不会挂住。

用真实的磁盘数据库（不是内存库）：先"运行"一个服务实例，关闭，再在同一数据目录上"重启"一个新的实例。
"""

from __future__ import annotations

import time

import httpx

from fakes import FakeTransport, content_chunks, usage
from test_api import parse_sse
from test_paper_service import base_paper
from verichalk.api import create_app
from verichalk.core.config import LLMMode, Settings
from verichalk.domain.paper import Revision
from verichalk.domain.run import Run, RunStatus
from verichalk.llm import build_gateway
from verichalk.orchestrator import Container, RunManager, Warmer
from verichalk.orchestrator.pipelines import diagnostic_pipeline
from verichalk.store import Store
from verichalk.trace import EventBus


async def boot(settings: Settings, kb):
    """启动一个服务实例（打开磁盘库 → 装配 → 回收遗留运行），返回 (container, http 客户端)。"""
    store = await Store.open(settings.data_path / "verichalk.db")
    llm = build_gateway(
        settings, transport=FakeTransport(content_chunks("好的。", usage=usage(300, 5, cached=256)))
    )
    bus = EventBus()
    mgr = RunManager(settings, store, bus, kb, llm, {"diagnostic": diagnostic_pipeline})
    await mgr.recover()
    c = Container(settings, store, bus, kb, llm, mgr, Warmer(settings, llm))
    app = create_app(settings, container=c)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    return c, client


async def test_restart_keeps_everything_and_interrupts_leftover_runs(tmp_path, kb_service):
    settings = Settings(llm_mode=LLMMode.live, cassette_dir=tmp_path / "c", data_dir=tmp_path / "data")

    # ---- 第一个实例：一轮对话 + 一份带两个修订的试卷 + 一个"正在运行"的运行 ----
    c1, cl1 = await boot(settings, kb_service)
    ses = (await cl1.post("/api/sessions")).json()["session"]
    run_id = (await cl1.post(f"/api/sessions/{ses['id']}/turns", data={"text": "小数加减法"})).json()[
        "run_id"
    ]
    await c1.manager.wait(run_id, 20)
    before_events = parse_sse((await cl1.get(f"/api/runs/{run_id}/events")).text)
    assert before_events[-1]["event"] == "run.finished"

    paper = base_paper()
    await c1.store.papers.save(
        ses["id"], paper, Revision(paper_id=paper.id, rev=1, author="agent", ts=time.time())
    )
    paper2 = paper.model_copy(update={"title": "改过标题的试卷", "rev": 2})
    await c1.store.papers.save(
        ses["id"], paper2, Revision(paper_id=paper.id, rev=2, author="user", ts=time.time())
    )
    await c1.store.runs.create(
        Run(
            id="run_in_flight",
            session_id=ses["id"],
            pipeline="diagnostic",
            status=RunStatus.running,
            created_at=time.time(),
        )
    )
    msgs_before = (await cl1.get(f"/api/sessions/{ses['id']}")).json()["messages"]
    await cl1.aclose()
    await c1.store.close()  # 服务停止（不等 in_flight 的运行）

    # ---- 第二个实例：同一数据目录 ----
    c2, cl2 = await boot(settings, kb_service)
    state = (await cl2.get(f"/api/sessions/{ses['id']}")).json()
    assert state["messages"] == msgs_before, "会话与消息不丢"
    assert state["paper"]["title"] == "改过标题的试卷" and state["paper"]["rev"] == 2, "试卷（最新修订）不丢"
    hist = (await cl2.get(f"/api/sessions/{ses['id']}/paper/history")).json()
    assert [r["rev"] for r in hist["revisions"]] == [1, 2], "修订历史不丢"
    assert state["active_run_id"] is None

    # 事件日志逐条保留；从中间续传不丢不重
    after = parse_sse((await cl2.get(f"/api/runs/{run_id}/events")).text)
    assert [e["id"] for e in after] == [e["id"] for e in before_events]
    mid = after[len(after) // 2]["id"]
    resumed = parse_sse(
        (await cl2.get(f"/api/runs/{run_id}/events", headers={"Last-Event-ID": str(mid)})).text
    )
    assert [e["id"] for e in resumed] == [e["id"] for e in after if e["id"] > mid]

    # 被打断的运行：状态是失败（已中断），事件流不会挂住
    left = (await cl2.get("/api/runs/run_in_flight")).json()
    assert left["status"] == "failed" and left["error"]["code"] == "interrupted"
    tail = await cl2.get("/api/runs/run_in_flight/events")
    assert tail.status_code == 200  # 直接结束的流（没有新事件），而不是一直等待

    # 重启后还能继续用：同一会话再来一轮
    again = await cl2.post(f"/api/sessions/{ses['id']}/turns", data={"text": "再来几道"})
    assert again.status_code == 202
    await c2.manager.wait(again.json()["run_id"], 20)
    assert len((await cl2.get(f"/api/sessions/{ses['id']}")).json()["messages"]) > len(msgs_before)
    await cl2.aclose()
    await c2.store.close()

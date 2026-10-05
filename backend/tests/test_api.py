from __future__ import annotations

import asyncio
import io
import json

import httpx
import pytest_asyncio
from PIL import Image

from fakes import FakeTransport, content_chunks, usage
from verichalk.api import create_app
from verichalk.core.config import LLMMode, Settings
from verichalk.llm import build_gateway
from verichalk.orchestrator import Container, PipelineResult, RunManager
from verichalk.trace import EventBus


def png_bytes(size=(64, 48)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", size, "white").save(b, "PNG")
    return b.getvalue()


@pytest_asyncio.fixture
async def env(store, kb_service, tmp_path):
    settings = Settings(llm_mode=LLMMode.live, cassette_dir=tmp_path, data_dir=tmp_path / "data")
    holder: dict = {}

    def make(pipelines=None, transport=None) -> Container:
        llm = build_gateway(
            settings,
            transport=transport or FakeTransport(content_chunks("好的。", usage=usage(300, 5, cached=256))),
        )
        bus = EventBus()
        mgr = RunManager(settings, store, bus, kb_service, llm, pipelines)
        c = Container(settings, store, bus, kb_service, llm, mgr)
        holder["c"] = c
        app = create_app(settings, container=c)
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        holder["client"] = client
        return c

    yield make, holder
    if "client" in holder:
        await holder["client"].aclose()


def parse_sse(text: str) -> list[dict]:
    out = []
    for block in text.strip().split("\n\n"):
        if block.startswith(":") or not block.strip():
            continue
        item = {}
        for line in block.split("\n"):
            k, _, v = line.partition(": ")
            item[k] = v
        out.append({"id": int(item["id"]), "event": item["event"], "data": json.loads(item["data"])})
    return out


async def test_health_and_error_format(env):
    make, h = env
    make()
    r = await h["client"].get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert (
        body["status"] == "ok"
        and body["chalkbase_version"]
        and body["data_version"]
        and body["models"]["fast"]
    )
    assert "aliyuncs" not in r.text
    r = await h["client"].get("/api/sessions/ses_nope")
    assert (
        r.status_code == 404
        and r.json()["error"]["code"] == "not_found"
        and r.json()["error"]["user_message"]
    )


async def test_turn_flow_sse_resume_and_visibility(env):
    make, h = env
    c = make()
    cl = h["client"]
    ses = (await cl.post("/api/sessions")).json()["session"]
    r = await cl.post(f"/api/sessions/{ses['id']}/turns", data={"text": "小数加减法"})
    assert r.status_code == 202
    run_id = r.json()["run_id"]
    await c.manager.wait(run_id, 20)

    full = parse_sse((await cl.get(f"/api/runs/{run_id}/events")).text)
    types = [e["event"] for e in full]
    assert types[0] == "run.started" and types[-1] == "run.finished"
    assert "llm.call" not in types and "span.started" not in types  # 用户流只含 user 可见事件
    assert "progress" in types and "message.delta" in types and "message.done" in types
    ids = [e["id"] for e in full]
    assert ids == sorted(ids)

    mid = full[len(full) // 2]["id"]
    resumed = parse_sse(
        (await cl.get(f"/api/runs/{run_id}/events", headers={"Last-Event-ID": str(mid)})).text
    )
    assert [e["id"] for e in resumed] == [i for i in ids if i > mid]  # 续传：不丢不重

    state = (await cl.get(f"/api/sessions/{ses['id']}")).json()
    assert [m["role"] for m in state["messages"]] == ["user", "assistant"] and state["active_run_id"] is None
    assert (await cl.get(f"/api/runs/{run_id}")).json()["status"] == "succeeded"


async def test_empty_turn_and_concurrency_conflict(env):
    make, h = env
    gate = asyncio.Event()

    async def hold(ctx, turn):
        await gate.wait()
        return PipelineResult()

    c = make(pipelines={"diagnostic": hold})
    cl = h["client"]
    ses = (await cl.post("/api/sessions")).json()["session"]
    r = await cl.post(f"/api/sessions/{ses['id']}/turns", data={"text": "  "})
    assert r.status_code == 422 and r.json()["error"]["user_message"]
    r1 = await cl.post(f"/api/sessions/{ses['id']}/turns", data={"text": "a"})
    r2 = await cl.post(f"/api/sessions/{ses['id']}/turns", data={"text": "b"})
    assert r1.status_code == 202 and r2.status_code == 409
    gate.set()
    await c.manager.wait(r1.json()["run_id"], 5)


async def test_cancel_endpoint(env):
    make, h = env
    started = asyncio.Event()

    async def slow(ctx, turn):
        started.set()
        await asyncio.sleep(30)
        return PipelineResult()

    c = make(pipelines={"diagnostic": slow})
    cl = h["client"]
    ses = (await cl.post("/api/sessions")).json()["session"]
    run_id = (await cl.post(f"/api/sessions/{ses['id']}/turns", data={"text": "a"})).json()["run_id"]
    await started.wait()
    assert (await cl.post(f"/api/runs/{run_id}/cancel")).json() == {"cancelled": True}
    assert (await cl.get(f"/api/runs/{run_id}")).json()["status"] == "cancelled"
    evs = parse_sse((await cl.get(f"/api/runs/{run_id}/events")).text)
    assert evs[-1]["event"] == "run.finished" and evs[-1]["data"]["status"] == "cancelled"
    assert c.manager.active_run(ses["id"]) is None


async def test_checkpoint_endpoint(env):
    make, h = env

    async def asks(ctx, turn):
        ans = await ctx.ask("clarify", "哪个年级？", [{"id": "4", "label": "四年级"}])
        return PipelineResult(reply_text=f"年级={ans['grade']}")

    c = make(pipelines={"diagnostic": asks})
    cl = h["client"]
    ses = (await cl.post("/api/sessions")).json()["session"]
    run_id = (await cl.post(f"/api/sessions/{ses['id']}/turns", data={"text": "出题"})).json()["run_id"]
    v: dict = {}
    for _ in range(100):
        v = (await cl.get(f"/api/runs/{run_id}")).json()
        if v["status"] == "awaiting_user":
            break
        await asyncio.sleep(0.02)
    assert v["checkpoint"]["prompt"] == "哪个年级？"
    assert (await cl.post(f"/api/runs/{run_id}/checkpoint", json={"answer": {"grade": 4}})).status_code == 202
    await c.manager.wait(run_id, 5)
    assert (await cl.post(f"/api/runs/{run_id}/checkpoint", json={"answer": {}})).status_code == 409


async def test_image_upload_validation(env, tmp_path):
    make, h = env
    c = make()
    cl = h["client"]
    ses = (await cl.post("/api/sessions")).json()["session"]
    url = f"/api/sessions/{ses['id']}/turns"
    ok = await cl.post(
        url, data={"text": "照这个出题"}, files=[("images", ("p.png", png_bytes(), "image/png"))]
    )
    assert ok.status_code == 202
    await c.manager.wait(ok.json()["run_id"], 20)
    msgs = (await cl.get(f"/api/sessions/{ses['id']}")).json()["messages"]
    att = msgs[0]["attachments"][0]
    assert att["mime"] == "image/png" and len(att["sha256"]) == 64
    assert (c.settings.data_path / "uploads" / ses["id"]).exists()
    bad = await cl.post(url, data={"text": "x"}, files=[("images", ("a.png", b"not an image", "image/png"))])
    assert bad.status_code == 422 and "图片" in bad.json()["error"]["user_message"]
    many = await cl.post(
        url, data={"text": "x"}, files=[("images", (f"{i}.png", png_bytes(), "image/png")) for i in range(5)]
    )
    assert many.status_code == 422


async def test_debug_endpoints_auth_and_content(env):
    make, h = env
    c = make()
    cl = h["client"]
    ses = (await cl.post("/api/sessions")).json()["session"]
    run_id = (await cl.post(f"/api/sessions/{ses['id']}/turns", data={"text": "小数加减法"})).json()["run_id"]
    await c.manager.wait(run_id, 20)

    # 未配置令牌：testclient 视为本机，允许
    runs = (await cl.get("/api/debug/runs")).json()
    assert runs and runs[0]["metrics"]["status"] == "succeeded" and runs[0]["run"]["state"] == {}
    detail = (await cl.get(f"/api/debug/runs/{run_id}")).json()
    assert detail["metrics"]["n_llm_calls"] == 1 and detail["metrics"]["trace_ok"] is True
    assert detail["run"]["state"]["diagnostic"]["kp_names"]
    evs = (await cl.get(f"/api/debug/runs/{run_id}/events")).json()
    assert {"llm.call", "span.started", "retrieval.result"} <= {e["type"] for e in evs}
    agg = (await cl.get("/api/debug/metrics")).json()
    assert agg["n_runs"] == 1 and agg["success_rate"]["p"] == 1.0

    # 配置令牌后：必须携带
    from pydantic import SecretStr

    c.settings.debug_token = SecretStr("tok-123456789")
    assert (await cl.get("/api/debug/runs")).status_code == 403
    assert (await cl.get("/api/debug/runs", headers={"X-Debug-Token": "wrong"})).status_code == 403
    assert (await cl.get("/api/debug/runs", headers={"X-Debug-Token": "tok-123456789"})).status_code == 200
    # 用户端点不受影响
    assert (await cl.get("/api/health")).status_code == 200

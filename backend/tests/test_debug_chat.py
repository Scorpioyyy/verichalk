"""调试台的运行分析助手：摘要的组织、按需查询的工具、流式对话循环与接口。"""

from __future__ import annotations

import json
from typing import Any

from fakes import FakeTransport, content_chunks, usage
from test_api import env  # noqa: F401  # pytest 夹具
from verichalk.orchestrator.debug_chat import ChatTurn, stream_analysis
from verichalk.orchestrator.run_digest import (
    build_index,
    call_detail,
    find_events,
    render_digest,
    span_detail,
    stage_output,
)


def tool_call_chunks(name: str, arguments: str, call_id: str = "call_1") -> list[Any]:
    return [
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "id": call_id, "function": {"name": name, "arguments": arguments}}
                        ]
                    },
                    "finish_reason": None,
                }
            ]
        },
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": usage(500, 20)},
    ]


async def one_run(env_, transport: FakeTransport):
    make, h = env_
    c = make(transport=transport)
    cl = h["client"]
    ses = (await cl.post("/api/sessions")).json()["session"]
    run_id = (await cl.post(f"/api/sessions/{ses['id']}/turns", data={"text": "小数加减法出3道"})).json()[
        "run_id"
    ]
    await c.manager.wait(run_id, 20)
    return c, cl, run_id


async def test_digest_describes_the_run_and_ids_resolve_through_the_tools(env) -> None:  # noqa: F811
    c, _, run_id = await one_run(
        env, FakeTransport(content_chunks("好的。", usage=usage(300, 5, cached=256)))
    )
    run = await c.store.runs.get(run_id)
    events = await c.store.events.list(run_id)
    idx = build_index(run, events, "小数加减法出3道")
    d = render_digest(idx)
    for must in ("# 运行概况", run_id, "小数加减法出3道", "# 阶段与耗时", "# 模型调用清单", "diagnostic"):
        assert must in d, must
    assert len(d) < 30_000
    call = idx.calls[0]
    assert call.id in d, "摘要里的 call_id 可以直接拿去查"
    full = call_detail(idx, call.id)
    assert full["messages"] and full["response"] and full["usage"]["prompt_tokens"] == 300
    assert call_detail(idx, "1")["id"] == call.id, "也可以用清单序号"
    assert "error" in call_detail(idx, "llm_nope")
    sp = next(s for s in idx.spans.values() if s.kind == "stage")
    assert span_detail(idx, sp.id)["name"] == sp.name and "error" in span_detail(idx, "spn_nope")
    assert "keys" in stage_output(idx, None) and "error" in stage_output(idx, "nope")
    hits = find_events(idx, type_="llm.call")
    assert hits["matches"] and all(m["type"] == "llm.call" for m in hits["matches"])


async def test_digest_notes_replayed_runs_and_stays_within_budget(env) -> None:  # noqa: F811
    c, _, run_id = await one_run(env, FakeTransport(content_chunks("好的。", usage=usage(300, 5))))
    run = await c.store.runs.get(run_id)
    idx = build_index(run, await c.store.events.list(run_id), "x")
    for rec in idx.calls:
        rec.from_cassette = True
    assert "录制回放" in render_digest(idx)
    assert len(render_digest(idx, budget=6000)) < 12_000, "预算很小时调用清单与题目细节被裁，固定部分仍在"


async def test_stream_runs_tools_then_answers_and_hides_failures(env) -> None:  # noqa: F811
    transport = FakeTransport(
        content_chunks("好的。", usage=usage(300, 5)),  # 第 1 个脚本：被分析的那次运行本身
        tool_call_chunks("get_stage_output", "{}"),
        content_chunks("这次运行只有一次模型调用，没有异常。", usage=usage(900, 30)),
    )
    c, _, run_id = await one_run(env, transport)
    out = [
        ev
        async for ev in stream_analysis(
            c, run_id, "小数加减法出3道", [ChatTurn(role="user", content="有没有异常？")]
        )
    ]
    types = [e["type"] for e in out]
    assert types[-1] == "done" and "tool" in types and "delta" in types
    tool = next(e for e in out if e["type"] == "tool")
    assert tool["name"] == "get_stage_output" and "阶段快照" in tool["label"]
    assert "".join(e["text"] for e in out if e["type"] == "delta").endswith("没有异常。")
    # 工具结果被回填给了模型；系统提示里带着摘要
    final_body = transport.bodies[-1]
    roles = [m["role"] for m in final_body["messages"]]
    assert roles[0] == "system" and "tool" in roles and "assistant" in roles
    assert run_id in final_body["messages"][0]["content"]
    assert "运行分析助手" in final_body["messages"][0]["content"]


async def test_chat_endpoint_streams_sse_and_validates_input(env) -> None:  # noqa: F811
    transport = FakeTransport(
        content_chunks("好的。", usage=usage(300, 5)), content_chunks("一切正常。", usage=usage(900, 10))
    )
    _, cl, run_id = await one_run(env, transport)
    ok = await cl.post(
        f"/api/debug/runs/{run_id}/chat",
        json={
            "messages": [
                {"role": "user", "content": "先总结一下"},
                {"role": "assistant", "content": "好的"},
                {"role": "user", "content": "再看看成本"},
            ]
        },
    )
    assert ok.status_code == 200 and ok.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(line[6:]) for line in ok.text.splitlines() if line.startswith("data: ")]
    assert events[-1] == {"type": "done"} and any(e["type"] == "delta" for e in events)
    history = transport.bodies[-1]["messages"]
    assert [m["role"] for m in history[1:]] == ["user", "assistant", "user"], (
        "历史按顺序带上，最后一条是当前问题"
    )

    bad = await cl.post(
        f"/api/debug/runs/{run_id}/chat", json={"messages": [{"role": "assistant", "content": "x"}]}
    )
    assert bad.status_code == 422
    empty = await cl.post(
        f"/api/debug/runs/{run_id}/chat", json={"messages": [{"role": "user", "content": "  "}]}
    )
    assert empty.status_code == 422
    missing = await cl.post(
        "/api/debug/runs/run_nope/chat", json={"messages": [{"role": "user", "content": "x"}]}
    )
    assert missing.status_code == 404

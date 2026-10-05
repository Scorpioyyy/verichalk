from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from fakes import FakeTransport, content_chunks, usage
from verichalk.core.config import LLMMode, Profile, Settings
from verichalk.core.errors import (
    BudgetExceeded,
    ConfigError,
    LLMBadResponse,
    LLMRateLimited,
    LLMSchemaError,
    LLMTimeout,
    ReplayMiss,
)
from verichalk.domain.events import LLMCall, SpanFinished, SpanKind, UsageUpdate
from verichalk.domain.llm import ChatMessage, Role, Usage
from verichalk.llm import (
    Budget,
    LLMGateway,
    LLMRequest,
    ModelRegistry,
    PriceTable,
    bind_budget,
    complete_json,
    extract_json,
)
from verichalk.trace import MemorySink, Tracer, use_tracer


def make_gateway(transport, tmp_path, mode=LLMMode.live, **kw) -> LLMGateway:
    s = Settings(llm_mode=mode, cassette_dir=tmp_path, cassette_namespace="t", llm_max_retries=2, **kw)
    reg = ModelRegistry.load(s.config_dir, Profile.cn)
    return LLMGateway(s, reg, PriceTable.load(s.config_dir), transport, sleep=_nosleep)


async def _nosleep(_: float) -> None:
    return None


def req(text="你好", **kw) -> LLMRequest:
    return LLMRequest(role=Role.fast, messages=[ChatMessage(role="user", content=text)], purpose="t", **kw)


async def test_streaming_aggregation_usage_cache_and_cost(tmp_path):
    tr = FakeTransport(content_chunks("你好，世界", usage=usage(2800, 10, cached=2048)))
    gw = make_gateway(tr, tmp_path)
    deltas: list[str] = []

    async def on_delta(t: str) -> None:
        deltas.append(t)

    r = await gw.complete(req(on_delta=on_delta))
    assert r.text == "你好，世界" and "".join(deltas) == r.text and len(deltas) > 1
    assert r.usage == Usage(prompt_tokens=2800, completion_tokens=10, cached_tokens=2048, reasoning_tokens=0)
    assert r.ttft_ms is not None and r.ttfb_ms is not None and r.total_ms >= r.ttft_ms
    # 成本：未命中 752 × 0.8 + 命中 2048 × 0.16 + 输出 10 × 2.7（元 / 百万 token）
    assert r.cost == pytest.approx((752 * 0.8 + 2048 * 0.16 + 10 * 2.7) / 1e6)
    body = tr.bodies[0]
    assert body["stream"] is True and body["stream_options"] == {"include_usage": True}
    assert body["enable_thinking"] is False  # 思考模式总是显式传递


async def test_events_emitted_llm_span_call_and_usage(tmp_path):
    sink = MemorySink()
    gw = make_gateway(FakeTransport(content_chunks("ok", usage=usage())), tmp_path)
    async with use_tracer(Tracer("r", sink)):
        await gw.complete(req())
    types = [type(e).__name__ for e in sink.events]
    assert "LLMCall" in types and "UsageUpdate" not in types  # 无预算时不发 usage.update
    call = next(e for e in sink.events if isinstance(e, LLMCall))
    assert call.record.model == "qwen3.8-flash" and call.record.usage.prompt_tokens == 100
    fin = next(e for e in sink.events if isinstance(e, SpanFinished) and e.kind == SpanKind.llm)
    assert fin.attrs["usage"]["prompt_tokens"] == 100


async def test_retry_on_rate_limit_before_content(tmp_path):
    tr = FakeTransport([LLMRateLimited("429")], [LLMTimeout("t")], content_chunks("好", usage=usage()))
    r = await make_gateway(tr, tmp_path).complete(req())
    assert r.text == "好" and r.retries == 2 and len(tr.bodies) == 3


async def test_no_retry_for_non_retryable_and_retry_exhausted(tmp_path):
    with pytest.raises(LLMBadResponse):
        await make_gateway(FakeTransport([LLMBadResponse("400")]), tmp_path).complete(req())
    with pytest.raises(LLMRateLimited):
        await make_gateway(FakeTransport([LLMRateLimited("429")]), tmp_path).complete(req())


async def test_no_retry_after_content_was_forwarded(tmp_path):
    chunks = [*content_chunks("半截")[:1], LLMTimeout("mid-stream")]
    tr = FakeTransport(chunks, content_chunks("不应被调用"))
    with pytest.raises(LLMTimeout):
        await make_gateway(tr, tmp_path).complete(req(on_delta=_sink_delta))
    assert len(tr.bodies) == 1


async def _sink_delta(_: str) -> None:
    return None


async def test_tool_calls_accumulated(tmp_path):
    chunks = [
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "id": "c1", "function": {"name": "kp_search", "arguments": '{"q":'}}
                        ]
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"小数"}'}}]},
                    "finish_reason": "tool_calls",
                }
            ]
        },
        {"choices": [], "usage": usage()},
    ]
    r = await make_gateway(FakeTransport(chunks), tmp_path).complete(req(tools=[{"type": "function"}]))
    assert r.tool_calls[0].name == "kp_search" and json.loads(r.tool_calls[0].arguments) == {"q": "小数"}
    assert r.finish_reason == "tool_calls"


async def test_record_then_replay_roundtrip_and_miss(tmp_path):
    live = make_gateway(
        FakeTransport(content_chunks("答案是42", usage=usage(50, 5))), tmp_path, LLMMode.record
    )
    r1 = await live.complete(req("问题"))
    rep = make_gateway(None, tmp_path, LLMMode.replay)
    got: list[str] = []

    async def cb(t: str) -> None:
        got.append(t)

    r2 = await rep.complete(req("问题", on_delta=cb))
    assert r2.text == r1.text and r2.usage == r1.usage and r2.from_cassette and got == ["答案是42"]
    with pytest.raises(ReplayMiss):
        await rep.complete(req("另一个问题"))
    # 键对消息与参数敏感
    with pytest.raises(ReplayMiss):
        await rep.complete(req("问题", temperature=0.9))


async def test_cassette_has_no_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "abcd1234efgh5678")
    gw = make_gateway(
        FakeTransport(content_chunks("key=abcd1234efgh5678", usage=usage())), tmp_path, LLMMode.record
    )
    await gw.complete(req())
    files = list((tmp_path / "t").glob("*.json"))
    assert files and all("abcd1234efgh5678" not in f.read_text(encoding="utf-8") for f in files)


async def test_budget_enforced_and_usage_update(tmp_path):
    gw = make_gateway(FakeTransport(content_chunks("x", usage=usage(900, 200))), tmp_path)
    sink = MemorySink()
    b = Budget(max_tokens=1000, max_cost=100)
    tok = bind_budget(b)
    try:
        async with use_tracer(Tracer("r", sink)):
            await gw.complete(req())
            assert any(isinstance(e, UsageUpdate) for e in sink.events)
            with pytest.raises(BudgetExceeded):  # 第一次已用 1100 > 1000，第二次调用前即被拒
                await gw.complete(req())
    finally:
        from verichalk.llm import unbind_budget

        unbind_budget(tok)


async def test_json_mode_requires_json_word(tmp_path):
    gw = make_gateway(FakeTransport(content_chunks("{}", usage=usage())), tmp_path)
    with pytest.raises(ValueError):
        await gw.complete(req("请回答", json_mode=True))
    r = await gw.complete(req("请输出 JSON", json_mode=True))
    assert r.text == "{}"


class Out(BaseModel):
    n: int
    tag: str


async def test_structured_output_repair_loop(tmp_path):
    tr = FakeTransport(
        content_chunks('{"n": "abc"}', usage=usage()),
        content_chunks('```json\n{"n": 3, "tag": "ok"}\n```', usage=usage()),
    )
    sink = MemorySink()
    async with use_tracer(Tracer("r", sink)):
        parsed, _ = await complete_json(make_gateway(tr, tmp_path), req("输出 JSON"), Out)
    assert parsed == Out(n=3, tag="ok")
    # 第二次请求带上了错误反馈
    assert "不符合要求" in json.dumps(tr.bodies[1]["messages"], ensure_ascii=False)
    fin = next(e for e in sink.events if isinstance(e, SpanFinished) and e.name == "structured_output")
    assert fin.attrs["schema_retries"] == 1


async def test_structured_output_gives_up(tmp_path):
    tr = FakeTransport(content_chunks("不是json", usage=usage()))
    with pytest.raises(LLMSchemaError):
        await complete_json(make_gateway(tr, tmp_path), req("输出 JSON"), Out, max_repairs=1)
    assert len(tr.bodies) == 2


def test_extract_json_tolerates_noise():
    assert extract_json('前言 {"a": 1} 后记') == {"a": 1}
    assert extract_json("```json\n[1,2]\n```") == [1, 2]


def test_registry_env_override_and_missing_role(monkeypatch):
    s = Settings()
    monkeypatch.setenv("VERICHALK_MODEL_FAST", "deepseek-v4.1-flash")
    reg = ModelRegistry.load(s.config_dir, Profile.intl)
    assert reg.role(Role.fast).model == "deepseek-v4.1-flash"
    assert reg.override(Role.smart, model="x").role(Role.smart).model == "x"
    with pytest.raises(ConfigError):
        reg.role("nope")


def test_price_unknown_model_is_none_not_guessed():
    pt = PriceTable.load(Settings().config_dir)
    assert pt.cost("some-new-model", Usage(prompt_tokens=10)) is None

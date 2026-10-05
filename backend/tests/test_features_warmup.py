from __future__ import annotations

import asyncio

import pytest

from fakes import FakeTransport, content_chunks, usage
from verichalk.core.config import LLMMode, Settings
from verichalk.core.errors import ConfigError, LLMTimeout
from verichalk.core.features import REGISTRY, FeatureFlags
from verichalk.llm import build_gateway
from verichalk.orchestrator import Warmer


def test_flags_parse_validate_and_describe():
    assert FeatureFlags.parse("").describe() == "全部开启"
    f = FeatureFlags.parse("warmup")
    assert not f.enabled("warmup") and f.describe() == "关闭：warmup"
    with pytest.raises(ConfigError):
        FeatureFlags.parse("no_such_flag")
    with pytest.raises(ConfigError):
        FeatureFlags().enabled("no_such_flag")


def test_settings_features_from_env(monkeypatch):
    monkeypatch.setenv("VERICHALK_OFF", "warmup")
    assert not Settings().features.enabled("warmup")
    monkeypatch.delenv("VERICHALK_OFF")
    assert Settings().features.enabled("warmup")


def test_every_registered_flag_is_documented():
    assert REGISTRY and all(f.description for f in REGISTRY.values())


def make(tmp_path, transport, **kw) -> tuple[Warmer, FakeTransport]:
    s = Settings(llm_mode=LLMMode.live, cassette_dir=tmp_path, **kw)
    gw = build_gateway(s, transport=transport)
    return Warmer(s, gw), transport


async def test_warmup_sends_minimal_prefix_requests_and_debounces(tmp_path):
    tr = FakeTransport(content_chunks("", usage=usage(300, 1)))
    w, _ = make(tmp_path, tr)
    assert w.trigger().state == "started"
    assert w.trigger().state == "running"
    await w.wait()
    assert tr.bodies, "应至少对一个已发布提示词的静态前缀发了请求"
    b = tr.bodies[0]
    assert b["max_tokens"] == 1 and b["enable_thinking"] is False
    assert [m["role"] for m in b["messages"]] == ["system", "user"] and b["messages"][1]["content"] == "ping"
    n = len(tr.bodies)
    assert w.trigger().state == "fresh" and len(tr.bodies) == n  # 有效期内不再发请求
    assert w.trigger(force=True).state == "started"
    await w.wait()
    assert len(tr.bodies) == 2 * n


async def test_warmup_disabled_by_flag_and_by_mode(tmp_path):
    tr = FakeTransport(content_chunks("", usage=usage()))
    w, _ = make(tmp_path, tr, off="warmup")
    assert w.trigger().state == "disabled" and not tr.bodies
    s = Settings(llm_mode=LLMMode.replay, cassette_dir=tmp_path)
    w2 = Warmer(s, build_gateway(s))
    assert w2.trigger().state == "disabled"  # 回放 / 评测不预热


async def test_warmup_failures_never_propagate(tmp_path):
    tr = FakeTransport([LLMTimeout("t")])
    s = Settings(llm_mode=LLMMode.live, cassette_dir=tmp_path, llm_max_retries=0)
    w = Warmer(s, build_gateway(s, transport=tr))
    w.trigger()
    await asyncio.wait_for(w.wait(), 5)
    st = w.status()
    assert st.errors >= 1 and st.state == "fresh"  # 失败被计数但不抛出；服务可继续


async def test_warmup_endpoint(env_with_warmup):
    c, client = env_with_warmup
    r = await client.post("/api/warmup")
    assert r.status_code == 200 and r.json()["state"] == "started"
    await c.warmer.wait()
    assert (await client.post("/api/warmup")).json()["state"] == "fresh"


@pytest.fixture
async def env_with_warmup(store, kb_service, tmp_path):
    import httpx

    from verichalk.api import create_app
    from verichalk.orchestrator import Container, RunManager
    from verichalk.trace import EventBus

    s = Settings(llm_mode=LLMMode.live, cassette_dir=tmp_path, data_dir=tmp_path / "d")
    gw = build_gateway(s, transport=FakeTransport(content_chunks("", usage=usage())))
    bus = EventBus()
    c = Container(s, store, bus, kb_service, gw, RunManager(s, store, bus, kb_service, gw), Warmer(s, gw))
    app = create_app(s, container=c)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    yield c, client
    await client.aclose()


async def test_ablation_report_renders_variants(tmp_path):
    """消融对照表：全开 + 每个关闭变体各一行，并写明判定规则。"""
    from verichalk.eval import Case, TurnSpec
    from verichalk.eval.ablation import render_ablation, run_ablation

    case = Case(
        id="c1", split="val", tags=["t"], turns=[TurnSpec(user="小数加减法")], expect={"status": "succeeded"}
    )

    async def factory(settings):
        from verichalk.orchestrator import build_container
        from verichalk.store import Store

        return await build_container(
            settings,
            store=await Store.open(":memory:"),
            llm=build_gateway(settings, transport=FakeTransport(content_chunks("好", usage=usage(10, 1)))),
        )

    res = await run_ablation(
        lambda off: Settings(llm_mode=LLMMode.live, cassette_dir=tmp_path, off=off),
        [case],
        suite="x",
        split="val",
        flags=["warmup"],
        factory=factory,
    )
    md = render_ablation(res)
    assert list(res) == ["all-on", "A/A 噪声参照", "off:warmup"]
    assert "| all-on |" in md and "| off:warmup |" in md and "判定规则" in md and "噪声底" in md
    assert res["off:warmup"].settings.features.describe() == "关闭：warmup"


def test_pipeline_presets_and_opt_in_flags() -> None:
    """classic 是默认链路（选择性开关全部关闭）；design 预设一键开启它们；`on` 单独开启，`off` 优先。"""
    from verichalk.core.features import DESIGN_FLAGS

    assert DESIGN_FLAGS == {
        "plan.design",
        "produce.design_prompt",
        "produce.difficulty_check",
        "plan.cross_unit",
    }
    classic = FeatureFlags.parse("")
    assert not any(classic.enabled(f) for f in DESIGN_FLAGS)
    assert classic.enabled("produce.blind_solve")  # 普通开关默认开启
    design = FeatureFlags.parse("", pipeline="design")
    assert all(design.enabled(f) for f in DESIGN_FLAGS)
    assert not FeatureFlags.parse("plan.cross_unit", pipeline="design").enabled(
        "plan.cross_unit"
    )  # 二分：关掉其一
    one = FeatureFlags.parse("", "plan.design")
    assert one.enabled("plan.design") and not one.enabled("produce.design_prompt")
    with pytest.raises(ConfigError):
        FeatureFlags.parse("", pipeline="nope")

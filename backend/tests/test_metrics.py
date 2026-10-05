"""指标计算对手工构造 trace 的黄金值测试（评测规格 M1 失败模式 #11）。"""

from __future__ import annotations

import pytest

from verichalk.domain.common import ErrorInfo
from verichalk.domain.events import (
    Event,
    LLMCall,
    Progress,
    RunFinished,
    RunStarted,
    SpanFinished,
    SpanKind,
    SpanStarted,
    SpanStatus,
)
from verichalk.domain.llm import LLMCallRecord, Usage
from verichalk.metrics import aggregate, compute_run_metrics, percentile, wilson


def rec(model, role, prompt, completion, cached, cost, ttft, total, retries=0, error=None, replay=False):
    return LLMCallRecord(
        id="l",
        role=role,
        model=model,
        profile="cn",
        usage=Usage(prompt_tokens=prompt, completion_tokens=completion, cached_tokens=cached),
        ttft_ms=ttft,
        total_ms=total,
        cost=cost,
        retries=retries,
        error=error,
        from_cassette=replay,
    )


def build_events() -> list[Event]:
    evs: list[Event] = [
        RunStarted(session_id="s", pipeline="p", ts=100.0),
        SpanStarted(kind=SpanKind.stage, name="understand", span_id="a", ts=100.1),
        Progress(label="x", ts=100.5),
        LLMCall(record=rec("m1", "fast", 1000, 100, 500, 0.002, 400, 900), ts=100.8),
        LLMCall(record=rec("m1", "fast", 1000, 50, 1000, 0.001, 200, 500, retries=1), ts=101.0),
        SpanFinished(
            kind=SpanKind.stage,
            name="understand",
            span_id="a",
            status=SpanStatus.ok,
            duration_ms=1500,
            ts=101.6,
        ),
        SpanStarted(kind=SpanKind.tool, name="kb.search", span_id="b", ts=101.7),
        SpanFinished(
            kind=SpanKind.tool,
            name="kb.search",
            span_id="b",
            status=SpanStatus.ok,
            duration_ms=200,
            attrs={"schema_retries": 1},
            ts=101.9,
        ),
        LLMCall(record=rec("m2", "smart", 2000, 300, 0, 0.01, 800, 3000), ts=104.9),
        RunFinished(status="succeeded", ts=105.0),
    ]
    for i, e in enumerate(evs, 1):
        e.seq, e.run_id = i, "r1"
    return evs


def test_run_metrics_golden():
    m = compute_run_metrics(build_events())
    assert m.status == "succeeded" and m.trace_ok
    assert m.e2e_ms == pytest.approx(5000) and m.first_feedback_ms == pytest.approx(500)
    assert m.usage.prompt_tokens == 4000 and m.usage.cached_tokens == 1500
    assert m.cache_hit_ratio == pytest.approx(1500 / 4000)  # 按 token 加权，不是各次比率的平均
    assert m.cost == pytest.approx(0.013) and m.n_llm_calls == 3 and m.llm_retries == 1
    m1 = next(x for x in m.models if x.model == "m1")
    assert (
        m1.calls == 2
        and m1.cache_hit_ratio == pytest.approx(1500 / 2000)
        and m1.ttft_p50_ms == pytest.approx(300)
    )
    assert m.n_tool_calls == 1 and m.schema_retries == 1
    st = next(s for s in m.stages if s.name == "understand")
    assert st.count == 1 and st.total_ms == 1500


def test_unknown_cost_makes_total_unknown_not_zero():
    evs = build_events()
    evs[3].record.cost = None  # type: ignore[attr-defined]
    assert compute_run_metrics(evs).cost is None


def test_error_calls_counted():
    evs = build_events()
    evs[4].record.error = ErrorInfo(code="llm_timeout")  # type: ignore[attr-defined]
    m = compute_run_metrics(evs)
    assert m.llm_errors == 1


def test_percentile_and_wilson_known_values():
    assert percentile([1, 2, 3, 4], 0.5) == 2.5
    assert percentile([5], 0.95) == 5.0 and percentile([], 0.5) is None
    lo, hi = wilson(8, 10)
    assert lo == pytest.approx(0.4902, abs=1e-3) and hi == pytest.approx(0.9433, abs=1e-3)
    assert wilson(0, 0) == (0.0, 1.0)
    lo, hi = wilson(10, 10)
    assert hi == 1.0 and lo == pytest.approx(0.7225, abs=1e-3)


def test_aggregate_success_rate_and_cache():
    ok, bad = compute_run_metrics(build_events()), compute_run_metrics(build_events())
    bad.status = "failed"
    agg = aggregate([ok, bad])
    assert agg.n_runs == 2 and agg.success_rate.k == 1 and agg.success_rate.p == 0.5
    assert agg.cache_hit_ratio == pytest.approx(1500 / 4000)
    assert agg.e2e_ms.p50 == pytest.approx(5000) and agg.trace_complete.p == 1.0

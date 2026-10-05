"""多次运行的聚合：分位数、成功率（Wilson 区间）、缓存命中、成本。评测报告与调试台"指标页"共用。"""

from __future__ import annotations

from pydantic import BaseModel

from .run_metrics import RunMetrics
from .stats import percentile, wilson


class Proportion(BaseModel):
    k: int
    n: int
    p: float | None
    lo: float
    hi: float


def proportion(k: int, n: int) -> Proportion:
    lo, hi = wilson(k, n)
    return Proportion(k=k, n=n, p=(k / n if n else None), lo=lo, hi=hi)


class Quantiles(BaseModel):
    n: int
    p50: float | None
    p95: float | None
    mean: float | None


def quantiles(values: list[float]) -> Quantiles:
    return Quantiles(
        n=len(values),
        p50=percentile(values, 0.5),
        p95=percentile(values, 0.95),
        mean=(sum(values) / len(values) if values else None),
    )


class AggregateMetrics(BaseModel):
    n_runs: int
    success_rate: Proportion  # F1
    trace_complete: Proportion  # F6
    e2e_ms: Quantiles  # D3
    first_feedback_ms: Quantiles  # D1
    cache_hit_ratio: float | None  # E2：按 token 加权
    cost_per_run: Quantiles  # E1
    llm_calls_per_run: Quantiles  # E3
    llm_error_rate: Proportion  # F2
    schema_retry_rate: Proportion  # E5（重试次数 / 调用数的近似）
    tokens_per_run: Quantiles


def aggregate(runs: list[RunMetrics]) -> AggregateMetrics:
    n = len(runs)
    ok = sum(1 for r in runs if r.status == "succeeded")
    prompt = sum(r.usage.prompt_tokens for r in runs)
    cached = sum(r.usage.cached_tokens for r in runs)
    calls = sum(r.n_llm_calls for r in runs)
    return AggregateMetrics(
        n_runs=n,
        success_rate=proportion(ok, n),
        trace_complete=proportion(sum(1 for r in runs if r.trace_ok), n),
        e2e_ms=quantiles([r.e2e_ms for r in runs if r.e2e_ms is not None]),
        first_feedback_ms=quantiles([r.first_feedback_ms for r in runs if r.first_feedback_ms is not None]),
        cache_hit_ratio=(cached / prompt if prompt else None),
        cost_per_run=quantiles([r.cost for r in runs if r.cost is not None]),
        llm_calls_per_run=quantiles([float(r.n_llm_calls) for r in runs]),
        llm_error_rate=proportion(sum(r.llm_errors for r in runs), calls),
        schema_retry_rate=proportion(sum(r.schema_retries for r in runs), calls),
        tokens_per_run=quantiles([float(r.usage.total_tokens) for r in runs]),
    )

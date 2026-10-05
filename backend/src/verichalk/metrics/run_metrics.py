"""单次运行的指标：全部由事件日志计算（D6）。覆盖 metrics.md 的 D（延迟）、E（成本与缓存）、F（可靠性）组。"""

from __future__ import annotations

from collections import defaultdict

from pydantic import BaseModel, Field

from ..domain.events import (
    Event,
    LLMCall,
    MessageDelta,
    Progress,
    RunFinished,
    RunStarted,
    SpanFinished,
    SpanKind,
)
from ..domain.llm import Usage
from .completeness import check_trace_completeness
from .stats import percentile


class StageStat(BaseModel):
    name: str
    count: int
    total_ms: float
    max_ms: float


class ModelStat(BaseModel):
    model: str
    role: str
    calls: int
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    cache_hit_ratio: float | None
    cost: float | None
    ttft_p50_ms: float | None
    total_p50_ms: float | None
    retries: int
    errors: int


class RunMetrics(BaseModel):
    run_id: str
    status: str | None = None
    error_code: str | None = None
    e2e_ms: float | None = None
    first_feedback_ms: float | None = None  # D1 的服务端近似：运行开始到首个进度 / 消息事件
    n_events: int = 0
    n_spans: int = 0
    trace_ok: bool = True
    trace_problems: list[str] = Field(default_factory=list)
    stages: list[StageStat] = Field(default_factory=list)
    models: list[ModelStat] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    cache_hit_ratio: float | None = None  # E2
    cost: float | None = None  # E1（任一调用成本未知则整体为 None）
    currency: str = "CNY"
    n_llm_calls: int = 0
    n_tool_calls: int = 0
    llm_errors: int = 0
    llm_retries: int = 0
    schema_retries: int = 0  # E5
    replayed_calls: int = 0


def _ratio(num: int, den: int) -> float | None:
    return num / den if den > 0 else None


def compute_run_metrics(events: list[Event]) -> RunMetrics:
    run_id = events[0].run_id if events else ""
    m = RunMetrics(run_id=run_id, n_events=len(events))
    comp = check_trace_completeness(events)
    m.trace_ok, m.trace_problems, m.n_spans = comp.ok, comp.problems, comp.n_spans

    t_start: float | None = None
    for e in events:
        if isinstance(e, RunStarted):
            t_start = e.ts
        elif isinstance(e, RunFinished):
            m.status = e.status
            m.error_code = e.error.code if e.error else None
            if t_start is not None:
                m.e2e_ms = (e.ts - t_start) * 1000
        elif isinstance(e, Progress | MessageDelta) and m.first_feedback_ms is None and t_start is not None:
            m.first_feedback_ms = (e.ts - t_start) * 1000

    stage: dict[str, list[float]] = defaultdict(list)
    for e in events:
        if isinstance(e, SpanFinished):
            if e.kind == SpanKind.stage:
                stage[e.name].append(e.duration_ms)
            elif e.kind == SpanKind.tool:
                m.n_tool_calls += 1
            if e.attrs.get("schema_retries"):
                m.schema_retries += int(e.attrs["schema_retries"])
    m.stages = [StageStat(name=k, count=len(v), total_ms=sum(v), max_ms=max(v)) for k, v in stage.items()]

    by_model: dict[tuple[str, str], list[LLMCall]] = defaultdict(list)
    for e in events:
        if isinstance(e, LLMCall):
            by_model[(e.record.model, e.record.role)].append(e)
    total = Usage()
    cost: float | None = 0.0
    for (model, role), calls in by_model.items():
        u = Usage()
        c: float | None = 0.0
        for ev in calls:
            r = ev.record
            u = u + r.usage
            c = None if (c is None or r.cost is None) else c + r.cost
            m.llm_retries += r.retries
            m.llm_errors += 1 if r.error else 0
            m.replayed_calls += 1 if r.from_cassette else 0
            m.currency = r.currency
        ttfts = [x.record.ttft_ms for x in calls if x.record.ttft_ms is not None]
        totals = [x.record.total_ms for x in calls if not x.record.error]
        m.models.append(
            ModelStat(
                model=model,
                role=role,
                calls=len(calls),
                prompt_tokens=u.prompt_tokens,
                completion_tokens=u.completion_tokens,
                cached_tokens=u.cached_tokens,
                cache_hit_ratio=_ratio(u.cached_tokens, u.prompt_tokens),
                cost=c,
                ttft_p50_ms=percentile(ttfts, 0.5),
                total_p50_ms=percentile(totals, 0.5),
                retries=sum(x.record.retries for x in calls),
                errors=sum(1 for x in calls if x.record.error),
            )
        )
        total = total + u
        cost = None if (cost is None or c is None) else cost + c
        m.n_llm_calls += len(calls)
    m.usage, m.cost = total, (cost if by_model else 0.0)
    m.cache_hit_ratio = _ratio(total.cached_tokens, total.prompt_tokens)
    return m

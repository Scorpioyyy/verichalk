"""核验评测（eval/specs/produce.md §2.2、§3）：直接测验证器，不经过写题模型。

- `VerifyBank`：正确题 / 注入了错误答案的题 / 题面被破坏的题。指标：V1 错答检出率、V2 正确题误杀率、V5 缺陷题检出率。
- 每个被核验的题在自己的 tracer 下运行，成本与延迟取自 `llm.call` 事件。
"""

from __future__ import annotations

import asyncio
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from ..core.features import FeatureFlags
from ..domain.blueprint import AnswerPart
from ..domain.events import LLMCall, SpanKind
from ..domain.paper import CheckStatus
from ..knowledge import KnowledgeService
from ..llm import LLMGateway
from ..metrics.stats import percentile, wilson
from ..trace import MemorySink, Tracer, span, use_tracer
from ..verify import VerifyEnv, VerifyInput, verify_item
from ..verify.checks import check_boundary

_FIGURE = re.compile(r"看图|如图|图中|下图|上图")


class BankItem(BaseModel):
    id: str
    split: str = "val"
    label: str  # correct / wrong / defect
    error_type: str | None = None
    base: str = ""
    grade: int | None = None
    lesson_id: str | None = None
    kind: str = "calc"
    stem: str
    options: list[str] = Field(default_factory=list)
    answers: list[str]
    gold: list[str] = Field(default_factory=list)
    solution: str = ""


def load_bank(path: Path, split: str | None = None) -> list[BankItem]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    items = [BankItem.model_validate(d) for d in data]
    # 依赖图形的题（"看图""如图"）本版本不出，也不拿来评测核验；模板里的这类题面不自足
    items = [i for i in items if not _FIGURE.search(i.stem)]
    return [i for i in items if split in (None, "all", i.split)]


@dataclass
class BankResult:
    item: BankItem
    checks: dict[str, str]
    details: dict[str, str]
    cost: float | None
    latency_ms: float
    n_calls: int
    caught_by: list[str] = field(default_factory=list)  # 判为 fail / warn 的检查名
    tokens_in: int = 0
    tokens_out: int = 0


async def _verify_one(
    env: VerifyEnv, item: BankItem, flags: FeatureFlags, sem: asyncio.Semaphore, *, use_lesson: bool = False
) -> BankResult:
    inp = VerifyInput(
        kind="calc"
        if item.kind == "fill"
        else item.kind,  # 模板题面的填空写法不统一（（ ）、=），评测核验的是答案而非版式
        stem=item.stem,
        options=item.options,
        answers=[AnswerPart(value=a) for a in item.answers],
        solution=item.solution,
        grade=item.grade,
        lesson_id=item.lesson_id if use_lesson else None,
    )
    sink = MemorySink()
    tracer = Tracer(f"bank_{item.id}", sink)
    async with sem, use_tracer(tracer), span(SpanKind.stage, "bank"):
        ver = await verify_item(env, inp, flags)
    llm_events = [e for e in sink.events if isinstance(e, LLMCall)]
    costs = [e.record.cost for e in llm_events]
    cost = None if any(c is None for c in costs) else float(sum(c for c in costs if c is not None))
    # 同一道题内的检查并行执行，延迟取最长的一次调用之和近似：用 span 总时长更准确
    spans = [e for e in sink.events if e.type == "span.finished" and getattr(e, "name", "") == "bank"]
    latency = float(getattr(spans[0], "duration_ms", 0.0)) if spans else 0.0
    checks = {c.name: c.status.value for c in ver.checks}
    details = {c.name: c.detail for c in ver.checks if c.detail}
    bad = [
        c.name
        for c in ver.checks
        if c.status in (CheckStatus.fail, CheckStatus.warn)
        and c.name in ("blind", "quality", "integration", "program")
    ]
    return BankResult(
        item,
        checks,
        details,
        cost,
        latency,
        len(llm_events),
        bad,
        sum(e.record.usage.prompt_tokens for e in llm_events),
        sum(e.record.usage.completion_tokens for e in llm_events),
    )


async def run_bank(
    gw: LLMGateway,
    kb: KnowledgeService | None,
    items: list[BankItem],
    *,
    off: str,
    concurrency: int = 8,
) -> list[BankResult]:
    env = VerifyEnv(llm=gw, kb=kb)  # type: ignore[arg-type]  仅跑不需要知识库的检查时 kb 可为 None
    flags = FeatureFlags.parse(off)
    sem = asyncio.Semaphore(concurrency)
    return list(await asyncio.gather(*(_verify_one(env, i, flags, sem) for i in items)))


def _rate(k: int, n: int) -> str:
    if n == 0:
        return "—"
    lo, hi = wilson(k, n)
    return f"{k / n:.3f} ({k}/{n}) [{lo:.2f},{hi:.2f}]"


def summarize_bank(results: list[BankResult]) -> dict[str, Any]:
    wrong = [r for r in results if r.item.label == "wrong"]
    correct = [r for r in results if r.item.label == "correct"]
    defect = [r for r in results if r.item.label == "defect"]
    out: dict[str, Any] = {
        "n": len(results),
        "v1": (sum(1 for r in wrong if r.caught_by), len(wrong)),
        "v2_fail": (sum(1 for r in correct if r.caught_by), len(correct)),
        "v5": (sum(1 for r in defect if r.caught_by), len(defect)),
    }
    by_type: dict[str, list[BankResult]] = defaultdict(list)
    for r in wrong + defect:
        by_type[str(r.item.error_type)].append(r)
    out["by_type"] = {t: (sum(1 for r in rs if r.caught_by), len(rs)) for t, rs in sorted(by_type.items())}
    by_check: dict[str, int] = defaultdict(int)
    for r in wrong:
        for c in r.caught_by:
            by_check[c] += 1
    out["wrong_caught_by_check"] = dict(by_check)
    costs = [r.cost for r in results if r.cost is not None]
    out["cost_per_item"] = statistics.fmean(costs) if costs else None
    out["tokens_in"] = statistics.fmean(r.tokens_in for r in results) if results else 0
    out["tokens_out"] = statistics.fmean(r.tokens_out for r in results) if results else 0
    lat = [r.latency_ms for r in results]
    out["latency_p50_ms"], out["latency_p95_ms"] = percentile(lat, 0.5), percentile(lat, 0.95)
    return out


def render_bank(title: str, s: dict[str, Any]) -> str:
    v1, v2, v5 = s["v1"], s["v2_fail"], s["v5"]
    lines = [
        f"### {title}",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| V1 错答检出率 | {_rate(*v1)} |",
        f"| V2 正确题误杀率 | {_rate(*v2)} |",
        f"| V5 缺陷题检出率 | {_rate(*v5)} |",
        f"| 单题成本（元） | {s['cost_per_item']:.4f} |"
        if s["cost_per_item"] is not None
        else "| 单题成本 | 未知（价格表无该模型） |",
        f"| 单题 token（输入 / 输出） | {s['tokens_in']:.0f} / {s['tokens_out']:.0f} |",
        f"| 延迟 p50 / p95 | {s['latency_p50_ms'] / 1000:.1f}s / {s['latency_p95_ms'] / 1000:.1f}s |",
        "",
        "按注入 / 破坏类型的检出：" + "；".join(f"{t} {k}/{n}" for t, (k, n) in s["by_type"].items()),
        "",
        "错答由哪项检查抓住（可重叠）："
        + "；".join(f"{k} {v}" for k, v in s["wrong_caught_by_check"].items()),
    ]
    return "\n".join(lines)


# ---- BoundaryBank：超纲核验（V3 / V4）----
class BoundaryItem(BaseModel):
    id: str
    split: str = "val"
    source: str = "authored"
    pair: str = ""
    lesson_id: str
    grade: int | None = None
    label: str  # in / out
    dimension: str = ""
    culprit: str | None = None
    stem: str
    solution: str = ""
    kind: str = "application"


def load_boundary(path: Path, split: str | None = None) -> list[BoundaryItem]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    items = [BoundaryItem.model_validate(d) for d in data]
    return [i for i in items if split in (None, "all", i.split)]


@dataclass
class BoundaryResult:
    item: BoundaryItem
    status: str  # pass / fail / skip
    verdict: str  # in / borderline / out / ""
    detail: str
    cost: float | None
    latency_ms: float


async def _boundary_one(env: VerifyEnv, item: BoundaryItem, sem: asyncio.Semaphore) -> BoundaryResult:
    inp = VerifyInput(
        kind=item.kind,
        stem=item.stem,
        answers=[AnswerPart(value="?")],
        solution=item.solution,
        grade=item.grade,
        lesson_id=item.lesson_id,
    )
    sink = MemorySink()
    tracer = Tracer(f"bnd_{item.id}", sink)
    async with sem, use_tracer(tracer), span(SpanKind.stage, "bank"):
        res = await check_boundary(env, inp)
    llm_events = [e for e in sink.events if isinstance(e, LLMCall)]
    costs = [e.record.cost for e in llm_events]
    cost = None if any(c is None for c in costs) else float(sum(c for c in costs if c is not None))
    spans = [e for e in sink.events if e.type == "span.finished" and getattr(e, "name", "") == "bank"]
    latency = float(getattr(spans[0], "duration_ms", 0.0)) if spans else 0.0
    return BoundaryResult(
        item, res.status.value, str(res.evidence.get("verdict", "")), res.detail, cost, latency
    )


async def run_boundary(
    gw: LLMGateway, kb: KnowledgeService, items: list[BoundaryItem], *, concurrency: int = 8
) -> list[BoundaryResult]:
    env = VerifyEnv(llm=gw, kb=kb)
    sem = asyncio.Semaphore(concurrency)
    return list(await asyncio.gather(*(_boundary_one(env, i, sem) for i in items)))


def summarize_boundary(results: list[BoundaryResult]) -> dict[str, Any]:
    outs = [r for r in results if r.item.label == "out"]
    ins = [r for r in results if r.item.label == "in"]
    skipped = sum(1 for r in results if r.status == "skip")

    def flagged(r: BoundaryResult) -> bool:
        return r.verdict == "out"

    def flagged_soft(r: BoundaryResult) -> bool:
        return r.verdict in ("out", "borderline")

    by_dim: dict[str, list[BoundaryResult]] = defaultdict(list)
    for r in outs:
        by_dim[r.item.dimension].append(r)
    by_src: dict[str, list[BoundaryResult]] = defaultdict(list)
    for r in outs:
        by_src[r.item.source].append(r)
    costs = [r.cost for r in results if r.cost is not None]
    return {
        "n": len(results),
        "skipped": skipped,
        "v3": (sum(1 for r in outs if flagged(r)), len(outs)),
        "v3_soft": (sum(1 for r in outs if flagged_soft(r)), len(outs)),
        "v4": (sum(1 for r in ins if flagged(r)), len(ins)),
        "v4_soft": (sum(1 for r in ins if flagged_soft(r)), len(ins)),
        "by_dim": {d: (sum(1 for r in rs if flagged(r)), len(rs)) for d, rs in sorted(by_dim.items())},
        "by_source": {d: (sum(1 for r in rs if flagged(r)), len(rs)) for d, rs in sorted(by_src.items())},
        "cost_per_item": statistics.fmean(costs) if costs else None,
        "latency_p50_ms": percentile([r.latency_ms for r in results], 0.5),
        "latency_p95_ms": percentile([r.latency_ms for r in results], 0.95),
    }


def render_boundary(title: str, s: dict[str, Any]) -> str:
    lines = [
        f"### {title}",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| V3 超纲检出率（判 out） | {_rate(*s['v3'])} |",
        f"| V3' 超纲检出率（out 或 borderline） | {_rate(*s['v3_soft'])} |",
        f"| V4 范围内误杀率（判 out） | {_rate(*s['v4'])} |",
        f"| V4' 范围内被标 out / borderline | {_rate(*s['v4_soft'])} |",
        f"| 抽取失败（skip） | {s['skipped']} |",
        f"| 单题成本（元） | {s['cost_per_item']:.4f} |"
        if s["cost_per_item"] is not None
        else "| 单题成本 | 未知 |",
        f"| 延迟 p50 / p95 | {s['latency_p50_ms'] / 1000:.1f}s / {s['latency_p95_ms'] / 1000:.1f}s |",
        "",
        "越界题按维度的检出（判 out）：" + "；".join(f"{d} {k}/{n}" for d, (k, n) in s["by_dim"].items()),
        "",
        "越界题按来源的检出：" + "；".join(f"{d} {k}/{n}" for d, (k, n) in s["by_source"].items()),
    ]
    return "\n".join(lines)

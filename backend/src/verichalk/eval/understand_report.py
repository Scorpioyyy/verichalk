"""意图理解（B 组）的指标汇总：B1 字段通过率与幻觉率、B2 澄清精确率 / 召回、B3 路由准确率与混淆、理解阶段延迟。"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from ..metrics import percentile, proportion
from .checks import CheckOutcome
from .runner import SuiteResult


def _outcomes(r: SuiteResult) -> list[CheckOutcome]:
    return [o for c in r.cases for o in c.outcomes]


def has_understanding(r: SuiteResult) -> bool:
    return any(o.name == "route" or o.name.startswith(("field:", "absent:", "origin:")) for o in _outcomes(r))


def _rate(outs: list[CheckOutcome]):
    return proportion(sum(1 for o in outs if o.passed), len(outs))


def summary(r: SuiteResult) -> dict[str, Any]:
    """机器可读的汇总（写入 history.jsonl，用于对比不同版本）。"""
    outs = _outcomes(r)
    field = [o for o in outs if o.name.startswith("field:")]
    origin = [o for o in outs if o.name.startswith("origin:")]
    absent = [o for o in outs if o.name.startswith("absent:")]
    route = [o for o in outs if o.name == "route"]
    clar = [o for o in outs if o.name == "clarify"]
    tp = sum(1 for o in clar if o.data.get("expected") and o.data.get("actual"))
    fp = sum(1 for o in clar if not o.data.get("expected") and o.data.get("actual"))
    fn = sum(1 for o in clar if o.data.get("expected") and not o.data.get("actual"))
    return {
        "b1_field": _rate(field).p,
        "b1_n": len(field),
        "b1_origin": _rate(origin).p,
        "b1_hallucination": (1 - p) if (p := _rate(absent).p) is not None else None,
        "b2_precision": tp / (tp + fp) if tp + fp else None,
        "b2_recall": tp / (tp + fn) if tp + fn else None,
        "b2_asked_rate": (tp + fp) / len(clar) if clar else None,
        "b3_route": _rate(route).p,
        "clarify_tp": tp,
        "clarify_fp": fp,
        "clarify_fn": fn,
    }


def _pct(p) -> str:
    return "—" if p.p is None else f"{p.p:.3f}（{p.k}/{p.n}，95% CI {p.lo:.3f}–{p.hi:.3f}）"


def section(r: SuiteResult) -> list[str]:
    outs = _outcomes(r)
    field = [o for o in outs if o.name.startswith("field:")]
    origin = [o for o in outs if o.name.startswith("origin:")]
    absent = [o for o in outs if o.name.startswith("absent:")]
    route = [o for o in outs if o.name == "route"]
    clar = [o for o in outs if o.name == "clarify"]
    tp = sum(1 for o in clar if o.data.get("expected") and o.data.get("actual"))
    fp = sum(1 for o in clar if not o.data.get("expected") and o.data.get("actual"))
    fn = sum(1 for o in clar if o.data.get("expected") and not o.data.get("actual"))
    L = ["", "## 意图理解（B 组）", "", "| 指标 | 值 | 验收线 |", "|---|---|---|"]
    L.append(f"| **B1 字段通过率** | {_pct(_rate(field))} | ≥ 0.90 |")
    L.append(f"| B1-origin（明说的字段来源为 user） | {_pct(_rate(origin))} | ≥ 0.95 |")
    hal = proportion(sum(1 for o in absent if not o.passed), len(absent))
    L.append(f"| B1-幻觉率（没说却断言） | {_pct(hal)} | ≤ 0.03 |")
    L.append(f"| **B2 澄清精确率** | {_pct(proportion(tp, tp + fp))} | ≥ 0.85 |")
    L.append(f"| **B2 澄清召回** | {_pct(proportion(tp, tp + fn))} | ≥ 0.80 |")
    L.append(f"| B2 提问占比 | {_pct(proportion(tp + fp, len(clar)))} | ≤ 0.15 |")
    L.append(f"| **B3 路由准确率** | {_pct(_rate(route))} | ≥ 0.95 |")
    times = [
        s.total_ms for c in r.cases for res in c.results for s in res.metrics.stages if s.name == "understand"
    ]
    if times:
        p50, p95 = percentile(times, 0.5), percentile(times, 0.95)
        L.append(f"| 理解阶段延迟 | p50 {p50:.0f}ms / p95 {p95:.0f}ms（n={len(times)}） | p50 ≤ 2500ms |")
    methods = Counter(
        (res.run.state.get("understand") or {}).get("method", "?")
        for c in r.cases
        for res in c.results
        if res.turn_expect
    )
    L.append(f"| 解析方式 | {dict(methods)} | |")

    by_field: dict[str, list[bool]] = defaultdict(list)
    for o in field:
        by_field[o.name.split(":", 1)[1]].append(o.passed)
    L += ["", "### B1 按字段", "", "| 字段 | 通过率 |", "|---|---|"]
    L += [f"| {k} | {_pct(proportion(sum(v), len(v)))} |" for k, v in sorted(by_field.items())]

    conf = Counter((o.data["expected"], o.data["actual"]) for o in route)
    wrong = {k: n for k, n in conf.items() if k[0] != k[1]}
    L += ["", "### B3 路由混淆（仅错误项）", ""]
    L += [
        f"- 期望 `{e}` → 实际 `{a}`：{n}" for (e, a), n in sorted(wrong.items(), key=lambda kv: -kv[1])
    ] or ["无错误。"]

    fails = Counter(o.name for o in outs if not o.passed)
    L += ["", "### 失败类别计数（前 15）", "", "| 检查项 | 失败数 |", "|---|---|"]
    L += [f"| {k} | {n} |" for k, n in fails.most_common(15)] or ["| — | 0 |"]
    return L

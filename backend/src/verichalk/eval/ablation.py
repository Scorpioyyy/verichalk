"""消融实验：依次跑"全开"与"逐项关闭某个特性开关"，输出对照表（evaluation.md §9）。

判定规则写在报告末尾：关闭后若质量指标无显著下降、且延迟或成本更优，则该模块应当删除。
本表目前对比的是通用指标（成功率、延迟、缓存、成本、检查通过）；质量指标（A 组）随 M3 的判官接入后加入同一张表。
"""

from __future__ import annotations

import time
from collections.abc import Callable

from ..core.config import Settings
from . import understand_report
from .cases import Case
from .runner import ContainerFactory, SuiteResult, default_factory, run_suite


async def run_ablation(
    make_settings: Callable[[str], Settings],
    cases: list[Case],
    *,
    suite: str,
    split: str,
    flags: list[str],
    concurrency: int = 4,
    factory: ContainerFactory = default_factory,
    burn_in: bool = True,
) -> dict[str, SuiteResult]:
    """`make_settings(off)` 返回关闭 `off`（逗号分隔，空串表示全开）的设置。结果按变体名索引，首项为 'all-on'，次项 'A/A 噪声参照' 是同一配置的重复运行。"""
    results: dict[str, SuiteResult] = {}
    # 预热轮：先完整跑一遍并丢弃结果。进程级的一次性初始化（检索器、模块导入、连接）否则会全部记在第一个变体头上，
    # 造成"全开比关闭慢"的顺序假象（回放模式下实测约 300ms）。
    if burn_in and cases:
        await run_suite(
            make_settings(""), cases[:1], suite=suite, split=split, concurrency=1, factory=factory
        )
    for name, off in [("all-on", ""), ("A/A 噪声参照", ""), *[(f"off:{f}", f) for f in flags]]:
        results[name] = await run_suite(
            make_settings(off), cases, suite=suite, split=split, concurrency=concurrency, factory=factory
        )
    return results


def _row(name: str, r: SuiteResult, base: SuiteResult | None) -> str:
    a = r.aggregate
    passed = sum(1 for c in r.cases if c.passed)

    def delta(v: float | None, b: float | None, fmt: str) -> str:
        return "—" if v is None or b is None else format(v - b, fmt)

    ba = base.aggregate if base else None
    cache = "—" if a.cache_hit_ratio is None else f"{a.cache_hit_ratio:.3f}"
    cost = "—" if a.cost_per_run.p50 is None else f"{a.cost_per_run.p50:.5f}"
    d_pass = "" if base is None else f"{passed - sum(1 for c in base.cases if c.passed):+d}"
    d_p50 = "" if ba is None else delta(a.e2e_ms.p50, ba.e2e_ms.p50, "+.0f")
    d_cost = "" if ba is None else delta(a.cost_per_run.p50, ba.cost_per_run.p50, "+.5f")
    p50 = "—" if a.e2e_ms.p50 is None else f"{a.e2e_ms.p50:.0f}"
    p95 = "—" if a.e2e_ms.p95 is None else f"{a.e2e_ms.p95:.0f}"
    return (
        f"| {name} | {passed}/{len(r.cases)} {d_pass} | {a.success_rate.p if a.success_rate.p is not None else '—'} | "
        f"{p50} {d_p50} | {p95} | {cache} | {cost} {d_cost} |"
    )


def render_ablation(results: dict[str, SuiteResult]) -> str:
    base = results["all-on"]
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(base.started_at))
    L = [
        f"# 消融实验：{base.suite} / {base.split}",
        "",
        f"- 时间：{ts}　profile：`{base.settings.profile.value}`　模式：`{base.settings.llm_mode.value}`",
        f"- 每个变体用例数：{len(base.cases)}（括号后的数字是相对全开的变化）",
        "",
        "| 变体 | 用例通过 | F1 成功率 | D3 e2e p50 (ms) | D3 e2e p95 (ms) | E2 缓存命中 | E1 成本 p50 |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, r in results.items():
        L.append(_row(name, r, None if name == "all-on" else base))
    if understand_report.has_understanding(base):
        f = lambda v: "—" if v is None else f"{v:.3f}"  # noqa: E731
        L += [
            "",
            "### 意图理解质量（B 组）",
            "",
            "| 变体 | B1 字段通过率 | B1-origin | 幻觉率 | B2 精确率 | B2 召回 | B3 路由 | 理解阶段 p50 (ms) |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for name, r in results.items():
            u = understand_report.summary(r)
            t = [
                x.total_ms
                for c in r.cases
                for res in c.results
                for x in res.metrics.stages
                if x.name == "understand"
            ]
            p50 = "—" if not t else f"{sorted(t)[len(t) // 2]:.0f}"
            L.append(
                f"| {name} | {f(u['b1_field'])} | {f(u['b1_origin'])} | {f(u['b1_hallucination'])} | {f(u['b2_precision'])} | {f(u['b2_recall'])} | {f(u['b3_route'])} | {p50} |"
            )
    aa = results.get("A/A 噪声参照")
    if aa and base.aggregate.e2e_ms.p50 is not None and aa.aggregate.e2e_ms.p50 is not None:
        L += [
            "",
            f"> **噪声底**：同一配置重复运行，e2e p50 相差 {aa.aggregate.e2e_ms.p50 - base.aggregate.e2e_ms.p50:+.0f}ms。变体与全开的差异必须明显大于此值才可信。",
        ]
    L += [
        "",
        "## 判定规则",
        "",
        "关闭某模块后：质量指标无显著下降（置信区间包含 0），且延迟或成本更优 → **该模块应当删除**；",
        "质量下降但代价过高 → 记录权衡并写入 design.md；质量下降且代价可接受 → 保留。",
        "单次对比的样本量有限，延迟差异在噪声范围内时需增大样本量重复。",
    ]
    return "\n".join(L) + "\n"

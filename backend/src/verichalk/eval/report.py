"""评测报告：Markdown 报告 + 追加到 history.jsonl（metrics.md §9）。"""

from __future__ import annotations

import json
import subprocess
import time
from collections import defaultdict
from pathlib import Path

from ..metrics import proportion
from . import understand_report
from .runner import SuiteResult


def git_rev(root: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
        return (out.stdout.strip() or "unknown") + ("+dirty" if dirty else "")
    except Exception:  # pragma: no cover
        return "unknown"


def _pct(p) -> str:
    if p.p is None:
        return "—"
    return f"{p.p:.3f}（{p.k}/{p.n}，95% CI {p.lo:.3f}–{p.hi:.3f}）"


def _q(q, unit="ms") -> str:
    if q.p50 is None:
        return "—"
    return f"p50 {q.p50:.0f}{unit} / p95 {q.p95:.0f}{unit}（n={q.n}）"


def render_markdown(r: SuiteResult, *, models: dict[str, str], rev: str) -> str:
    agg = r.aggregate
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r.started_at))
    L = [
        f"# 评测报告：{r.suite} / {r.split}",
        "",
        f"- 时间：{ts}（用时 {r.duration_s:.1f}s）　提交：`{rev}`　profile：`{r.settings.profile.value}`　模式：`{r.settings.llm_mode.value}`",
        "- 模型角色：" + "，".join(f"{k}={v}" for k, v in models.items()),
        f"- 特性开关：{r.settings.features.describe()}",
        "",
    ]
    L += [
        "## 总览",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| 用例数 / 运行数 | {len(r.cases)} / {agg.n_runs} |",
        f"| F1 运行成功率 | {_pct(agg.success_rate)} |",
        f"| F6 trace 完整度 | {_pct(agg.trace_complete)} |",
        f"| D3 端到端时长 | {_q(agg.e2e_ms)} |",
        f"| D1 首反馈（服务端） | {_q(agg.first_feedback_ms)} |",
        f"| E2 KV 缓存命中率 | {'—' if agg.cache_hit_ratio is None else f'{agg.cache_hit_ratio:.3f}'} |",
        f"| E1 单次运行成本 | {'—' if agg.cost_per_run.p50 is None else f'p50 {agg.cost_per_run.p50:.4f} / p95 {agg.cost_per_run.p95:.4f}'} |",
        f"| E3 每次运行的模型调用数 | {'—' if agg.llm_calls_per_run.mean is None else f'均值 {agg.llm_calls_per_run.mean:.1f}'} |",
        f"| F2 模型错误率 | {_pct(agg.llm_error_rate)} |",
        f"| E5 结构化输出重试率 | {_pct(agg.schema_retry_rate)} |",
        "",
    ]

    by_check: dict[str, list[bool]] = defaultdict(list)
    by_tag: dict[str, list[bool]] = defaultdict(list)
    for c in r.cases:
        for o in c.outcomes:
            by_check[o.name].append(o.passed)
        for t in c.tags:
            by_tag[t].append(c.passed)
    L += ["## 检查项通过率", "", "| 检查 | 通过率 |", "|---|---|"]
    L += [f"| {k} | {_pct(proportion(sum(v), len(v)))} |" for k, v in sorted(by_check.items())]
    L += ["", "## 按标签切片（用例整体通过率）", "", "| 标签 | 通过率 |", "|---|---|"]
    L += [f"| {k} | {_pct(proportion(sum(v), len(v)))} |" for k, v in sorted(by_tag.items())]

    if understand_report.has_understanding(r):
        L += understand_report.section(r)
    fails = [c for c in r.cases if not c.passed]
    L += ["", f"## 失败样本（{len(fails)}）", ""]
    if not fails:
        L.append("无。")
    for c in fails:
        L.append(f"- **{c.case_id}** [{', '.join(c.tags)}]" + (f" — 错误：{c.error}" if c.error else ""))
        for o in c.outcomes:
            if not o.passed:
                L.append(f"  - ✗ {o.name}：{o.detail}（run `{o.run_id}`）")
    return "\n".join(L) + "\n"


def write_report(r: SuiteResult, out_dir: Path, *, models: dict[str, str], root: Path) -> Path:
    rev = git_rev(root)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(r.started_at))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{stamp}_{r.suite}_{r.split}.md"
    path.write_text(render_markdown(r, models=models, rev=rev), encoding="utf-8")
    agg = r.aggregate
    rec = {
        "id": f"{stamp}_{r.suite}_{r.split}",
        "ts": r.started_at,
        "suite": r.suite,
        "split": r.split,
        "rev": rev,
        "profile": r.settings.profile.value,
        "mode": r.settings.llm_mode.value,
        "models": models,
        "off": sorted(r.settings.features.off),
        **({"understand": understand_report.summary(r)} if understand_report.has_understanding(r) else {}),
        "n_cases": len(r.cases),
        "case_pass": sum(1 for c in r.cases if c.passed),
        "success_rate": agg.success_rate.p,
        "trace_complete": agg.trace_complete.p,
        "e2e_p50_ms": agg.e2e_ms.p50,
        "e2e_p95_ms": agg.e2e_ms.p95,
        "cache_hit_ratio": agg.cache_hit_ratio,
        "cost_p50": agg.cost_per_run.p50,
    }
    with (out_dir / "history.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return path

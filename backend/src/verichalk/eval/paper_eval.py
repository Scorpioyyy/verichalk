"""整卷蓝图评测（eval/specs/edit.md 指标 P-A～P-D）：确定性、不调用写题模型，只用知识库。

对每个整卷需求：排细目表（`plan_paper_structure`）→ 规划（纯规则，不调规划模型）→ 检查分值合计、题量区间、
知识点覆盖、难度梯度、题型与题位一致、知识点都在已学范围内。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Any

import yaml

from ..core.config import Settings
from ..domain.brief import Brief, Origin, PaperSpec, Slot
from ..domain.paper import ItemKind
from ..knowledge import ComboMiner, ComboWeights, KnowledgeService
from ..stages.paper_plan import apply_plan_to_brief, plan_paper_structure
from ..stages.plan_rules import build_draft, resolve_scope


@dataclass
class PaperRow:
    id: str
    n_items: int = 0
    total: int = 0
    pa: bool = False  # 分值合计 = 总分
    pb: bool = False  # 题量在合理区间
    pc: float = 0.0  # 知识点覆盖
    pd: bool = False  # 难度单调
    consistent: bool = False  # 规划的题型 = 题位题型
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.pa and self.pb and self.pc >= 0.9 and self.pd and self.consistent and not self.problems


def load_paper_specs(path: Path) -> list[dict[str, Any]]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _brief(c: dict[str, Any]) -> Brief:
    b = Brief()
    b.scope.grade = Slot[int](value=c["grade"], origin=Origin.user)
    b.scope.semester = Slot[str](value=c["semester"], origin=Origin.user)
    if c.get("units"):
        b.scope.units = Slot[list[str]](value=c["units"], origin=Origin.user)
    b.paper = PaperSpec(
        duration_min=c.get("duration"), total_score=c.get("total"), structure=c.get("structure", [])
    )
    if c.get("kinds"):
        b.kinds = Slot[list[ItemKind]](value=[ItemKind(k) for k in c["kinds"]], origin=Origin.user)
    return b


async def run_paper_eval(settings: Settings, cases: list[dict[str, Any]]) -> list[PaperRow]:
    kb = KnowledgeService.from_settings(settings)
    g = await kb.graph()
    miner = ComboMiner(g, ComboWeights.load(settings.config_dir))
    rows: list[PaperRow] = []
    for c in cases:
        row = PaperRow(c["id"])
        try:
            brief = _brief(c)
            plan = plan_paper_structure(brief)
            scope = await resolve_scope(brief, kb)
            draft = await build_draft(apply_plan_to_brief(brief, plan), kb, miner, scope, slots=plan.slots)
            specs = draft.blueprint.items
        except Exception as e:  # 评测要看到崩溃
            row.problems.append(f"崩溃：{type(e).__name__}: {str(e)[:150]}")
            rows.append(row)
            continue
        row.n_items, row.total = plan.n_items, plan.total_score
        fixed = bool(c.get("structure"))
        row.pa = sum(s.score for s in plan.slots) == plan.total_score and (
            fixed or plan.total_score in (c["total"], plan.n_items)
        )
        dur = c["duration"]
        row.pb = fixed or (dur / 5 * 1.5 <= plan.n_items <= dur / 5 * 2.5) or plan.n_items in (6, 30)
        main = set(scope.pool)
        covered = {k for s in specs for k in s.kp_ids} & main
        row.pc = min(1.0, len(covered) / max(1, min(len(main), len(specs))))
        row.pd = [s.difficulty for s in plan.slots] == sorted(s.difficulty for s in plan.slots) and all(
            a.difficulty <= b.difficulty for a, b in pairwise(specs)
        )
        row.consistent = len(specs) == plan.n_items and all(
            sp.kind == sl.kind for sp, sl in zip(specs, plan.slots, strict=True)
        )
        if not row.consistent:
            row.problems.append("规划的题型与细目表题位不一致")
        outside = {k for s in specs for k in s.kp_ids} - scope.learned
        if outside:
            row.problems.append(f"含未学知识点：{sorted(outside)[:3]}")
        rows.append(row)
    return rows


def render_paper_report(rows: list[PaperRow], title: str) -> str:
    n = len(rows)
    pa = sum(r.pa for r in rows) / n
    pb = sum(r.pb for r in rows) / n
    pc = sum(r.pc for r in rows) / n
    pd = sum(r.pd for r in rows) / n
    lines = [
        f"# 整卷蓝图评测：{title}",
        "",
        "| 指标 | 值 | 阈值 | 判定 |",
        "|---|---|---|---|",
        f"| P-A 分值合计 = 总分 | {pa:.3f} | = 1.00 | {'✅' if pa == 1 else '❌'} |",
        f"| P-B 题量落在时长区间 | {pb:.3f} | ≥ 0.95 | {'✅' if pb >= 0.95 else '❌'} |",
        f"| P-C 知识点覆盖（均值） | {pc:.3f} | ≥ 0.90 | {'✅' if pc >= 0.9 else '❌'} |",
        f"| P-D 难度单调 | {pd:.3f} | = 1.00 | {'✅' if pd == 1 else '❌'} |",
        "",
        "| 用例 | 题数 | 总分 | A | B | C | D | 说明 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r.id} | {r.n_items} | {r.total} | {'✅' if r.pa else '❌'} | {'✅' if r.pb else '❌'} "
            f"| {r.pc:.2f} | {'✅' if r.pd else '❌'} | {'；'.join(r.problems)} |"
        )
    return "\n".join(lines) + "\n"

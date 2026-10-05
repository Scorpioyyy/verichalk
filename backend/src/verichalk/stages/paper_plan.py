"""整卷的结构规划：从需求（时长、总分、题型结构）确定性地生成细目表（FR-4，eval/specs/edit.md 失败模式 10）。

不调用模型：题量、分区、分值、难度梯度都是可复现、可单独评测的规则（P-A～P-D）。知识点的选择与情境由规划阶段完成。
"""

from __future__ import annotations

import math
import re

from ..domain.brief import Brief, Origin
from ..domain.brief import Slot as BriefSlot
from ..domain.paper import ItemKind
from ..domain.paper_plan import PaperPlan, SectionPlan, Slot
from ..domain.paper_rules import distribute_scores
from .plan_rules import difficulty_ladder

# 试卷里题型的出现顺序
KIND_ORDER = [
    ItemKind.choice,
    ItemKind.judge,
    ItemKind.fill,
    ItemKind.calc,
    ItemKind.application,
    ItemKind.open,
]
KIND_TITLE = {
    ItemKind.choice: "选择题",
    ItemKind.judge: "判断题",
    ItemKind.fill: "填空题",
    ItemKind.calc: "计算题",
    ItemKind.application: "解决问题",
    ItemKind.open: "开放题",
}
# 默认的题型比例（单元测试 / 期中期末的常见结构）
DEFAULT_MIX = {
    ItemKind.choice: 0.20,
    ItemKind.judge: 0.10,
    ItemKind.fill: 0.25,
    ItemKind.calc: 0.20,
    ItemKind.application: 0.25,
}
MIN_ITEMS, MAX_ITEMS = 6, 30
DEFAULT_DURATION, DEFAULT_TOTAL = 40, 100


def estimate_count(duration_min: int) -> int:
    """按时长估计题量：约每 5 分钟 2 道（小学生答题节奏），限制在 6～30 题。"""
    return max(MIN_ITEMS, min(MAX_ITEMS, round(duration_min / 5 * 2)))


def _apportion(total: int, weights: dict[ItemKind, float]) -> dict[ItemKind, int]:
    """最大余数法把题量按比例分到各题型；至少保证每个入选题型 1 道（题量够的话）。"""
    s = sum(weights.values())
    raw = {k: total * w / s for k, w in weights.items()}
    base = {k: math.floor(v) for k, v in raw.items()}
    for k in sorted(raw, key=lambda k: (-(raw[k] - base[k]), KIND_ORDER.index(k)))[
        : total - sum(base.values())
    ]:
        base[k] += 1
    for k in list(base):  # 入选的题型不能是 0 道：从最多的题型匀一道
        if base[k] == 0 and total >= len(base):
            donor = max(base, key=lambda x: base[x])
            base[donor] -= 1
            base[k] = 1
    return {k: n for k, n in base.items() if n > 0}


def plan_paper_structure(brief: Brief, *, title: str = "") -> PaperPlan:
    spec = brief.paper
    notes: list[str] = []
    duration = (spec.duration_min if spec and spec.duration_min else None) or DEFAULT_DURATION
    if not (spec and spec.duration_min):
        notes.append(f"没有说明考试时长，先按 {DEFAULT_DURATION} 分钟估计")
    total = int(spec.total_score) if spec and spec.total_score else DEFAULT_TOTAL
    if not (spec and spec.total_score):
        notes.append(f"没有说明总分，先按 {DEFAULT_TOTAL} 分设计")

    # 教师给出的结构优先：[{"kind": "choice", "count": 10, "score": 2}, ...]
    counts: dict[ItemKind, int] = {}
    fixed_scores: dict[ItemKind, int] = {}
    for row in spec.structure if spec else []:
        try:
            kind = ItemKind(str(row.get("kind")))
            n = int(row.get("count") or 0)
        except (ValueError, TypeError):
            continue
        if n > 0:
            counts[kind] = counts.get(kind, 0) + n
            if row.get("score"):
                fixed_scores[kind] = int(float(row["score"]))
    if counts:
        n_total = sum(counts.values())
        if fixed_scores and set(fixed_scores) >= set(counts):
            total_fixed = sum(counts[k] * fixed_scores[k] for k in counts)
            if spec and spec.total_score and total_fixed != total:
                notes.append(
                    f"您指定的各题型分值合计 {total_fixed} 分，与总分 {total} 分不一致，已按各题型分值为准"
                )
            total = total_fixed
    else:
        n_total = estimate_count(duration)
        weights = dict(DEFAULT_MIX)
        if brief.kinds:
            weights = {k: DEFAULT_MIX.get(k, 0.2) for k in brief.kinds.value}
        counts = _apportion(n_total, weights)

    kinds_all = [k for k in KIND_ORDER if k in counts for _ in range(counts[k])]
    n = len(kinds_all)
    if fixed_scores and set(fixed_scores) >= set(counts):
        scores = [fixed_scores[k] for k in kinds_all]
    else:
        if total < n:
            notes.append(f"总分 {total} 小于题数 {n}，已提高到 {n} 分")
            total = n
        scores = distribute_scores(kinds_all, total)

    lo, hi = (brief.difficulty.value[0], brief.difficulty.value[-1]) if brief.difficulty else (1, 4)
    ladder = difficulty_ladder(n, lo, hi)  # 整卷从易到难；各分区按顺序拿到连续的一段
    slots = [Slot(kind=k, difficulty=d, score=s) for k, d, s in zip(kinds_all, ladder, scores, strict=True)]

    sections: list[SectionPlan] = []
    i = 0
    for kind in KIND_ORDER:
        c = counts.get(kind, 0)
        if not c:
            continue
        seg = slots[i : i + c]
        i += c
        sections.append(
            SectionPlan(
                title=KIND_TITLE[kind],
                kind=kind,
                count=c,
                score_each=min(s.score for s in seg),
                subtotal=sum(s.score for s in seg),
                difficulty=(seg[0].difficulty, seg[-1].difficulty),
            )
        )
    return PaperPlan(
        title=title or paper_title(brief),
        duration_min=duration,
        total_score=sum(s.score for s in slots),
        sections=sections,
        slots=slots,
        notes=notes,
    )


_GRADE_CN = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六"}
_NUM_CN = "零一二三四五六七八九十十一十二十三十四十五"


def paper_title(brief: Brief) -> str:
    """试卷标题：年级 + 学期 + 单元（教师限定了单元时）+ 测试卷，如"四年级下册第三单元测试卷"。"""
    g = brief.scope.grade.value if brief.scope.grade else None
    sem = brief.scope.semester.value if brief.scope.semester else None
    head = (f"{_GRADE_CN.get(g, '')}年级" if g else "") + ({"a": "上册", "b": "下册"}.get(sem or "", ""))
    units = brief.scope.units.value if brief.scope.units else []
    nums = sorted({int(m.group(1)) for u in units if (m := re.search(r"\.u(\d+)$", u))})
    unit = ""
    if len(nums) == 1:
        unit = f"第{_NUM_CN[nums[0]] if nums[0] <= 10 else nums[0]}单元"
    elif len(nums) > 1:
        unit = f"第{nums[0]}～{nums[-1]}单元"
    return (head + unit + "测试卷") if head else "数学测试卷"


def apply_plan_to_brief(brief: Brief, plan: PaperPlan) -> Brief:
    """规划阶段按题位数出题：题量 = 题位数。"""
    out = brief.model_copy(deep=True)
    out.count = BriefSlot[int](value=plan.n_items, origin=Origin.inferred)
    return out

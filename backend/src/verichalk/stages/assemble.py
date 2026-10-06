"""装配：把本次产出的题目放进会话的试卷（architecture §5 `assemble`）。

- 没有试卷：新建一份；
- 已有试卷：默认**追加**到最后一个分区（教师说"再来 3 道"）；明确说"重出 / 换一批"才**替换**全部题目；
- 试卷已经有分值时，新题按题型给默认分值；
- 一次装配 = 一条修订（经补丁，可撤销）。
"""

from __future__ import annotations

import time

from .. import trace
from ..core.ids import new_id
from ..domain.blueprint import Blueprint
from ..domain.paper import Item, ItemKind, Paper, Revision, Section
from ..domain.paper_ops import (
    AddItem,
    AddSection,
    Op,
    RemoveSection,
    SetMeta,
    SetTitle,
    apply_patch,
)
from ..domain.paper_plan import PaperPlan
from ..domain.paper_rules import Placement, default_score, distribute_scores
from .base import RunContext
from .paper_plan import KIND_ORDER, KIND_TITLE

_GRADE_CN = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六"}


def _meta_ops(paper: Paper, bp: Blueprint) -> list[Op]:
    ops: list[Op] = []
    if bp.grade and "grade" not in paper.meta:
        ops.append(SetMeta(key="grade", value=bp.grade))
    if bp.lesson_id and "lesson_id" not in paper.meta:
        ops.append(SetMeta(key="lesson_id", value=bp.lesson_id))
    return ops


async def assemble(ctx: RunContext, items: list[Item], bp: Blueprint, how: Placement = "new") -> Paper | None:
    if not items:
        return None
    current = await ctx.store.papers.get_current(ctx.session_id)
    if current is None:
        how = "new"
    grade = _GRADE_CN.get(bp.grade or 0, "")
    ops: list[Op]
    if how == "new" or current is None:
        base = Paper(id=new_id("pap"), rev=current.rev if current else 0)
        sec = Section(id=new_id("sec"), title="练习题", kind="mixed")
        ops = [
            SetTitle(title=f"{grade}年级数学练习" if grade else "数学练习"),
            AddSection(section=sec),
            *(AddItem(section_id=sec.id, item=it) for it in items),
            *_meta_ops(base, bp),
        ]
        summary = f"生成 {len(items)} 道题"
        paper = apply_patch(base, ops)
    else:
        scored = any(it.score is not None for it in current.all_items())
        if scored:
            items = [it.model_copy(update={"score": it.score or default_score(it.kind)}) for it in items]
        if how == "replace":
            sec = Section(id=new_id("sec"), title="练习题", kind="mixed")
            ops = [
                *(RemoveSection(section_id=s.id) for s in current.sections),
                AddSection(section=sec),
                *(AddItem(section_id=sec.id, item=it) for it in items),
                *_meta_ops(current, bp),
            ]
            summary = f"换成新的 {len(items)} 道题"
        else:
            target = current.sections[-1] if current.sections else None
            ops = []
            if target is None:
                target = Section(id=new_id("sec"), title="练习题", kind="mixed")
                ops.append(AddSection(section=target))
            ops += [AddItem(section_id=target.id, item=it) for it in items]
            ops += _meta_ops(current, bp)
            summary = f"追加 {len(items)} 道题"
        paper = apply_patch(current, ops)
    patch = [op.model_dump(mode="json") for op in ops]
    saved = await ctx.store.papers.save(
        ctx.session_id,
        paper,
        Revision(
            paper_id=paper.id,
            rev=paper.rev,
            author="agent",
            run_id=ctx.run_id,
            ts=time.time(),
            patch=patch,
            summary=summary,
        ),
    )
    await trace.paper_patched(paper.id, saved.rev, patch, summary)
    return paper


async def assemble_paper(
    ctx: RunContext,
    delivered: list[tuple[int, Item]],
    plan: PaperPlan,
    bp: Blueprint,
    *,
    title: str = "",
    note: str = "整卷",
    sample: bool = False,
) -> Paper | None:
    """整卷装配：按细目表分区（题型顺序）、同区内按题位顺序（难度从易到难）、给每题分值，替换会话里已有的试卷。

    `delivered` 是（题位序号，题目）。题位总数与实际交付数不同（有题没通过核验）时，按题型权重重新分配分值，保证合计仍是总分。
    `sample`：样题（整卷的几道题先给教师看风格）——每题沿用细目表里它那个题位的分值，合计不必是总分。"""
    if not delivered:
        return None
    delivered = sorted(delivered, key=lambda p: p[0])
    kinds = [it.kind for _, it in delivered]
    if sample or len(delivered) == len(plan.slots):
        scores = [plan.slots[i].score for i, _ in delivered]
    else:
        scores = distribute_scores(kinds, max(plan.total_score, len(kinds)))
    by_kind: dict[ItemKind, list[Item]] = {}
    for (_, it), sc in zip(delivered, scores, strict=True):
        by_kind.setdefault(it.kind, []).append(it.model_copy(update={"score": float(sc)}))
    current = await ctx.store.papers.get_current(ctx.session_id)
    grade = _GRADE_CN.get(bp.grade or 0, "")
    sections: list[tuple[Section, list[Item]]] = []
    for kind in KIND_ORDER:
        if kind in by_kind:
            sections.append(
                (Section(id=new_id("sec"), title=KIND_TITLE[kind], kind=kind.value), by_kind[kind])
            )
    ops: list[Op] = []
    base = current if current is not None else Paper(id=new_id("pap"), rev=0)
    if current is not None:
        ops += [RemoveSection(section_id=s.id) for s in current.sections]
    ops.append(SetTitle(title=title or plan.title or (f"{grade}年级数学测试卷" if grade else "数学测试卷")))
    ops += [AddSection(section=s) for s, _ in sections]
    ops += [AddItem(section_id=s.id, item=it) for s, its in sections for it in its]
    total = int(sum(scores))
    for key, val in (
        ("total_score", total),
        ("duration_minutes", None if sample else plan.duration_min),  # 样题不是整卷：没有建议用时
        ("grade", bp.grade),
        ("lesson_id", bp.lesson_id),
    ):
        if val is not None:
            ops.append(SetMeta(key=key, value=val))
    paper = apply_patch(base, ops)
    patch = [op.model_dump(mode="json") for op in ops]
    saved = await ctx.store.papers.save(
        ctx.session_id,
        paper,
        Revision(
            paper_id=paper.id,
            rev=paper.rev,
            author="agent",
            run_id=ctx.run_id,
            ts=time.time(),
            patch=patch,
            summary=note,
        ),
    )
    await trace.paper_patched(paper.id, saved.rev, patch, note)
    return paper

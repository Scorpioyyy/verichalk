"""装配（M3 的最小版本）：把本次产出的题目放进一份新的试卷修订，并发出补丁事件。

分区排序、分值、答案页、整卷结构与"在已有试卷上追加 / 替换"属于 M5；这里只保证：产出的题目有一个稳定的落点、
经由 Patch 机制（D22）进入试卷、可撤销，调试台与用户端读同一份事件。
"""

from __future__ import annotations

import time

from .. import trace
from ..core.ids import new_id
from ..domain.blueprint import Blueprint
from ..domain.paper import Item, Paper, Revision, Section
from ..domain.paper_ops import AddItem, AddSection, SetTitle, apply_patch
from .base import RunContext

_GRADE_CN = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六"}


async def assemble_basic(ctx: RunContext, items: list[Item], bp: Blueprint) -> Paper | None:
    if not items:
        return None
    current = await ctx.store.papers.get_current(ctx.session_id)
    base = Paper(id=new_id("pap"), rev=current.rev if current else 0)
    grade = _GRADE_CN.get(bp.grade or 0, "")
    title = f"{grade}年级数学练习" if grade else "数学练习"
    sec = Section(id=new_id("sec"), title="练习题", kind="mixed")
    ops = [
        SetTitle(title=title),
        AddSection(section=sec),
        *(AddItem(section_id=sec.id, item=it) for it in items),
    ]
    paper = apply_patch(base, ops)
    await ctx.store.papers.save(
        ctx.session_id,
        paper,
        Revision(
            paper_id=paper.id,
            rev=paper.rev,
            author="agent",
            run_id=ctx.run_id,
            ts=time.time(),
            patch=[op.model_dump(mode="json") for op in ops],
            summary=f"生成 {len(items)} 道题",
        ),
    )
    await trace.paper_patched(
        paper.id, paper.rev, [op.model_dump(mode="json") for op in ops], f"生成 {len(items)} 道题"
    )
    return paper

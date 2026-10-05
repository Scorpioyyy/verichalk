"""复核阶段：用户手改（或新增）了题目内容之后，对这些题重新核验（architecture §5 `edit` 的复核部分，C2）。

手改的题没有求解程序，所以最多是"已校对"（单路：独立盲解）；结构检查不过也不会像生成的题那样被丢弃——
那是教师自己写的题，不能凭空消失——而是标成"需复核"并把原因给出来。结果只在该题的 rev 没变时落盘（R2）。
"""

from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel, Field

from .. import trace
from ..core.errors import KnowledgeError, NotFound
from ..domain.blueprint import AnswerPart
from ..domain.paper import Item, Verification, VerifyStatus
from ..verify import VerifyEnv, VerifyInput, verify_item
from .base import RunContext, Stage

log = logging.getLogger("verichalk.review")


class ReviewTarget(BaseModel):
    item_id: str
    rev: int  # 提交复核时该题的 rev；落盘时对不上就说明用户又改过，结论作废


class ReviewIn(BaseModel):
    targets: list[ReviewTarget]


class ReviewOut(BaseModel):
    updated: list[str] = Field(default_factory=list)
    stale: list[str] = Field(default_factory=list)  # 复核期间被再次修改（或删除）的题


def answer_parts(item: Item) -> list[AnswerPart]:
    """题目的结构化答案；用户手改过答案（`answer_value` 已丢弃）时，按答案串拆成每问一个值。"""
    if isinstance(item.answer_value, list) and item.answer_value:
        try:
            return [AnswerPart.model_validate(a) for a in item.answer_value]
        except Exception:  # 旧数据格式不符：退回答案串
            log.warning("review: bad answer_value on %s", item.id)
    text = item.answer.strip()
    if not text:
        return []
    pieces = [p.strip() for p in text.replace(";", "；").split("；") if p.strip()]
    return [AnswerPart(value=p) for p in pieces]


async def verify_input_for(ctx: RunContext, item: Item, meta: dict) -> VerifyInput:
    names: list[str] = []
    for kid in item.kp_ids[:3]:
        try:
            names.append((await ctx.kb.kp(kid)).name)
        except (KnowledgeError, NotFound):
            names.append(kid)
    grade = meta.get("grade")
    return VerifyInput(
        kind=item.kind.value,
        stem=item.stem,
        options=item.options,
        answers=answer_parts(item),
        solution=item.solution,
        solver_code="",
        kp_names=names,
        tier=item.tier.value,
        grade=grade if isinstance(grade, int) else None,
        lesson_id=meta.get("lesson_id") if isinstance(meta.get("lesson_id"), str) else None,
    )


class ReviewStage(Stage[ReviewIn, ReviewOut]):
    name = "review"
    input_model = ReviewIn
    output_model = ReviewOut

    async def run(self, ctx: RunContext, inp: ReviewIn) -> ReviewOut:
        paper = await ctx.store.papers.get_current(ctx.session_id)
        out = ReviewOut()
        if paper is None:
            out.stale = [t.item_id for t in inp.targets]
            return out
        env = VerifyEnv(llm=ctx.llm, kb=ctx.kb)
        feats = ctx.settings.features

        async def one(t: ReviewTarget) -> None:
            found = paper.find_item(t.item_id)
            if found is None or found[2].rev != t.rev:
                out.stale.append(t.item_id)
                return
            item = found[2]
            ver = await verify_item(env, await verify_input_for(ctx, item, paper.meta), feats)
            if ver.status == VerifyStatus.rejected:  # 教师自己的题不"废弃"：标成需复核并说明原因
                ver = Verification(status=VerifyStatus.needs_review, checks=ver.checks)
            saved = await ctx.store.papers.set_verification(
                ctx.session_id, t.item_id, t.rev, ver, run_id=ctx.run_id
            )
            if saved is None:
                out.stale.append(t.item_id)
                return
            out.updated.append(t.item_id)
            await trace.item_status(t.item_id, ver.status, ver.checks)
            await trace.paper_patched(paper.id, saved.rev, saved.patch, saved.summary)

        await asyncio.gather(*(one(t) for t in inp.targets))
        return out

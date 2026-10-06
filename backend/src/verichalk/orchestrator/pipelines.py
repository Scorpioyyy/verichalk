"""管线定义：名称 → 一个 `(ctx, turn) -> PipelineResult` 的异步函数。

管线是"哪些阶段按什么顺序运行"的唯一声明处；新增阶段在这里注册（architecture §10）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .. import trace
from ..core.ids import new_id
from ..domain.paper import Item
from ..domain.paper_rules import placement
from ..domain.perception import ReferenceSet
from ..domain.run import Attachment
from ..domain.understanding import ClarifyRequest, Route, Understanding
from ..stages import (
    AnswerIn,
    AnswerStage,
    DiagnosticIn,
    DiagnosticStage,
    EditIn,
    EditStage,
    PerceiveIn,
    PerceiveStage,
    PlanIn,
    PlanStage,
    ReviewIn,
    ReviewStage,
    ReviewTarget,
    RunContext,
    UnderstandIn,
    UnderstandStage,
    compose_reply,
    run_stage,
)
from ..stages.assemble import assemble
from ..stages.edit import compose_edit_reply
from ..stages.perceive_post import apply_edits
from ..stages.reply import compose_generate_reply
from .paper_flow import paper_flow
from .produce_flow import produce_specs


@dataclass
class TurnInput:
    text: str
    attachments: list[Attachment] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)  # 非对话触发的运行（如手改后的复核）带的参数


@dataclass
class PipelineResult:
    reply_message_id: str | None = None
    reply_text: str = ""


Pipeline = Callable[[RunContext, TurnInput], Awaitable[PipelineResult]]


async def diagnostic_pipeline(ctx: RunContext, turn: TurnInput) -> PipelineResult:
    out = await run_stage(ctx, DiagnosticStage(), DiagnosticIn(text=turn.text))
    return PipelineResult(reply_message_id=out.message_id, reply_text=out.reply)


async def review_pipeline(ctx: RunContext, turn: TurnInput) -> PipelineResult:
    """手改后的复核：只更新核验状态，不产生对话消息。"""
    targets = [ReviewTarget.model_validate(t) for t in turn.payload.get("targets", [])]
    await run_stage(ctx, ReviewStage(), ReviewIn(targets=targets))
    return PipelineResult()


def answer_text(answer: dict, req: ClarifyRequest) -> str:
    """把用户对澄清的回应（点选项或自由输入）变成一句补充说明，拼到原话后面重新理解。"""
    if answer.get("text"):
        return str(answer["text"]).strip()
    opt = next((o for o in req.options if o.id == answer.get("option")), None)
    if opt is None:
        return "你来定"
    return "保留原年级，不要超出该年级的内容" if opt.id == "keep_grade" else opt.label


DEFAULT_PHOTO_REQUEST = "照这页练习再出几道类似的题"  # 只发了照片、没写要求时


async def perceive_turn(ctx: RunContext, turn: TurnInput) -> ReferenceSet | None:
    """有照片：识别 → 展示识别卡片 →（有看不清的题）请教师确认或修改。没有照片返回 None。"""
    if not turn.attachments:
        return None
    rs = await run_stage(ctx, PerceiveStage(), PerceiveIn(attachments=turn.attachments))
    await trace.perception_ready(rs)
    if rs.usable and rs.needs_confirm and ctx.settings.features.enabled("perceive.confirm"):
        n = sum(it.low_confidence for it in rs.items)
        why = (
            f"有 {n} 道题的字迹没能完全看清，已按最可能的读法转写。请核对，需要的话直接修改。"
            if n
            else "这张照片画质一般，数字有可能读错。请核对一下转写结果，需要的话直接修改。"
        )
        ans = await ctx.ask(
            "perception",
            why,
            [{"id": "confirm", "label": "没问题，开始出题"}],
            {"references": rs.model_dump(mode="json")},
        )
        rs = apply_edits(rs, list(ans.get("items") or []))
        await trace.perception_ready(rs)
    return rs


async def understand_turn(
    ctx: RunContext, turn: TurnInput, refs: ReferenceSet | None = None
) -> Understanding:
    """理解需求，必要时澄清一次；发出 `understanding.ready`。`refs`：照片的识别结果（学生上下文与参考题）。"""
    photo = refs.context if refs is not None and refs.usable else None
    text = turn.text.strip() or (DEFAULT_PHOTO_REQUEST if photo else turn.text)
    base = UnderstandIn(text=text, has_paper=ctx.has_paper, prev_brief=ctx.prev_brief, photo=photo)
    u: Understanding = await run_stage(ctx, UnderstandStage(), base)
    if u.clarify:  # 一次最多问一个问题；回答后重新理解，不再追问
        answer = await ctx.ask("clarify", u.clarify.prompt, [o.model_dump() for o in u.clarify.options])
        supplement = answer_text(answer, u.clarify)
        again = base.model_copy(update={"text": f"{text}（补充：{supplement}）"})
        u = await run_stage(ctx, UnderstandStage(), again, key="clarified")
        if u.clarify:  # 仍缺范围（如回答"你来定"）：按默认继续并告知
            u.notes.append("范围仍不明确，已按常见情况处理，您可以随时调整")
            u.clarify = None
    if refs is not None:
        if refs.usable:
            if u.brief is not None:
                u.brief.references = refs.references()
            if refs.message:
                u.notes.append(refs.message)
        elif turn.text.strip():
            u.notes.append(f"照片没能识别，已按您的文字要求出题。{refs.message}")
    await trace.understanding_ready(u)
    return u


async def _finish(reply: str) -> PipelineResult:
    message_id = new_id("msg")
    await trace.message_done(message_id, reply)
    return PipelineResult(reply_message_id=message_id, reply_text=reply)


async def understand_pipeline(ctx: RunContext, turn: TurnInput) -> PipelineResult:
    """只做需求理解并复述（M2 的评测与调试用）。"""
    return await _finish(compose_reply(await understand_turn(ctx, turn)))


async def main_pipeline(ctx: RunContext, turn: TurnInput) -> PipelineResult:
    """理解需求 →（出题请求）规划 → 逐题创作与核验 → 装配 → 总结；其他路由给出相应回复。"""
    refs = await perceive_turn(ctx, turn)
    if refs is not None and not refs.usable and not turn.text.strip():
        return await _finish(refs.message)  # 只有照片且读不了：说明原因与下一步，不乱出题
    u = await understand_turn(ctx, turn, refs)
    if u.route == Route.generate and u.brief is not None:
        return await _finish(await generate(ctx, u, turn.text))
    if u.route == Route.paper and u.brief is not None:
        return await _finish(await paper_flow(ctx, u))
    if u.route == Route.edit and u.edit is not None and ctx.has_paper:
        out = await run_stage(
            ctx, EditStage(), EditIn(instruction=u.edit.instruction or turn.text, target=u.edit.target)
        )
        return await _finish(compose_edit_reply(out))
    if u.route == Route.ask and ctx.has_paper:
        ans = await run_stage(ctx, AnswerStage(), AnswerIn(question=turn.text))
        return PipelineResult(reply_message_id=ans.message_id, reply_text=ans.reply)
    if u.route == Route.export and ctx.has_paper:
        return await _finish(
            "好的，请点击页面上的“导出”按钮：可以选 PDF、Word、Markdown 或 LaTeX，教师版（含答案与解析）或学生版（空白卷），"
            "还能设置学校、班级和答案放在题后还是附页。"
        )
    return await _finish(compose_reply(u))


async def generate(ctx: RunContext, u: Understanding, text: str = "") -> str:
    """出题：规划 → 逐题创作与核验（并行，受并发上限约束）→ 装配 → 总结。每道题完成时立即发出 `item.status`。"""
    assert u.brief is not None
    bp = await run_stage(ctx, PlanStage(), PlanIn(brief=u.brief))
    outs = await produce_specs(ctx, bp, bp.items, u.brief, keep_kinds=u.brief.kinds is not None)
    items: list[Item] = [o.item for o in outs if o.item is not None]
    dropped = [o.dropped_reason for o in outs if o.item is None]
    how = placement(text, ctx.has_paper)
    await assemble(ctx, items, bp, how)
    return compose_generate_reply(u, bp, items, dropped, how)


PIPELINES: dict[str, Pipeline] = {
    "diagnostic": diagnostic_pipeline,
    "main": main_pipeline,
    "understand": understand_pipeline,
    "review": review_pipeline,
}

DEFAULT_PIPELINE = "main"

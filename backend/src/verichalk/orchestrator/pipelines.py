"""管线定义：名称 → 一个 `(ctx, turn) -> PipelineResult` 的异步函数。

管线是"哪些阶段按什么顺序运行"的唯一声明处；新增阶段在这里注册（architecture §10）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from .. import trace
from ..core.ids import new_id
from ..domain.run import Attachment
from ..domain.understanding import ClarifyRequest, Understanding
from ..stages import (
    DiagnosticIn,
    DiagnosticStage,
    RunContext,
    UnderstandIn,
    UnderstandStage,
    compose_reply,
    run_stage,
)


@dataclass
class TurnInput:
    text: str
    attachments: list[Attachment] = field(default_factory=list)


@dataclass
class PipelineResult:
    reply_message_id: str | None = None
    reply_text: str = ""


Pipeline = Callable[[RunContext, TurnInput], Awaitable[PipelineResult]]


async def diagnostic_pipeline(ctx: RunContext, turn: TurnInput) -> PipelineResult:
    out = await run_stage(ctx, DiagnosticStage(), DiagnosticIn(text=turn.text))
    return PipelineResult(reply_message_id=out.message_id, reply_text=out.reply)


def answer_text(answer: dict, req: ClarifyRequest) -> str:
    """把用户对澄清的回应（点选项或自由输入）变成一句补充说明，拼到原话后面重新理解。"""
    if answer.get("text"):
        return str(answer["text"]).strip()
    opt = next((o for o in req.options if o.id == answer.get("option")), None)
    if opt is None:
        return "你来定"
    return "保留原年级，不要超出该年级的内容" if opt.id == "keep_grade" else opt.label


async def main_pipeline(ctx: RunContext, turn: TurnInput) -> PipelineResult:
    """M2：理解需求（必要时澄清一次）→ 展示"本次假设" → 回复。出题等能力在后续里程碑接入。"""
    base = UnderstandIn(text=turn.text, has_paper=ctx.has_paper, prev_brief=ctx.prev_brief)
    u: Understanding = await run_stage(ctx, UnderstandStage(), base)
    if u.clarify:  # 一次最多问一个问题；回答后重新理解，不再追问
        answer = await ctx.ask("clarify", u.clarify.prompt, [o.model_dump() for o in u.clarify.options])
        supplement = answer_text(answer, u.clarify)
        again = base.model_copy(update={"text": f"{turn.text}（补充：{supplement}）"})
        u = await run_stage(ctx, UnderstandStage(), again, key="clarified")
        if u.clarify:  # 仍缺范围（如回答"你来定"）：按默认继续并告知
            u.notes.append("范围仍不明确，已按常见情况处理，您可以随时调整")
            u.clarify = None
    await trace.understanding_ready(u)
    reply = compose_reply(u)
    message_id = new_id("msg")
    await trace.message_done(message_id, reply)
    return PipelineResult(reply_message_id=message_id, reply_text=reply)


PIPELINES: dict[str, Pipeline] = {
    "diagnostic": diagnostic_pipeline,
    "main": main_pipeline,
}

DEFAULT_PIPELINE = "main"

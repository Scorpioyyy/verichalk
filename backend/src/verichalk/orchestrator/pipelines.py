"""管线定义：名称 → 一个 `(ctx, turn) -> PipelineResult` 的异步函数。

管线是"哪些阶段按什么顺序运行"的唯一声明处；新增阶段在这里注册（architecture §10）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ..domain.run import Attachment
from ..stages import DiagnosticIn, DiagnosticStage, RunContext, run_stage


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


PIPELINES: dict[str, Pipeline] = {
    "diagnostic": diagnostic_pipeline,
}

DEFAULT_PIPELINE = "diagnostic"  # M2 起改为 "main"

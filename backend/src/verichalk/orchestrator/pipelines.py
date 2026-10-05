"""管线定义：名称 → 一个 `(ctx, turn) -> PipelineResult` 的异步函数。

管线是"哪些阶段按什么顺序运行"的唯一声明处；新增阶段在这里注册（architecture §10）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .. import trace
from ..core.ids import new_id
from ..domain.brief import Brief
from ..domain.paper import Item
from ..domain.run import Attachment
from ..domain.understanding import ClarifyRequest, Route, Understanding
from ..stages import (
    DiagnosticIn,
    DiagnosticStage,
    PlanIn,
    PlanStage,
    ProduceIn,
    ProduceOut,
    ProduceStage,
    ReviewIn,
    ReviewStage,
    ReviewTarget,
    RunContext,
    UnderstandIn,
    UnderstandStage,
    compose_reply,
    run_stage,
)
from ..stages.assemble import assemble_basic
from ..stages.plan_rules import replacement_spec
from ..stages.reply import compose_generate_reply


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


async def understand_turn(ctx: RunContext, turn: TurnInput) -> Understanding:
    """理解需求，必要时澄清一次；发出 `understanding.ready`。"""
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
    u = await understand_turn(ctx, turn)
    if u.route == Route.generate and u.brief is not None:
        return await _finish(await generate(ctx, u))
    return await _finish(compose_reply(u))


def _constraints_text(brief: Brief) -> str:
    return "；".join(brief.constraints.value) if brief.constraints else ""


async def generate(ctx: RunContext, u: Understanding) -> str:
    """出题：规划 → 逐题创作与核验（并行，受并发上限约束）→ 装配 → 总结。每道题完成时立即发出 `item.status`。"""
    assert u.brief is not None
    bp = await run_stage(ctx, PlanStage(), PlanIn(brief=u.brief))
    n = len(bp.items)
    await trace.progress("开始逐题创作并核验", 0, n)
    sem = asyncio.Semaphore(ctx.settings.item_concurrency)
    done = 0
    constraints = _constraints_text(u.brief)

    async def one(spec) -> ProduceOut:
        nonlocal done
        async with sem:
            avoid = [
                s.angle or f"{s.scene}情境的{'、'.join(s.kp_names)}" for s in bp.items if s.id != spec.id
            ]
            out = await run_stage(
                ctx,
                ProduceStage(),
                ProduceIn(
                    spec=spec,
                    grade=bp.grade,
                    lesson_id=bp.lesson_id,
                    constraints=constraints,
                    avoid=avoid[:6],
                ),
                key=spec.id,
            )
        done += 1
        await trace.progress(f"已完成 {done}/{n} 道题的核验", done, n)
        return out

    outs = list(await asyncio.gather(*(one(s) for s in bp.items)))
    # 补题：丢弃的题换成更稳妥的写法再来一次，尽量凑够教师要的题量（一轮）
    missing = [i for i, o in enumerate(outs) if o.item is None]
    if missing:
        await trace.progress(f"有 {len(missing)} 道题没有通过核验，正在换个考法补上")
        repl = await asyncio.gather(
            *(one(replacement_spec(bp.items[i], keep_kinds=u.brief.kinds is not None)) for i in missing)
        )
        for i, r in zip(missing, repl, strict=True):
            if r.item is not None:
                outs[i] = r
    items: list[Item] = [o.item for o in outs if o.item is not None]
    dropped = [o.dropped_reason for o in outs if o.item is None]
    await assemble_basic(ctx, items, bp)
    return compose_generate_reply(u, bp, items, dropped)


PIPELINES: dict[str, Pipeline] = {
    "diagnostic": diagnostic_pipeline,
    "main": main_pipeline,
    "understand": understand_pipeline,
    "review": review_pipeline,
}

DEFAULT_PIPELINE = "main"

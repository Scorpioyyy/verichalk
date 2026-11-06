"""逐题生成的并行执行（出题与整卷共用）：每道题完成时立即发出 `item.status` 与 `item.delivered`（用户端逐题上屏），
丢弃的题立刻换个更稳妥的写法补一道。"""

from __future__ import annotations

import asyncio

from .. import trace
from ..core.aio import gather_all
from ..domain.blueprint import Blueprint, ItemSpec
from ..domain.brief import Action, Brief, Origin
from ..domain.paper import Tier
from ..stages import ProduceIn, ProduceOut, ProduceStage, RunContext, run_stage
from ..stages.plan_rules import replacement_spec


def reference_notes(brief: Brief) -> list[str]:
    """照片里的题的提示词写法：带上大题题干（"化简各数。0.70="），数字看不清的不当作范例。"""
    return [
        (f"{r.instruction} {r.text}" if r.instruction else r.text)
        + (f"（图：{r.figure_desc}）" if r.figure_desc else "")
        for r in brief.references
        if not r.uncertain
    ]


def constraints_text(brief: Brief) -> str:
    return "；".join(brief.constraints.value) if brief.constraints else ""


async def produce_specs(
    ctx: RunContext,
    bp: Blueprint,
    specs: list[ItemSpec],
    brief: Brief,
    *,
    keep_kinds: bool,
) -> list[ProduceOut]:
    """对 `specs` 逐题生成并核验（并行，受并发上限约束），返回与 `specs` 同序的结果；
    被丢弃的题立刻用更稳妥的写法补一道（不等整批结束）。`keep_kinds`：教师明说了题型（或整卷的细目表已定题型）就保持题型。"""
    n = len(specs)
    await trace.progress("开始逐题生成并核验", 0, n)
    sem = asyncio.Semaphore(ctx.settings.item_concurrency)
    done = delivered = 0
    constraints = constraints_text(brief)
    ref_texts = [r.text for r in brief.references if not r.uncertain]
    ref_notes = reference_notes(brief)
    # 教师明说要把几块内容结合 / 复习穿插：补上的题也要结合，不能降成单知识点题
    keep_combo = (
        brief.action.value == Action.review
        or len(set(brief.scope.kp_topics.values())) >= 2
        or bool(
            brief.tier_mix
            and brief.tier_mix.origin == Origin.user
            and Tier.integrated in brief.tier_mix.value
        )
    )

    async def write(spec: ItemSpec) -> ProduceOut:
        async with sem:
            avoid = [
                s.angle or f"{s.scene}情境的{'、'.join(s.kp_names)}" for s in bp.items if s.id != spec.id
            ]
            return await run_stage(
                ctx,
                ProduceStage(),
                ProduceIn(
                    spec=spec,
                    grade=bp.grade,
                    lesson_id=bp.lesson_id,
                    constraints=constraints,
                    request=brief.request,
                    avoid=avoid[:6],
                    references=ref_texts,
                    reference_notes=ref_notes,
                ),
                key=spec.id,
            )

    async def one(spec: ItemSpec) -> ProduceOut:
        nonlocal done, delivered
        out = await write(spec)
        if out.item is None:  # 没通过核验：立刻换个更稳妥的写法补一道，不等其他题
            again = await write(replacement_spec(spec, keep_kinds=keep_kinds, keep_combo=keep_combo))
            if again.item is not None:
                out = again
        done += 1
        await trace.progress(f"已完成 {done}/{n} 道题的核验", done, n)
        if out.item is not None:  # 先让教师看到：整份试卷要等全部完成才装配
            delivered += 1
            await trace.item_delivered(out.item, delivered)
        return out

    return list(await gather_all(*(one(s) for s in specs)))

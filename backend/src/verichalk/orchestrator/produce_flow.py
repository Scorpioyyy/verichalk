"""逐题创作的并行执行（出题与整卷共用）：每道题完成时立即发出 `item.status` 与 `item.delivered`（用户端逐题上屏），
丢弃的题换个考法补一轮。"""

from __future__ import annotations

import asyncio

from .. import trace
from ..domain.blueprint import Blueprint, ItemSpec
from ..domain.brief import Brief
from ..stages import ProduceIn, ProduceOut, ProduceStage, RunContext, run_stage
from ..stages.plan_rules import replacement_spec


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
    """对 `specs` 逐题创作并核验（并行，受并发上限约束），返回与 `specs` 同序的结果；
    被丢弃的题用更稳妥的写法补一轮。`keep_kinds`：教师明说了题型（或整卷的细目表已定题型）就保持题型。"""
    n = len(specs)
    await trace.progress("开始逐题创作并核验", 0, n)
    sem = asyncio.Semaphore(ctx.settings.item_concurrency)
    done = delivered = 0
    constraints = constraints_text(brief)

    async def one(spec: ItemSpec) -> ProduceOut:
        nonlocal done, delivered
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
        if out.item is not None:  # 先让教师看到：整份试卷要等全部完成才装配
            delivered += 1
            await trace.item_delivered(out.item, delivered)
        return out

    outs = list(await asyncio.gather(*(one(s) for s in specs)))
    missing = [i for i, o in enumerate(outs) if o.item is None]
    if missing:
        await trace.progress(f"有 {len(missing)} 道题没有通过核验，正在换个考法补上")
        repl = await asyncio.gather(
            *(one(replacement_spec(specs[i], keep_kinds=keep_kinds)) for i in missing)
        )
        for i, r in zip(missing, repl, strict=True):
            if r.item is not None:
                outs[i] = r
    return outs

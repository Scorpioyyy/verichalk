"""整卷：分阶段生成（蓝图 → 样题 → 全卷），每一步都可调整后继续（PRD FR-4，原则 7）。

1. **蓝图**：确定性地排出细目表（分区 × 题型 × 题量 × 分值 × 难度梯度，`stages/paper_plan`），在 `blueprint` 检查点展示；
   教师可确认，或用一句话调整（"选择题多两道""满分改成 120"）——调整由模型译成结构化调整项，再由规则重排。
2. **样题**：先出 2～3 道不同题型的样题，上屏并在 `samples` 检查点让教师判断风格；可重出一次。
3. **全卷**：其余题目并行创作与核验，按细目表装配（分区、分值、总分精确）。
特性开关 `paper.staged` 关闭（或运行环境不支持检查点，如评测）时，直接一次出完整份卷子。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from .. import trace
from ..core.errors import LLMError
from ..domain.blueprint import Blueprint
from ..domain.brief import Brief, PaperSpec
from ..domain.llm import Role
from ..domain.paper import Item, ItemKind, VerifyStatus
from ..domain.paper_plan import PaperPlan
from ..domain.understanding import Understanding
from ..llm import LLMRequest, complete_json, get_prompt
from ..stages import PlanIn, PlanStage, RunContext, run_stage
from ..stages.assemble import assemble_paper
from ..stages.paper_plan import KIND_TITLE, apply_plan_to_brief, plan_paper_structure
from .produce_flow import produce_specs

log = logging.getLogger("verichalk.paper")

_CONFIRM = re.compile(r"^(?:好|好的|可以|行|没问题|确认|就这样|继续|开始|ok|OK)[，。!！\s]*$")
MAX_ADJUST_ROUNDS = 2
N_SAMPLES = 3


class PaperTweak(BaseModel):
    duration_min: int | None = None
    total_score: int | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    scores: dict[str, int] = Field(default_factory=dict)
    remove_kinds: list[str] = Field(default_factory=list)
    note: str = ""


def confirmed(answer: dict[str, Any]) -> bool:
    text = str(answer.get("text") or "").strip()
    if text:
        return bool(_CONFIRM.match(text))
    return answer.get("option") != "adjust"


def apply_tweak(brief: Brief, plan: PaperPlan, tweak: PaperTweak) -> Brief:
    """把调整项并进需求：题型结构（题量 / 每题分值）、时长、总分。规则重排由 `plan_paper_structure` 完成。"""
    counts = {s.kind.value: s.count for s in plan.sections}
    scores = {s.kind.value: s.score_each for s in plan.sections}
    for k in tweak.remove_kinds:
        counts.pop(k, None)
    for k, n in tweak.counts.items():
        if k in {x.value for x in ItemKind}:
            if n > 0:
                counts[k] = n
            else:
                counts.pop(k, None)
    out = brief.model_copy(deep=True)
    spec = out.paper or PaperSpec()
    spec.duration_min = tweak.duration_min or plan.duration_min
    spec.total_score = tweak.total_score or plan.total_score
    rows: list[dict[str, Any]] = [{"kind": k, "count": n} for k, n in counts.items()]
    if tweak.scores:  # 教师指定了某些题型的每题分值：其余题型沿用当前分值
        for r in rows:
            r["score"] = tweak.scores.get(r["kind"], scores.get(r["kind"], 0))
    spec.structure = rows
    out.paper = spec
    return out


def table_text(plan: PaperPlan) -> str:
    lines = [
        f"- {s.title}：{s.count} 道，每题 {s.score_each} 分，小计 {s.subtotal} 分" for s in plan.sections
    ]
    lines.append(f"共 {plan.n_items} 道题，满分 {plan.total_score} 分，建议用时 {plan.duration_min} 分钟")
    return "\n".join(lines)


def pick_samples(plan: PaperPlan, n: int = N_SAMPLES) -> list[int]:
    """样题：从不同的分区各取一道（尽量铺开）：每个分区的第一题。"""
    starts: list[int] = []
    i = 0
    for s in plan.sections:
        starts.append(i)
        i += s.count
    if len(starts) <= n:
        return starts
    step = (len(starts) - 1) / (n - 1)
    return sorted({starts[round(k * step)] for k in range(n)})


async def _adjust(ctx: RunContext, plan: PaperPlan, text: str) -> PaperTweak | None:
    built = get_prompt("paper.adjust").render(
        dynamic={
            "table": plan.table(),
            "duration": plan.duration_min,
            "total": plan.total_score,
            "text": text,
        }
    )
    try:
        tweak, _ = await complete_json(
            ctx.llm,
            LLMRequest(
                role=Role.fast,
                messages=built.messages,
                purpose="paper.adjust",
                prompt=built.ref,
                max_tokens=500,
            ),
            PaperTweak,
        )
        return tweak
    except LLMError as e:
        log.warning("paper.adjust failed: %s", e.code)
        return None


def _sample_payload(items: list[Item]) -> list[dict[str, Any]]:
    return [
        {
            "id": it.id,
            "kind": it.kind.value,
            "stem": it.stem,
            "options": it.options,
            "answer": it.answer,
            "status": it.verification.status.value,
        }
        for it in items
    ]


def compose_paper_reply(plan: PaperPlan, items: list[Item], dropped: list[str], notes: list[str]) -> str:
    n = len(items)
    lines = [
        f"试卷已经排好：共 {n} 道题"
        + (f"（计划 {plan.n_items} 道）" if n != plan.n_items else "")
        + f"，满分 {plan.total_score} 分，建议用时 {plan.duration_min} 分钟。"
    ]
    by_kind: dict[ItemKind, int] = {}
    for it in items:
        by_kind[it.kind] = by_kind.get(it.kind, 0) + 1
    lines.append("- " + "；".join(f"{KIND_TITLE[k]} {c} 道" for k, c in by_kind.items()))
    counts = {
        s: sum(1 for it in items if it.verification.status == s)
        for s in (VerifyStatus.verified, VerifyStatus.checked, VerifyStatus.needs_review)
    }
    text = {
        VerifyStatus.verified: "已核验",
        VerifyStatus.checked: "已校对",
        VerifyStatus.needs_review: "需复核",
    }
    lines.append("- " + "，".join(f"{c} 道{text[s]}" for s, c in counts.items() if c))
    if dropped:
        lines.append(
            f"- 另有 {len(dropped)} 道题没有通过核验，已经舍弃，分值已重新分配，总分仍是 {plan.total_score} 分"
        )
    lines += [f"提示：{x}" for x in notes]
    lines.append("可以直接在试卷上修改，也可以告诉我怎么改（比如“第 5 题换个场景”）；满意后点“导出”。")
    return "\n".join(lines)


async def paper_flow(ctx: RunContext, u: Understanding) -> str:
    assert u.brief is not None
    brief = u.brief
    plan = plan_paper_structure(brief)
    staged = ctx.settings.features.enabled("paper.staged") and ctx.ask_fn is not None
    bp: Blueprint | None = None
    for rnd in range(MAX_ADJUST_ROUNDS + 1):
        bp = await run_stage(
            ctx,
            PlanStage(),
            PlanIn(brief=apply_plan_to_brief(brief, plan), slots=plan.slots),
            key=f"paper_plan{rnd}",
        )
        if not staged:
            break
        ans = await ctx.ask(
            "blueprint",
            "我先按您的要求排了一份细目表，您看看是否合适：\n"
            + table_text(plan)
            + "\n可以直接开始出题，也可以告诉我怎么调整（比如“选择题多两道”“满分改成 120”）。",
            [{"id": "confirm", "label": "就这样，开始出题"}, {"id": "adjust", "label": "我想调整"}],
            {"plan": plan.model_dump(mode="json"), "table": plan.table()},
        )
        if confirmed(ans) or rnd == MAX_ADJUST_ROUNDS:
            break
        tweak = await _adjust(ctx, plan, str(ans.get("text") or ""))
        if tweak is None or tweak.note:
            plan.notes.append(
                tweak.note if tweak and tweak.note else "没有听明白要怎么调整，先按原来的细目表出题"
            )
            break
        brief = apply_tweak(brief, plan, tweak)
        plan = plan_paper_structure(brief)
    assert bp is not None
    specs = bp.items
    delivered: list[tuple[int, Item]] = []
    dropped: list[str] = []
    rest = list(range(len(specs)))
    if staged and len(specs) > N_SAMPLES:
        sample_idx = pick_samples(plan)
        for attempt in range(2):
            outs = await produce_specs(
                ctx,
                bp,
                [
                    s.model_copy(update={"id": f"{s.id}{'b' * attempt}"})
                    for s in (specs[i] for i in sample_idx)
                ],
                brief,
                keep_kinds=True,
            )
            got = [(i, o.item) for i, o in zip(sample_idx, outs, strict=True) if o.item is not None]
            if got:
                await assemble_paper(
                    ctx, got, plan, bp, title=(plan.title or "试卷") + "（样题）", note="样题"
                )
            ans = await ctx.ask(
                "samples",
                "先出了几道样题，您看看风格和难度是否合适；合适就继续出完整份试卷。",
                [
                    {"id": "confirm", "label": "合适，继续出全卷"},
                    {"id": "redo", "label": "风格不对，重出样题"},
                ],
                {"items": _sample_payload([it for _, it in got])},
            )
            if attempt == 1 or ans.get("option") != "redo":
                delivered = got
                dropped += [o.dropped_reason for o in outs if o.item is None]
                rest = [i for i in range(len(specs)) if i not in sample_idx]
                break
            await trace.progress("好的，重新出一批样题")
    outs2 = await produce_specs(ctx, bp, [specs[i] for i in rest], brief, keep_kinds=True)
    delivered += [(i, o.item) for i, o in zip(rest, outs2, strict=True) if o.item is not None]
    dropped += [o.dropped_reason for o in outs2 if o.item is None]
    await assemble_paper(ctx, delivered, plan, bp, title=plan.title, note=f"整卷（{len(delivered)} 道题）")
    notes = [*plan.notes, *u.notes, *bp.notes]
    return compose_paper_reply(plan, [it for _, it in sorted(delivered, key=lambda p: p[0])], dropped, notes)

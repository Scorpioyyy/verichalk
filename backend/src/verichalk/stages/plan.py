"""规划阶段：`Brief` → `Blueprint`（architecture §5.2，D20）。

流程：范围解析 → 确定性蓝图（题量 / 档位 / 难度 / 题型 / 知识点 / 组合 / 情境，`plan_rules`）→ 发出检索事件（调试台的图检索视图）
→ 规划模型为每道题选择搭配、构想情境与问法（`plan.llm`）。模型不可用或输出非法时降级为确定性蓝图（F3）。
"""

from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel, Field

from .. import trace
from ..core.errors import BudgetExceeded, KnowledgeError, LLMError
from ..domain.blueprint import Blueprint
from ..domain.brief import Brief
from ..domain.knowledge import Combo, GraphEdge, GraphNode, RetrievalPayload
from ..domain.llm import Role
from ..domain.paper import ItemKind, Tier
from ..knowledge import ComboMiner, ComboWeights, GraphData
from ..llm import LLMGateway, LLMRequest, complete_json, get_prompt
from .base import RunContext, Stage
from .plan_rules import Draft, ScopeInfo, build_draft, resolve_scope

log = logging.getLogger("verichalk.plan")

_TIER_CN = {Tier.consolidate: "巩固", Tier.variation: "变式", Tier.integrated: "综合"}
_KIND_CN = {
    ItemKind.fill: "填空题",
    ItemKind.choice: "选择题",
    ItemKind.calc: "计算题",
    ItemKind.judge: "判断题",
    ItemKind.application: "应用题",
    ItemKind.open: "开放题",
}
_GRADE_CN = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六"}


class PlanIn(BaseModel):
    brief: Brief


class IdeaItem(BaseModel):
    index: int
    combo: str = ""
    scene: str = ""
    design: str = ""
    target_error: str = ""
    angle: str = ""
    roles: dict[str, str] = Field(default_factory=dict)


class IdeaOut(BaseModel):
    items: list[IdeaItem]


def _requirements(brief: Brief) -> str:
    bits: list[str] = []
    if brief.scenes:
        bits.append("指定情境：" + "、".join(brief.scenes.value))
    if brief.constraints:
        bits.append("限制：" + "；".join(brief.constraints.value))
    return "；".join(bits) or "无特别要求"


def retrieval_payload(g: GraphData, scope: ScopeInfo, draft: Draft) -> RetrievalPayload:
    """规划的检索载荷：锚点、选中的组合及其关系边，供调试台绘制。"""
    nodes: dict[str, GraphNode] = {}

    def add(kid: str, role: str) -> None:
        n = g.nodes[kid]
        if kid not in nodes or role == "selected":
            nodes[kid] = GraphNode(
                id=kid,
                name=n.name,
                grade=n.grade,
                semester=n.semester,
                domain=n.domain,
                role=role,  # type: ignore[arg-type]
            )

    for a in scope.anchors:
        add(a, "anchor")
    edges: list[GraphEdge] = []
    combos: list[Combo] = []
    for alts in draft.alts.values():
        for j, c in enumerate(alts):
            for kid in c.kp_ids:
                add(kid, "selected" if j == 0 else "candidate")
            for x in range(len(c.kp_ids)):
                for y in range(x + 1, len(c.kp_ids)):
                    key = GraphData.key(c.kp_ids[x], c.kp_ids[y])
                    types = list(g.edges.get(key, {})) or (["cooccur"] if key in g.cooccur else [])
                    for t in types[:1]:
                        edges.append(GraphEdge(source=key[0], target=key[1], type=t))
            if j == 0:
                combos.append(c)
    for it in draft.blueprint.items:
        for kid in it.kp_ids:
            add(kid, "selected")
    return RetrievalPayload(
        step="plan.combos",
        nodes=list(nodes.values()),
        edges=edges,
        combos=combos,
        notes=draft.blueprint.notes,
    )


def merge_ideas(draft: Draft, ideas: IdeaOut, user_scenes: list[str]) -> Blueprint:
    """把模型的选择合并进确定性蓝图；任何一项不合法就保留该项的确定性结果（约束由代码保证）。"""
    bp = draft.blueprint.model_copy(deep=True)
    by_idx = {i.index: i for i in ideas.items}
    notes = list(bp.notes)
    for it in bp.items:
        idea = by_idx.get(it.index)
        if idea is None:
            continue
        alts = draft.alts.get(it.id, [])
        choice = idea.combo.strip().upper()
        if alts and choice == "NONE":
            notes.append(f"第 {it.index} 题没有自然的综合搭配，改为单知识点题")
            it.kp_ids, it.kp_names, it.tier, it.rationale = it.kp_ids[:1], it.kp_names[:1], Tier.variation, ""
            it.review = False
        elif alts and len(choice) == 1 and 0 <= ord(choice) - 65 < len(alts):
            c = alts[ord(choice) - 65]
            names = {k: n for k, n in zip(it.kp_ids, it.kp_names, strict=False)}
            if c.kp_ids != it.kp_ids:
                it.kp_ids = list(c.kp_ids)
                it.kp_names = [names.get(k, k) for k in it.kp_ids]
                it.rationale = c.rationale
        if user_scenes:
            pass  # 教师指定的情境由确定性分配保证，不让模型改
        elif idea.scene and len(idea.scene) <= 24:
            it.scene = idea.scene.strip()
        if idea.angle:
            it.angle = idea.angle.strip()[:120]
        it.design = idea.design.strip()[:20]
        it.target_error = idea.target_error.strip()[:120]
        if idea.roles and len(it.kp_ids) > 1:
            by_name = {n: k for k, n in zip(it.kp_ids, it.kp_names, strict=False)}
            it.roles = {by_name[n]: r.strip()[:80] for n, r in idea.roles.items() if n in by_name}
    bp.notes = notes
    bp.method = "llm"
    return bp


class PlanStage(Stage[PlanIn, Blueprint]):
    name = "plan"
    input_model = PlanIn
    output_model = Blueprint

    async def run(self, ctx: RunContext, inp: PlanIn) -> Blueprint:
        brief = inp.brief
        feats = ctx.settings.features
        await trace.progress("正在回顾学过的内容，挑选合适的知识点")
        scope = await resolve_scope(brief, ctx.kb)
        g = await ctx.kb.graph()
        miner = ComboMiner(g, ComboWeights.load(ctx.settings.config_dir))
        draft = await build_draft(brief, ctx.kb, miner, scope, use_miner=feats.enabled("plan.combo_miner"))
        await trace.retrieval(retrieval_payload(g, scope, draft))
        if not feats.enabled("plan.llm") or not draft.blueprint.items:
            return draft.blueprint
        await trace.progress("正在构思题目的情境与考法")
        try:
            return await self._ideate(ctx, brief, draft, g)
        except (LLMError, BudgetExceeded, KnowledgeError) as e:
            log.warning("plan: ideation failed (%s), using the deterministic blueprint", e.code)
            draft.blueprint.notes.append("情境与考法由系统直接分配（智能构思暂时不可用）")
            return draft.blueprint

    async def _ideate(self, ctx: RunContext, brief: Brief, draft: Draft, g: GraphData) -> Blueprint:
        llm: LLMGateway = ctx.llm
        bp = draft.blueprint
        # 知识库里这些知识点"教什么、学生常错什么"：规划据此设计要暴露的易错点
        kp_ids = sorted(
            {k for it in bp.items for k in it.kp_ids}
            | {k for al in draft.alts.values() for c in al for k in c.kp_ids}
        )
        details = {d.id: d for d in await asyncio.gather(*(ctx.kb.kp(k) for k in kp_ids))}
        items = []
        for it in bp.items:
            alts = [
                {"names": " × ".join(g.nodes[k].name for k in c.kp_ids), "why": c.rationale[:120] or "—"}
                for c in draft.alts.get(it.id, [])
            ]
            scenes = []
            for s in draft.scene_cands.get(it.id, [])[:5]:
                ctx = draft.contexts.get(s)
                scenes.append(
                    s
                    + (
                        f"（{'、'.join(ctx.typical_quantities[:3])}）"
                        if ctx and ctx.typical_quantities
                        else ""
                    )
                )
            items.append(
                {
                    "index": it.index,
                    "tier_cn": _TIER_CN[it.tier],
                    "difficulty": it.difficulty,
                    "kind_cn": _KIND_CN[it.kind],
                    "kp_info": [
                        {
                            "name": details[k].name,
                            "desc": details[k].description[:70],
                            "errors": details[k].typical_errors[:2],
                        }
                        for k in dict.fromkeys(
                            it.kp_ids + [x for c in draft.alts.get(it.id, []) for x in c.kp_ids]
                        )
                        if k in details
                    ],
                    "alts": alts,
                    "scenes": "、".join(scenes) or "（自由选择生活情境）",
                }
            )
        grade = bp.grade
        built = get_prompt("plan.ideate").render(
            dynamic={
                "grade_text": f"{_GRADE_CN.get(grade or 0, '')}年级" if grade else "小学",
                "number_hint": draft.blueprint.items[0].number_hint if bp.items else "",
                "requirements": _requirements(brief),
                "items": items,
            }
        )
        out, _ = await complete_json(
            llm,
            LLMRequest(
                role=Role.smart,
                messages=built.messages,
                purpose="plan.ideate",
                prompt=built.ref,
                max_tokens=2600,
                temperature=0.5,
            ),
            IdeaOut,
        )
        return merge_ideas(draft, out, list(brief.scenes.value) if brief.scenes else [])

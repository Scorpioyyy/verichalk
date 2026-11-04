"""规划阶段：`Brief` → `Blueprint`（architecture §5.2，D20、D59）。

分工：**代码管约束，模型管设计**。
1. 确定性骨架（`plan_rules.build_draft`）：范围、题量、档位、难度、题型、数值范围（能力边界）——这些由代码保证；
2. 候选知识点菜单（`plan_rules.build_menu`）：教师点名的主题按"本册优先"检索，加上图上有桥的搭档，只含已学、可考的知识点；
3. 规划模型（`plan.llm`）读教师原话，为整套题从菜单里选知识点、设计一条解题链与具体情境；代码校验并合并
   （知识点必须来自菜单，教师点名主题时每道题至少考一个主题知识点，综合题要两个不同的知识点）。
模型不可用、输出非法或开关关闭时，骨架本身就是可用的蓝图（F3 降级）。
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from .. import trace
from ..core.errors import BudgetExceeded, KnowledgeError, LLMError
from ..domain.blueprint import Blueprint
from ..domain.brief import Brief
from ..domain.knowledge import Combo, GraphEdge, GraphNode, RetrievalPayload
from ..domain.llm import Role
from ..domain.paper import ItemKind, Tier
from ..domain.paper_plan import Slot
from ..knowledge import ComboMiner, ComboWeights, GraphData
from ..llm import LLMRequest, complete_json, get_prompt
from .base import RunContext, Stage
from .plan_rules import MenuEntry, ScopeInfo, build_draft, build_menu, resolve_scope

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
    slots: list[Slot] | None = None  # 整卷：细目表的题位（题型与难度已定）


class ComposeItem(BaseModel):
    index: int
    kps: list[str] = Field(default_factory=list)
    scene: str = ""
    plan: str = ""


class ComposeOut(BaseModel):
    items: list[ComposeItem]


def merge_compose(
    draft: Blueprint, out: ComposeOut, menu: list[MenuEntry], g: GraphData, brief: Brief
) -> Blueprint:
    """把模型的整套设计合并进确定性骨架（题量、档位、难度、题型、数值范围不变）。

    不变量：知识点只来自菜单；教师点名了主题时，每道题至少有一个主题知识点（不满足就由代码换上用得最少的主题知识点）；
    综合题有两个不同的知识点——给不出时，教师明说要结合就保留骨架的搭配，否则改成单知识点的变式题；
    `template` 来源的题与复习题的搭配不动；教师指定的情境不被模型改写。"""
    bp = draft.model_copy(deep=True)
    code = {m.code: m.kp_id for m in menu}
    by_idx = {i.index: i for i in out.items}
    user_scenes = bool(brief.scenes)
    must_combine = len(set(brief.scope.kp_topics.values())) >= 2
    topical = [m.kp_id for m in menu if m.topical]
    used = dict.fromkeys(topical, 0)
    for it in bp.items:
        x = by_idx.get(it.index)
        if x is None or it.source == "template":
            continue
        ids = list(dict.fromkeys(code[c.strip().upper()] for c in x.kps if c.strip().upper() in code))
        if it.review:
            pass  # 复习题的搭配（已学前置）由骨架决定，模型只设计情境与解题链
        elif it.tier == Tier.integrated:
            if len(ids) >= 2:
                it.kp_ids, it.rationale = ids[:2], "规划按解题链选择的搭配"
            elif ids and not must_combine:
                it.kp_ids, it.tier, it.rationale = ids[:1], Tier.variation, ""
        elif ids:
            it.kp_ids = ids[:1]
        if topical and not any(k in used for k in it.kp_ids):
            fix = min(topical, key=lambda k: used[k])
            it.kp_ids = [fix, *it.kp_ids[1:]] if it.tier == Tier.integrated else [fix]
            it.rationale = "按教师点名的主题替换"
        for k in it.kp_ids:
            if k in used:
                used[k] += 1
        it.kp_names = [g.nodes[k].name for k in it.kp_ids]
        scene = x.scene.strip()[:24]
        if not user_scenes and scene and scene != it.scene:
            it.scene, it.scene_hint = scene, ""  # 情境库的典型量只对应骨架里的原情境
        it.angle = x.plan.strip()[:120]
    bp.method = "llm"
    return bp


def _requirements(brief: Brief) -> str:
    bits: list[str] = []
    if brief.scenes:
        bits.append("指定情境：" + "、".join(brief.scenes.value))
    if brief.constraints:
        bits.append("限制：" + "；".join(brief.constraints.value))
    return "；".join(bits)


def retrieval_payload(
    g: GraphData, scope: ScopeInfo, menu: list[MenuEntry], bp: Blueprint
) -> RetrievalPayload:
    """规划的检索载荷（调试台图检索视图）：锚点、菜单里的候选、最终选中的知识点与综合题的搭配边。"""
    nodes: dict[str, GraphNode] = {}

    def add(kid: str, role: str, note: str = "") -> None:
        n = g.nodes.get(kid)
        if n is None or (kid in nodes and role != "selected"):
            return
        nodes[kid] = GraphNode(
            id=kid,
            name=n.name,
            grade=n.grade,
            semester=n.semester,
            domain=n.domain,
            role=role,  # type: ignore[arg-type]
            note=note,
        )

    for it in bp.items:
        for kid in it.kp_ids:
            add(kid, "selected")
    for a in scope.anchors:
        add(a, "anchor")
    for m in menu:
        add(m.kp_id, "candidate", m.note)
    edges: list[GraphEdge] = []
    combos: list[Combo] = []
    for it in bp.items:
        if len(it.kp_ids) < 2:
            continue
        a, b = it.kp_ids[0], it.kp_ids[1]
        key = GraphData.key(a, b)
        types = list(g.edges.get(key, {})) or (["cooccur"] if key in g.cooccur else [])
        edges += [GraphEdge(source=key[0], target=key[1], type=t) for t in types[:1]]
        combos.append(Combo(kp_ids=[a, b], score=0.0, rationale=it.rationale or it.angle))
    return RetrievalPayload(
        step="plan.combos", nodes=list(nodes.values()), edges=edges, combos=combos, notes=bp.notes
    )


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
        draft = await build_draft(
            brief, ctx.kb, miner, scope, use_miner=feats.enabled("plan.combo_miner"), slots=inp.slots
        )
        bp, menu = draft, []
        if feats.enabled("plan.llm") and any(it.source != "template" for it in bp.items):
            menu = await build_menu(brief, ctx.kb, miner, scope)
            if menu:
                await trace.progress("正在构思题目的情境与考法")
                try:
                    bp = await self._compose(ctx, brief, draft, menu, g)
                except (LLMError, BudgetExceeded, KnowledgeError) as e:
                    log.warning("plan: compose failed (%s), using the deterministic blueprint", e.code)
                    bp.notes.append("情境与考法由系统直接分配（智能构思暂时不可用）")
        await trace.retrieval(retrieval_payload(g, scope, menu, bp))
        return bp

    async def _compose(
        self, ctx: RunContext, brief: Brief, draft: Blueprint, menu: list[MenuEntry], g: GraphData
    ) -> Blueprint:
        bp = draft
        built = get_prompt("plan.compose").render(
            dynamic={
                "request": brief.request,
                "grade_text": f"{_GRADE_CN.get(bp.grade or 0, '')}年级" if bp.grade else "小学",
                "number_hint": bp.items[0].number_hint if bp.items else "",
                "requirements": _requirements(brief),
                "menu": [{"code": m.code, "name": m.name, "where": m.where, "note": m.note} for m in menu],
                "items": [
                    {
                        "index": it.index,
                        "tier_cn": _TIER_CN[it.tier],
                        "difficulty": it.difficulty,
                        "kind_cn": _KIND_CN[it.kind],
                    }
                    for it in bp.items
                ],
            }
        )
        out, _ = await complete_json(
            ctx.llm,
            LLMRequest(
                role=Role.smart,
                messages=built.messages,
                purpose="plan.compose",
                prompt=built.ref,
                max_tokens=2000,
                temperature=0.5,
            ),
            ComposeOut,
        )
        return merge_compose(draft, out, menu, g, brief)

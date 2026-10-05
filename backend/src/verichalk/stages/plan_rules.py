"""规划的确定性部分（D20）：范围解析、题量 / 档位 / 难度 / 题型分配、知识点与组合选择、情境分配。

LLM 只在这些确定性结果之上"选择与构想"（`plan.py`）；模型不可用或输出非法时，这里的结果就是可用的蓝图（降级）。
所有约束（范围合法、题量精确、档位分布、情境不重复）都由代码保证，不靠提示词请求模型遵守。
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field, replace

from ..domain.blueprint import Blueprint, ItemSpec
from ..domain.brief import Action, Brief, SourceMode
from ..domain.knowledge import BoundaryView, Combo, ContextBrief
from ..domain.paper import ItemKind, Tier
from ..knowledge import ComboMiner, GraphData, KnowledgeService, pair_signals

# 各档位默认的题型循环（与知识点自然题型取交集；用户指定题型时以用户为准）
DEFAULT_KINDS: dict[Tier, list[ItemKind]] = {
    Tier.consolidate: [ItemKind.calc, ItemKind.fill, ItemKind.judge, ItemKind.choice],
    Tier.variation: [ItemKind.fill, ItemKind.choice, ItemKind.application],
    Tier.integrated: [ItemKind.application],
}
TIER_ORDER = [Tier.consolidate, Tier.variation, Tier.integrated]
MAX_SCENE_REUSE = 2
N_ALTS = 3  # 每道综合题给规划模型备选的组合数


@dataclass
class ScopeInfo:
    lesson_id: str
    grade: int | None
    learned: set[str]
    anchors: list[str]  # 用户明说 / 检索映射到的知识点（按相关度）
    pool: list[str]  # 可用于出题的知识点（已学、可考），锚点在前
    notes: list[str] = field(default_factory=list)


@dataclass
class Draft:
    """确定性蓝图 + 供规划模型选择的备选。"""

    blueprint: Blueprint
    alts: dict[str, list[Combo]] = field(default_factory=dict)  # 综合 / 复习题 id → 备选组合（第 0 个已选中）
    scene_cands: dict[str, list[str]] = field(default_factory=dict)  # 题 id → 候选情境
    contexts: dict[str, ContextBrief] = field(default_factory=dict)  # 情境主题 → 该年级的典型量与数值范围
    boundary: BoundaryView | None = None


def largest_remainder(total: int, weights: dict[Tier, float]) -> dict[Tier, int]:
    """按比例把 total 分到各档位（最大余数法）；比例全为 0 时给巩固档。"""
    s = sum(max(0.0, w) for w in weights.values())
    if s <= 0 or total <= 0:
        return {Tier.consolidate: total}
    raw = {t: total * max(0.0, w) / s for t, w in weights.items()}
    base = {t: math.floor(v) for t, v in raw.items()}
    rest = total - sum(base.values())
    for t in sorted(raw, key=lambda k: (-(raw[k] - base[k]), TIER_ORDER.index(k) if k in TIER_ORDER else 9))[
        :rest
    ]:
        base[t] += 1
    return {t: n for t, n in base.items() if n > 0}


def difficulty_ladder(n: int, lo: int, hi: int) -> list[int]:
    if n <= 1:
        return [round((lo + hi) / 2)] * max(n, 0)
    return [lo + round((hi - lo) * i / (n - 1)) for i in range(n)]


def _evenly(pool: list[str], m: int) -> list[str]:
    """从 pool 里均匀地取 m 个（覆盖整个范围）；m 超过 pool 时循环。"""
    if not pool or m <= 0:
        return []
    if m >= len(pool):
        return [pool[i % len(pool)] for i in range(m)]
    return [pool[round(i * (len(pool) - 1) / (m - 1))] if m > 1 else pool[0] for i in range(m)]


async def resolve_scope(brief: Brief, kb: KnowledgeService) -> ScopeInfo:
    g = await kb.graph()
    s = brief.scope
    grade = s.grade.value if s.grade else None
    semester = s.semester.value if s.semester else None
    lesson = brief.target_lesson.value if brief.target_lesson else None
    default_notes: list[str] = []
    if lesson is None and grade:
        lesson = await kb.last_lesson(kb.book_id(grade, semester or "b"))
    if lesson is None:  # PRD §6：范围仍不明确时按三年级处理并明示
        grade, lesson = 3, await kb.last_lesson(kb.book_id(3, "b"))
        default_notes.append("没有说明年级，先按三年级处理，您可以随时调整")
    if lesson is None:
        raise ValueError("无法确定目标课时")
    learned = await kb.learned_before(lesson, inclusive=True)
    anchors = [k for k in (s.kp_ids.value if s.kp_ids else []) if k in g.nodes]
    notes: list[str] = list(default_notes)

    def usable(k: str) -> bool:
        n = g.nodes[k]
        return k in learned and n.assessable

    pool: list[str] = []
    if anchors:  # 用户点名了主题：题目就围绕这些知识点（同主线的知识点只作为组合的搭档，不稀释主题）
        pool = [k for k in anchors if usable(k)]
    elif s.units:
        units = set(s.units.value)
        pool = [
            n.id
            for n in sorted(g.nodes.values(), key=lambda n: n.position)
            if n.unit_id in units and usable(n.id)
        ]
    elif grade:
        pool = [
            n.id
            for n in sorted(g.nodes.values(), key=lambda n: n.position)
            if n.grade == grade and (semester is None or n.semester == semester) and usable(n.id)
        ]
    if not anchors and len(pool) > 6:
        # 大范围（整册 / 整单元）：优先考查核心且适合文字出题的知识点（习题多、有自然题型），避免出到活动类内容
        core = [k for k in pool if g.nodes[k].n_exercises >= 3 and g.nodes[k].kinds]
        if brief.kinds:
            want = {k.value for k in brief.kinds.value}
            matched = [k for k in core if g.nodes[k].kinds & want]
            core = matched if len(matched) >= 3 else core
        if len(core) >= 3:
            pool = core
    if not pool:
        notes.append("范围内没有找到可出题的知识点，已改用目标课时之前最近学过的内容")
        recent = sorted((g.nodes[k] for k in learned if usable(k)), key=lambda n: -n.position)[:12]
        pool = [n.id for n in recent]
    return ScopeInfo(lesson, grade, learned, anchors, pool, notes)


def _boundary_hint(b: BoundaryView) -> str:
    bits = []
    if b.integer_domain_max:
        bits.append(f"整数不超过 {b.integer_domain_max}")
    bits.append(f"小数最多 {b.decimal_max_places} 位" if b.decimal_max_places else "不出现小数")
    bits.append("可以出现" + "、".join(b.fraction_types) if b.fraction_types else "不出现分数")
    if b.operations:  # 已学的运算形态：没列出的（如"除数是小数的除法"）学生没学过，题里不能出现
        ops = "；".join(f"{op}（{'、'.join(forms)}）" for op, forms in b.operations.items())
        bits.append(f"只能用已学的运算形态——{ops}")
    return "；".join(bits)


def _pick_scene(cands: list[str], used: Counter[str]) -> str:
    for c in cands:
        if used[c] < MAX_SCENE_REUSE:
            return c
    return cands[0] if cands else ""


async def build_draft(
    brief: Brief, kb: KnowledgeService, miner: ComboMiner, scope: ScopeInfo, *, use_miner: bool = True
) -> Draft:
    g: GraphData = await kb.graph()
    count = brief.count.value if brief.count else 5
    lo, hi = brief.difficulty.value if brief.difficulty else (2, 4)
    template = brief.source.value == SourceMode.template
    review = brief.action.value == Action.review
    weights = (
        dict(brief.tier_mix.value)
        if brief.tier_mix
        else {Tier.consolidate: 0.3, Tier.variation: 0.4, Tier.integrated: 0.3}
    )
    if template:  # 用户明说要课本式的基础题：全部巩固档，不做综合
        weights = {Tier.consolidate: 1.0}
    tiers = largest_remainder(count, weights)
    sequence: list[Tier] = [t for t in TIER_ORDER for _ in range(tiers.get(t, 0))]
    if review:  # 复习：至少三分之一的题把已学前置知识点穿插进来（按综合档处理）
        n_review = max(1, math.ceil(count / 3))
        for i in range(n_review):
            sequence[-(i + 1)] = Tier.integrated
    ladder = difficulty_ladder(len(sequence), lo, hi)
    boundary = await kb.boundary(scope.lesson_id)
    grade = scope.grade or (g.nodes[scope.pool[0]].grade if scope.pool else None)
    ctx_list = await kb.contexts_for(grade, limit=60)
    contexts = {c.theme: c for c in ctx_list}
    user_scenes = list(brief.scenes.value) if brief.scenes else []

    n_single = sum(1 for t in sequence if t != Tier.integrated)
    pool = scope.pool
    if template:  # 课本式同类题只能来自有程序化题型（不依赖图形）的知识点
        usable = []
        for k in pool:
            ats = await kb.archetypes_for(k, limit=8)
            if any(
                a.kp_id == k and a.verifiable_type == "program" and (a.figure_ratio or 0) < 0.5 for a in ats
            ):
                usable.append(k)
        if usable:
            pool = usable
        else:
            scope.notes.append("该范围内没有可程序化生成的课本题型，改为由模型原创")
            template = False
    singles = iter(_evenly(pool, n_single))
    semester = brief.scope.semester.value if brief.scope.semester else None
    # 综合题的搭档必须适合纯文字出题（教材里有不依赖图形的题型）：作图、观察物体类的知识点不拼进综合题
    combo_scope = {k for k in scope.learned if g.nodes[k].kinds} if g.nodes else scope.learned
    if (
        brief.scope.units and not review
    ):  # 教师限定了单元：搭档不跨出这些单元（用户意图优先于"跨单元综合"的先验）
        units = set(brief.scope.units.value)
        combo_scope = {k for k in combo_scope if g.nodes[k].unit_id in units} or combo_scope
    # 先验：范围是整学期 / 整年级、没点名知识点也没限定单元时，综合题默认跨单元（区统考考的正是单元之间的综合）
    broad = not scope.anchors and not brief.scope.units
    n_integrated = sum(1 for t in sequence if t == Tier.integrated)
    anchor_cycle = scope.anchors or (_evenly(pool, n_integrated) if n_integrated else pool) or pool
    book_scope = {  # 本学期的知识点：整学期综合的搭档优先从这里取，不够再放宽到更早的内容
        k
        for k in combo_scope
        if scope.grade
        and g.nodes[k].grade == scope.grade
        and (not semester or g.nodes[k].semester == semester)
    }
    synth_miner = ComboMiner(g, replace(miner.w, proximity=0.3))  # 跨单元综合时，"教学位置相近"不再加分
    used_combo: list[tuple[str, ...]] = []
    # 多主题请求（"乘法和周长"）：综合题优先在用户点名的主题之间组合——这是教师明说的意图，先于图上的"自然搭配"
    groups: dict[str, list[str]] = {}
    for kid in scope.anchors:
        topic = brief.scope.kp_topics.get(kid)
        if topic and kid in scope.learned:
            groups.setdefault(topic, []).append(kid)
    topic_pairs: list[Combo] = []
    if len(groups) >= 2:
        names = list(groups)
        for ai in range(len(names)):
            for bi in range(ai + 1, len(names)):
                for ka in groups[names[ai]]:
                    for kb_ in groups[names[bi]]:
                        sc = sum(pair_signals(g, ka, kb_, miner.w).values())
                        topic_pairs.append(
                            Combo(
                                kp_ids=[ka, kb_],
                                score=sc,
                                rationale=f"教师要求把「{names[ai]}」和「{names[bi]}」结合起来",
                            )
                        )
        topic_pairs.sort(key=lambda c: (-c.score, c.kp_ids))
    used_scene: Counter[str] = Counter()
    items: list[ItemSpec] = []
    alts: dict[str, list[Combo]] = {}
    scene_cands: dict[str, list[str]] = {}
    notes = list(scope.notes)
    n_int = 0
    for i, tier in enumerate(sequence):
        spec_id = f"it{i + 1}"
        combo: Combo | None = None
        kp_ids: list[str] = []
        candidates: list[Combo] = []
        if tier == Tier.integrated:
            anchor = anchor_cycle[n_int % len(anchor_cycle)]
            n_int += 1
            if review:
                rc = [
                    r.id
                    for r in await kb.review_candidates(scope.lesson_id, [anchor], limit=12)
                    if r.id in g.nodes
                ]
                rc = [
                    r for r in rc if r != anchor and (r,) not in used_combo and (anchor, r) not in used_combo
                ]
                candidates = [
                    Combo(
                        kp_ids=[anchor, r],
                        score=1.0 - 0.05 * j,
                        rationale="复习：" + g.nodes[r].name + "是" + g.nodes[anchor].name + "的已学前置",
                    )
                    for j, r in enumerate(rc[:N_ALTS])
                ]
            elif topic_pairs:
                free = [
                    c
                    for c in topic_pairs
                    if tuple(c.kp_ids) not in used_combo and tuple(reversed(c.kp_ids)) not in used_combo
                ]
                candidates = (free or topic_pairs)[:N_ALTS]
            elif not use_miner:  # 消融基线：不用图结构，直接取"检索相近"的已学知识点
                hits = await kb.search(g.nodes[anchor].name, k=12)
                near = [
                    h.id
                    for h in hits
                    if h.id != anchor and h.id in scope.learned and (anchor, h.id) not in used_combo
                ]
                candidates = [
                    Combo(kp_ids=[anchor, k], score=1.0 - 0.05 * j, rationale="检索相近")
                    for j, k in enumerate(near[:N_ALTS])
                ]
            else:
                if (
                    broad
                ):  # 整学期综合：优先"同一学期、不同单元、不同主题、有桥"的搭配（区统考的综合题正是这种）
                    for sc, topic in (
                        (book_scope, "cross_topic"),
                        (book_scope, "any"),
                        (combo_scope, "cross_topic"),
                    ):
                        if len(candidates) >= N_ALTS:
                            break
                        candidates += synth_miner.mine(
                            [anchor],
                            sc | {anchor},
                            k=N_ALTS,
                            exclude=used_combo,
                            topic=topic,
                            cross_unit=True,
                        )
                for mode in ("cross_topic", "in_topic"):  # 其次跨主题（创新），再次同主题（教材式）
                    if len(candidates) >= N_ALTS:
                        break
                    candidates += miner.mine(
                        [anchor], combo_scope | {anchor}, k=N_ALTS, exclude=used_combo, topic=mode
                    )
                seen: set[frozenset[str]] = set()
                candidates = [
                    c
                    for c in candidates
                    if not (frozenset(c.kp_ids) in seen or seen.add(frozenset(c.kp_ids)))
                ][:N_ALTS]
            if candidates:
                combo = candidates[0]
                used_combo.append(tuple(combo.kp_ids))
                alts[spec_id] = candidates
            else:
                notes.append(f"第 {i + 1} 题没有找到合适的综合搭配，改为单知识点题")
                tier = Tier.variation
                kp_ids = [anchor]
        if combo is not None:
            kp_ids = combo.kp_ids
        elif tier != Tier.integrated:
            kp_ids = [next(singles)]
        kinds_pool = DEFAULT_KINDS[tier]
        if brief.kinds:
            kind = brief.kinds.value[i % len(brief.kinds.value)]
        else:
            natural = set().union(*(g.nodes[k].kinds for k in kp_ids)) if kp_ids else set()
            ordered = [k for k in kinds_pool if k.value in natural] or kinds_pool
            kind = ordered[sum(1 for it in items if it.tier == tier) % len(ordered)]
        # 情境：用户指定 > 知识点共同出现的情境（综合题）/ 该知识点自己的情境（单点题）
        if user_scenes:
            cands = [user_scenes[i % len(user_scenes)]]
        else:
            shared = g.shared_contexts(kp_ids[0], kp_ids[1]) if len(kp_ids) > 1 else g.own_contexts(kp_ids[0])
            cands = [c for c in shared if c in contexts]
            if not cands:
                cands = sorted(contexts)[:3]
        scene = _pick_scene(cands, used_scene)
        used_scene[scene] += 1
        scene_cands[spec_id] = cands[:5]
        ctx = contexts.get(scene)
        scene_hint = ""
        if ctx:
            rng = "，".join(f"{k}={v}" for k, v in ctx.number_range.items())
            scene_hint = f"典型量：{'、'.join(ctx.typical_quantities[:5])}" + (
                f"；该年级数值范围：{rng}" if rng else ""
            )
        archetype_ids: list[str] = []
        for k in kp_ids[:2]:
            ats = [
                a
                for a in await kb.archetypes_for(k, limit=8 if template else 4)
                if (a.figure_ratio or 0) < 0.5
            ]
            if template:
                ats = [
                    a for a in ats if a.verifiable_type == "program" and a.kp_id == k
                ]  # 只用主知识点匹配的题型
                ats.sort(key=lambda a: abs(a.difficulty - ladder[i]))
            archetype_ids += [a.id for a in ats[: 4 if template else 2]]
        items.append(
            ItemSpec(
                id=spec_id,
                index=i + 1,
                kp_ids=kp_ids,
                kp_names=[g.nodes[k].name for k in kp_ids],
                tier=tier,
                difficulty=ladder[i],
                kind=kind,
                scene=scene,
                scene_hint=scene_hint,
                number_hint=_boundary_hint(boundary),
                source="template" if template else "novel",
                archetype_ids=archetype_ids,
                review=review and tier == Tier.integrated,
                rationale=combo.rationale if combo else "",
            )
        )
    if len(scope.pool) < count and not scope.anchors:
        notes.append("范围内的知识点不多，部分题目会从不同角度考查同一知识点")
    bp = Blueprint(
        lesson_id=scope.lesson_id,
        grade=grade,
        items=items,
        method="rules",
        notes=notes,
        source=brief.source.value,
    )
    return Draft(bp, alts, scene_cands, contexts, boundary)


def replacement_spec(spec: ItemSpec, *, keep_kinds: bool) -> ItemSpec:
    """题被丢弃后的替换规格：改成更稳妥的写法再来一次——只考第一个知识点、答案用数值、换一个考法。
    教师明说了题型就保持；`template` 来源的题改由模型原创（题型卡片已经证明不可用）。"""
    kind = spec.kind
    if not keep_kinds and kind in (ItemKind.fill, ItemKind.judge, ItemKind.choice, ItemKind.open):
        kind = ItemKind.application
    return spec.model_copy(
        update={
            "id": f"{spec.id}r",
            "kp_ids": spec.kp_ids[:1],
            "kp_names": spec.kp_names[:1],
            "roles": {},
            "tier": Tier.consolidate if spec.tier == Tier.integrated else spec.tier,
            "kind": kind,
            "source": "novel",
            "archetype_ids": [],
            "review": False,
            "angle": "换一个与常见写法不同的考法；条件和问题都用具体数值，答案是数值，题面只问一个问题",
        }
    )

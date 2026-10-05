"""意图理解的后处理：原始解析 → 带来源标记的 Brief。

全部是确定性代码（不调用模型）：知识点映射、年级推断、单元与目标课时解析、默认值与"本次假设"、
澄清判断（范围缺失 / 范围冲突）、上一轮范围的继承、展示用芯片。把这些放在代码里而不是提示词里，
是因为它们需要可复现、可测试，并且规则与模型两条解析路径共用。
"""

from __future__ import annotations

from typing import TypeVar

from ..domain.brief import Action, Brief, Origin, PaperSpec, Scope, Slot, SourceMode
from ..domain.knowledge import KPHit
from ..domain.paper import ItemKind, Tier
from ..domain.understanding import Chip, ClarifyOption, ClarifyRequest, Method, RawParse, Route, Understanding
from ..knowledge import KnowledgeService

DEFAULT_COUNT = 5
DEFAULT_DIFFICULTY = [2, 4]
DEFAULT_TIER_MIX = {Tier.consolidate: 0.3, Tier.variation: 0.4, Tier.integrated: 0.3}
MAP_REL = 0.6  # 保留得分不低于最高分 60% 的命中
MAP_MIN = 0.35  # 绝对下限：低于它的命中不当作知识点映射
MAP_MAX = 3
CONFLICT_GAP = 2  # 用户年级比知识点年级低多少年才算范围冲突

_GRADE_CN = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六"}
_KIND_CN = {
    ItemKind.fill: "填空题",
    ItemKind.choice: "选择题",
    ItemKind.calc: "计算题",
    ItemKind.judge: "判断题",
    ItemKind.application: "应用题",
    ItemKind.open: "开放题",
}
_TIER_CN = {Tier.consolidate: "巩固", Tier.variation: "变式", Tier.integrated: "综合"}
_SEM_CN = {"a": "上册", "b": "下册"}


T = TypeVar("T")


def slot(value: T, origin: Origin) -> Slot[T]:
    return Slot[T](value=value, origin=origin)


def grade_text(grade: int, semester: str | None) -> str:
    return f"{_GRADE_CN[grade]}年级" + (_SEM_CN[semester] if semester else "")


def map_topics(hits: list[KPHit]) -> list[KPHit]:
    """把检索命中筛成知识点映射：相对最高分保留，设绝对下限与数量上限。"""
    if not hits:
        return []
    top = max(h.score for h in hits)
    kept = [h for h in hits if h.score >= max(MAP_MIN, top * MAP_REL)]
    return kept[:MAP_MAX]


def _origin(field: str, raw: RawParse) -> Origin:
    return Origin.user if field in raw.explicit else Origin.inferred


def scope_question() -> ClarifyRequest:
    return ClarifyRequest(
        prompt="想给几年级出题？也可以直接告诉我具体的单元或知识点。",
        options=[ClarifyOption(id=f"g{g}", label=f"{_GRADE_CN[g]}年级") for g in range(1, 7)],
    )


def conflict_question(topic: str, kp_grade: int, grade: int) -> ClarifyRequest:
    return ClarifyRequest(
        prompt=f"“{topic}”是{_GRADE_CN[kp_grade]}年级才学的内容，而您说的是{_GRADE_CN[grade]}年级。想怎么处理？",
        options=[
            ClarifyOption(id=f"g{kp_grade}", label=f"按{_GRADE_CN[kp_grade]}年级出"),
            ClarifyOption(id="keep_grade", label=f"保留{_GRADE_CN[grade]}年级，换成该年级的内容"),
        ],
    )


async def finalize(
    raw: RawParse,
    *,
    hits: list[KPHit],
    kb: KnowledgeService,
    prev: Brief | None = None,
    method: Method = "llm",
) -> Understanding:
    """把 `RawParse` 加工成 `Understanding`。`hits` 是对用户原话的知识点检索结果。"""
    route = raw.route
    if route in (Route.offtopic, Route.export, Route.ask):
        return Understanding(route=route, reason=raw.reason, method=method)
    if route == Route.edit:
        return Understanding(route=route, edit=raw.edit, reason=raw.reason, method=method)

    notes: list[str] = []
    assumptions: list[str] = []
    mapped = map_topics(hits) if raw.topics else []

    # ---- 年级 / 学期 / 单元 ----
    grade, grade_origin = raw.grade, _origin("grade", raw)
    semester, sem_origin = raw.semester, _origin("semester", raw)
    if grade is None and mapped and mapped[0].grade:
        grade, grade_origin = mapped[0].grade, Origin.inferred
        assumptions.append(f"按“{mapped[0].name}”所在的{_GRADE_CN[grade]}年级处理")
    unit_ids: list[str] = []
    if raw.unit_ordinals and grade and semester:
        for n in raw.unit_ordinals:
            u = await kb.unit_by_ordinal(kb.book_id(grade, semester), n)
            if u:
                unit_ids.append(u.id)
            else:
                notes.append(f"没有找到{grade_text(grade, semester)}的第{n}单元")

    # ---- 继承上一轮的范围（只继承范围，不继承题量 / 难度等） ----
    inherited = False
    if prev and grade is None and not mapped and not unit_ids:
        ps = prev.scope
        if ps.grade or ps.kp_ids:
            inherited = True
            grade = ps.grade.value if ps.grade else None
            semester = ps.semester.value if ps.semester else semester
            sem_origin = Origin.inferred
            grade_origin = Origin.inferred
            unit_ids = ps.units.value if ps.units else []
            if ps.kp_ids:
                mapped = [KPHit(id=k, name=k) for k in ps.kp_ids.value]
            assumptions.append("沿用上一轮的范围")

    # ---- 澄清（确定性规则）----
    clarify: ClarifyRequest | None = None
    if grade is None and not mapped and not unit_ids:
        clarify = scope_question()
    elif raw.grade and raw.topics and "grade" in raw.explicit and not inherited:
        # 范围冲突：用户明说的年级，比"该主题在教材里最早出现的年级"低 2 年以上。
        # 用知识库里所有匹配该主题的知识点的最早年级来判断，而不是看检索命中了哪几条
        # （跨年级的主题，检索可能只命中高年级的，会误报；命中里混着低年级的相近知识点，又会漏报）。
        floors = [f for t in raw.topics if (f := await kb.topic_floor(t))]
        if floors:
            grade_floor, kp_name = max(floors)  # 多个主题时取最严格的那个
            if grade_floor - raw.grade >= CONFLICT_GAP:
                clarify = conflict_question(kp_name, grade_floor, raw.grade)

    # ---- 目标课时：学生"学到哪" ----
    target: str | None = None
    if unit_ids and grade and semester:
        us = await kb.units(kb.book_id(grade, semester))
        last = next((u.last_lesson_id for u in us if u.id == unit_ids[-1]), None)
        target = last
    elif mapped and not inherited:
        target = await kb.latest_lesson([h.lesson_id for h in mapped if h.lesson_id])
    elif grade and semester:
        target = await kb.last_lesson(kb.book_id(grade, semester))
    elif grade:
        target = await kb.last_lesson(kb.book_id(grade, "b"))
    elif prev and prev.target_lesson:
        target = prev.target_lesson.value

    # ---- 组装 Brief ----

    scope = Scope(
        grade=slot(grade, grade_origin) if grade else None,
        semester=slot(semester, sem_origin) if semester else None,
        units=slot(unit_ids, Origin.user) if unit_ids else None,
        kp_ids=slot([h.id for h in mapped], Origin.inferred) if mapped else None,
        topics=slot(raw.topics, Origin.user) if raw.topics else None,
    )
    brief = Brief(scope=scope)
    action = Action(raw.action)
    brief.action = slot(action, Origin.user if raw.action != "generate" else Origin.default)
    if route == Route.paper:
        brief.action = slot(Action.paper, Origin.user)
        assumptions.append("整卷会分阶段生成：先确认蓝图，再出样题，最后成卷")
    if target:
        brief.target_lesson = slot(target, Origin.inferred)

    if raw.count:
        brief.count = slot(raw.count, _origin("count", raw))
    elif route == Route.generate:
        brief.count = slot(DEFAULT_COUNT, Origin.default)
        assumptions.append(f"题量未说明，先按 {DEFAULT_COUNT} 道出")
    if raw.difficulty and len(raw.difficulty) == 2:
        brief.difficulty = slot(
            [max(1, min(5, raw.difficulty[0])), max(1, min(5, raw.difficulty[1]))], _origin("difficulty", raw)
        )
    else:
        brief.difficulty = slot(DEFAULT_DIFFICULTY, Origin.default)
        assumptions.append("难度未说明，按中等并带梯度出")
    if raw.kinds:
        brief.kinds = slot(raw.kinds, _origin("kinds", raw))
    if raw.tier:
        brief.tier_mix = slot({raw.tier: 1.0}, _origin("tier", raw))
    else:
        brief.tier_mix = slot(dict(DEFAULT_TIER_MIX), Origin.default)
    if raw.source != SourceMode.auto:
        brief.source = slot(raw.source, _origin("source", raw))
    if raw.scenes:
        brief.scenes = slot(raw.scenes, _origin("scenes", raw))
    if raw.constraints:
        brief.constraints = slot(raw.constraints, _origin("constraints", raw))
    if raw.paper:
        brief.paper = raw.paper
    elif route == Route.paper:
        brief.paper = PaperSpec()
    if action == Action.review:
        assumptions.append("按螺旋复习处理：会把前面学过的内容穿插进来")
    brief.assumptions = assumptions

    return Understanding(
        route=route,
        brief=brief,
        clarify=clarify,
        reason=raw.reason,
        chips=await _chips(brief, mapped, kb),
        method=method,
        notes=notes,
    )


async def _chips(brief: Brief, mapped: list[KPHit], kb: KnowledgeService) -> list[Chip]:
    s, chips = brief.scope, []

    def add(key: str, label: str, value: str, origin: Origin) -> None:
        chips.append(Chip(key=key, label=label, value=value, origin=origin.value))

    if s.grade:
        add(
            "grade",
            "年级",
            grade_text(s.grade.value, s.semester.value if s.semester else None),
            s.grade.origin,
        )
    if s.units and s.grade and s.semester:
        us = {u.id: u for u in await kb.units(kb.book_id(s.grade.value, s.semester.value))}
        add(
            "unit",
            "单元",
            "、".join(us[u].title.split(" ", 1)[-1] if u in us else u for u in s.units.value),
            s.units.origin,
        )
    if mapped:
        add("topics", "知识点", "、".join(h.name for h in mapped[:2]), Origin.inferred)
    if brief.count:
        add("count", "题量", f"{brief.count.value} 道", brief.count.origin)
    if brief.difficulty:
        lo, hi = brief.difficulty.value
        add(
            "difficulty",
            "难度",
            {1: "很简单", 2: "简单", 3: "中等", 4: "较难", 5: "很难"}[lo]
            + ("" if lo == hi else f" ~ {({1: '很简单', 2: '简单', 3: '中等', 4: '较难', 5: '很难'})[hi]}"),
            brief.difficulty.origin,
        )
    if brief.kinds:
        add("kinds", "题型", "、".join(_KIND_CN[k] for k in brief.kinds.value), brief.kinds.origin)
    if brief.tier_mix and brief.tier_mix.origin != Origin.default:
        add("tier", "档位", "、".join(_TIER_CN[t] for t in brief.tier_mix.value), brief.tier_mix.origin)
    if brief.scenes:
        add("scenes", "情境", "、".join(brief.scenes.value), brief.scenes.origin)
    if brief.constraints:
        add("constraints", "限制", "；".join(brief.constraints.value), brief.constraints.origin)
    return chips

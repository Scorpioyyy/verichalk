"""规划的"代码管约束、模型管设计"（D59）：模型的整套设计合并进确定性骨架时，约束由代码保证；候选菜单按本册优先、只含已学。"""

from __future__ import annotations

import pytest

from verichalk.domain.blueprint import Blueprint, ItemSpec
from verichalk.domain.brief import Brief, Origin, Scope, Slot
from verichalk.domain.paper import Tier
from verichalk.knowledge import ComboMiner, ComboWeights, GraphData, KnowledgeService
from verichalk.knowledge.graph import KPNode
from verichalk.stages.plan import ComposeItem, ComposeOut, merge_compose
from verichalk.stages.plan_rules import MenuEntry, build_menu, resolve_scope


def _graph(*ids: str) -> GraphData:
    nodes = {
        k: KPNode(
            id=k,
            name=f"名{k}",
            grade=3,
            semester="b",
            domain="na",
            thread="t",
            topic="x",
            position=i,
            unit_id="u",
            lesson_id="l",
        )
        for i, k in enumerate(ids)
    }
    return GraphData(nodes=nodes)


def _bp(*tiers: Tier) -> Blueprint:
    return Blueprint(
        items=[
            ItemSpec(
                id=f"it{i}",
                index=i,
                kp_ids=["d1", "d2"] if t == Tier.integrated else ["d1"],
                tier=t,
                scene="骨架",
            )
            for i, t in enumerate(tiers, 1)
        ]
    )


MENU = [
    MenuEntry("K1", "t1", "名t1", "本册", topical=True),
    MenuEntry("K2", "t2", "名t2", "本册", topical=True),
    MenuEntry("K3", "p1", "名p1", "旧知"),
]
G = _graph("t1", "t2", "p1", "d1", "d2")


def test_merge_keeps_menu_kps_and_guarantees_a_topic_kp() -> None:
    out = ComposeOut(
        items=[
            ComposeItem(
                index=1, kps=["K3"], scene="春游买门票", plan="【直接应用】…"
            ),  # 只选了搭档：换成主题知识点
            ComposeItem(
                index=2, kps=["K9", "k2"], scene="图书角", plan="【逆向】…"
            ),  # 不存在的代号忽略；大小写不敏感
            ComposeItem(index=3, kps=["K1", "K3"], scene="操场跑圈", plan="【多步综合】先…再…"),
        ]
    )
    bp = merge_compose(_bp(Tier.consolidate, Tier.variation, Tier.integrated), out, MENU, G, Brief())
    a, b, c = bp.items
    assert a.kp_ids == ["t1"] and a.rationale == "按教师点名的主题替换"
    assert b.kp_ids == ["t2"]
    assert c.kp_ids == ["t1", "p1"] and c.tier == Tier.integrated
    assert [it.kp_names for it in bp.items] == [
        ["名t1"],
        ["名t2"],
        ["名t1", "名p1"],
    ]  # 名称来自图，不会漏出 ID
    assert (a.scene, a.angle, a.scene_hint) == ("春游买门票", "【直接应用】…", "")
    assert bp.method == "llm"


def test_integrated_slot_without_a_pair() -> None:
    out = ComposeOut(items=[ComposeItem(index=1, kps=["K1"], plan="…")])
    free = merge_compose(_bp(Tier.integrated), out, MENU, G, Brief())
    assert free.items[0].tier == Tier.variation and free.items[0].kp_ids == ["t1"]  # 没要求结合：降为单知识点
    combine = Brief(scope=Scope(kp_topics={"t1": "乘法", "t2": "周长"}))
    kept = merge_compose(_bp(Tier.integrated), out, MENU, _graph("t1", "t2", "p1", "d1", "d2"), combine)
    # 教师明说要结合：保留骨架的搭配（骨架里没有主题知识点时由代码换上一个）
    assert kept.items[0].tier == Tier.integrated and len(kept.items[0].kp_ids) == 2


def test_teacher_scenes_and_template_items_are_not_rewritten() -> None:
    bp = _bp(Tier.consolidate, Tier.consolidate)
    bp.items[1].source = "template"
    brief = Brief(scenes=Slot(value=["小动物"], origin=Origin.user))
    out = ComposeOut(items=[ComposeItem(index=1, kps=["K1"], scene="超市"), ComposeItem(index=2, kps=["K2"])])
    got = merge_compose(bp, out, MENU, G, brief)
    assert got.items[0].scene == "骨架"
    assert got.items[1].kp_ids == ["d1"]


@pytest.fixture(scope="module")
def kb() -> KnowledgeService:
    from chalkbase import Curriculum

    return KnowledgeService(Curriculum())  # pyright: ignore[reportCallIssue]


async def test_menu_is_learned_only_and_prefers_the_target_book(kb: KnowledgeService) -> None:
    g = await kb.graph()
    brief = Brief(
        scope=Scope(
            grade=Slot(value=3, origin=Origin.user),
            semester=Slot(value="a", origin=Origin.user),
            topics=Slot(value=["时间"], origin=Origin.user),
        )
    )
    scope = await resolve_scope(brief, kb)
    menu = await build_menu(brief, kb, ComboMiner(g, ComboWeights()), scope)
    assert menu and all(m.kp_id in scope.learned for m in menu)
    assert [m.code for m in menu] == [f"K{i}" for i in range(1, len(menu) + 1)]
    topical = [g.nodes[m.kp_id] for m in menu if m.topical]
    assert topical and (topical[0].grade, topical[0].semester) == (3, "a")  # "三上的时间"先给本册的内容
    assert any(m.where.startswith("本册") for m in menu)


async def test_menu_keeps_figure_only_topics_and_drops_unrelated_hits(kb: KnowledgeService) -> None:
    # "认识钟表""搭配问题"在教材里只有看图题：仍然是老师点名的主题（标注出来），而不是被换成检索到的无关知识点
    g = await kb.graph()
    for anchor, grade, sem, topic in [
        ("kp.na.认识时间与年月日.hour_halfhour", 1, "a", "认识钟表"),
        ("kp.na.数量关系.combination_counting", 3, "a", "搭配问题"),
    ]:
        brief = Brief(
            scope=Scope(
                grade=Slot(value=grade, origin=Origin.user),
                semester=Slot(value=sem, origin=Origin.user),
                topics=Slot(value=[topic], origin=Origin.user),
                kp_ids=Slot(value=[anchor], origin=Origin.inferred),
            )
        )
        scope = await resolve_scope(brief, kb)
        menu = await build_menu(brief, kb, ComboMiner(g, ComboWeights()), scope)
        topical = [m for m in menu if m.topical]
        assert topical and topical[0].kp_id == anchor and "配图" in topical[0].note
        assert all(g.nodes[m.kp_id].thread == g.nodes[anchor].thread for m in topical)

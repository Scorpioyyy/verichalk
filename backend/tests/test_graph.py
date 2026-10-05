"""组合挖掘的不变量（plan.md P1）：合法、确定、多样；用合成小图单测，并用真实知识库抽查范围合法性。"""

from __future__ import annotations

import pytest

from verichalk.knowledge import ComboMiner, GraphData, KPNode


def node(i: str, *, grade: int = 4, topic: str = "t", unit: str = "u1", pos: int = 0) -> KPNode:
    return KPNode(
        id=i,
        name=i,
        grade=grade,
        semester="a",
        domain="na",
        thread=i[0],
        topic=topic,
        position=pos,
        unit_id=unit,
        lesson_id="l",
    )


def small_graph() -> GraphData:
    g = GraphData({k: node(k, pos=n) for n, k in enumerate("abcdef")})
    g.add_edge("a", "b", "prerequisite", directed=True)
    g.add_edge("a", "c", "related", directed=False)
    g.add_edge("b", "d", "builds_on", directed=True)
    g.add_cooccur(["a", "e"])
    g.add_cooccur(["a", "e", "f"])
    return g


def test_members_stay_in_scope() -> None:
    g = small_graph()
    for combo in ComboMiner(g).mine(["a"], {"a", "b", "c"}, k=5):
        assert set(combo.kp_ids) <= {"a", "b", "c"}


def test_anchor_outside_scope_is_ignored() -> None:
    assert ComboMiner(small_graph()).mine(["a"], {"b", "c"}) == []


def test_deterministic_and_unique() -> None:
    g = small_graph()
    a = ComboMiner(g).mine(["a"], set("abcdef"), k=5, size=3)
    b = ComboMiner(g).mine(["a"], set("abcdef"), k=5, size=3)
    assert a == b
    assert len({frozenset(c.kp_ids) for c in a}) == len(a)


def test_exclude_and_reuse_limit() -> None:
    g = small_graph()
    miner = ComboMiner(g)
    first = miner.mine(["a"], set("abcdef"), k=1)[0]
    again = miner.mine(["a"], set("abcdef"), k=3, exclude=[tuple(first.kp_ids)])
    assert all(set(c.kp_ids) != set(first.kp_ids) for c in again)
    for p in "bcdef":
        assert sum(p in c.kp_ids for c in miner.mine(["a"], set("abcdef"), k=8)) <= 2


def test_cooccur_signal_ranks_first() -> None:
    g = small_graph()
    top = ComboMiner(g).mine(["a"], set("abcdef"), k=1)[0]
    assert "教材有" in top.rationale or "前置" in top.rationale


def test_topic_mode() -> None:
    g = GraphData({"a": node("a", topic="x"), "b": node("b", topic="x"), "c": node("c", topic="y")})
    g.add_edge("a", "b", "prerequisite", directed=True)
    g.add_edge("a", "c", "related", directed=False)
    miner = ComboMiner(g)
    assert [c.kp_ids for c in miner.mine(["a"], set("abc"), topic="in_topic")] == [["a", "b"]]
    assert [c.kp_ids for c in miner.mine(["a"], set("abc"), topic="cross_topic")] == [["a", "c"]]


@pytest.mark.parametrize("lesson", ["g3a.u3.l01", "g4b.u3.l01", "g6a.u4.l01"])
async def test_real_graph_scope_legality(lesson: str) -> None:
    """真实知识库：每个组合的所有成员都在目标课时之前已学（P1 硬不变量）。"""
    from chalkbase import Curriculum

    from verichalk.knowledge import KnowledgeService

    kb = KnowledgeService(Curriculum())  # pyright: ignore[reportCallIssue]
    g = await kb.graph()
    scope = await kb.learned_before(lesson, inclusive=True)
    miner = ComboMiner(g)
    for anchor in sorted(scope)[:40]:
        for combo in miner.mine([anchor], scope, k=4, size=3):
            assert set(combo.kp_ids) <= scope

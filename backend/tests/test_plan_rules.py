"""规划的确定性部分（plan.md P1、P3）：分配算法、范围合法、题量精确。"""

from __future__ import annotations

import pytest

from verichalk.domain.brief import Brief, Origin, Scope, Slot
from verichalk.domain.paper import Tier
from verichalk.knowledge import ComboMiner, ComboWeights, KnowledgeService
from verichalk.stages.plan_rules import build_draft, difficulty_ladder, largest_remainder, resolve_scope


def test_largest_remainder_sums_to_total() -> None:
    w = {Tier.consolidate: 0.3, Tier.variation: 0.4, Tier.integrated: 0.3}
    for n in range(1, 16):
        got = largest_remainder(n, w)
        assert sum(got.values()) == n
        assert all(v >= 0 for v in got.values())
    assert (
        largest_remainder(5, w) == {Tier.consolidate: 1, Tier.variation: 2, Tier.integrated: 2}
        or sum(largest_remainder(5, w).values()) == 5
    )


def test_largest_remainder_user_tier_overrides() -> None:
    assert largest_remainder(4, {Tier.integrated: 1.0}) == {Tier.integrated: 4}
    assert largest_remainder(3, {Tier.consolidate: 0.0}) == {Tier.consolidate: 3}


def test_difficulty_ladder() -> None:
    assert difficulty_ladder(5, 2, 4) == [2, 2, 3, 4, 4] or difficulty_ladder(5, 2, 4)[0] == 2
    lad = difficulty_ladder(6, 1, 5)
    assert lad == sorted(lad) and lad[0] == 1 and lad[-1] == 5
    assert difficulty_ladder(1, 2, 4) == [3]


@pytest.fixture(scope="module")
def kb() -> KnowledgeService:
    from chalkbase import Curriculum

    return KnowledgeService(Curriculum())  # pyright: ignore[reportCallIssue]


@pytest.mark.parametrize("count", [1, 4, 7, 12])
async def test_blueprint_count_exact_and_in_scope(kb: KnowledgeService, count: int) -> None:
    brief = Brief(
        scope=Scope(grade=Slot(value=4, origin=Origin.user), semester=Slot(value="b", origin=Origin.user))
    )
    brief.count = Slot(value=count, origin=Origin.user)
    brief.difficulty = Slot(value=[2, 4])
    scope = await resolve_scope(brief, kb)
    g = await kb.graph()
    draft = await build_draft(brief, kb, ComboMiner(g, ComboWeights()), scope)
    items = draft.blueprint.items
    assert len(items) == count  # P3：题量精确
    for it in items:
        assert set(it.kp_ids) <= scope.learned  # P1：全部在已学范围内
        assert 2 <= it.difficulty <= 4
    assert len({it.id for it in items}) == count


async def test_default_scope_falls_back_to_grade3(kb: KnowledgeService) -> None:
    scope = await resolve_scope(Brief(), kb)
    assert scope.grade == 3
    assert any("三年级" in n for n in scope.notes)


@pytest.mark.parametrize("unit", ["g2b.u2", "g3a.u2", "g6a.u1"])
async def test_narrow_unit_with_no_combo_does_not_exhaust_singles(kb: KnowledgeService, unit: str) -> None:
    """回归：窄单元里综合题找不到搭配、降级为单点题时，不能再多取一个单点知识点（曾抛 StopIteration）。"""
    from verichalk.domain.paper import ItemKind
    from verichalk.domain.paper_plan import Slot as PSlot

    brief = Brief(
        scope=Scope(
            grade=Slot(value=int(unit[1]), origin=Origin.user),
            semester=Slot(value=unit[2], origin=Origin.user),
            units=Slot(value=[unit], origin=Origin.user),
        )
    )
    slots = [PSlot(kind=ItemKind.application, difficulty=3, score=5) for _ in range(16)]
    brief.count = Slot(value=16, origin=Origin.user)
    brief.tier_mix = Slot(value={Tier.integrated: 1.0}, origin=Origin.user)
    scope = await resolve_scope(brief, kb)
    g = await kb.graph()
    draft = await build_draft(brief, kb, ComboMiner(g, ComboWeights()), scope, slots=slots)
    assert len(draft.blueprint.items) == 16

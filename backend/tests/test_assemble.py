"""装配与分值规则：追加不丢东西、替换可撤销、分值合计精确（eval/specs/edit.md 失败模式 9、10）。"""

from __future__ import annotations

import random
from typing import Any

import pytest

from verichalk.core.config import Settings
from verichalk.domain.blueprint import Blueprint
from verichalk.domain.paper import Item, ItemKind
from verichalk.domain.paper_rules import KIND_WEIGHT, default_score, distribute_scores, placement
from verichalk.orchestrator.papers import PaperService
from verichalk.stages import RunContext
from verichalk.stages.assemble import assemble
from verichalk.trace import MemorySink, Tracer, use_tracer


def it(i: int, kind: ItemKind = ItemKind.calc, **kw: Any) -> Item:
    return Item(id=f"n{i}", kind=kind, stem=f"题 {i}", answer=str(i), **kw)


def make_ctx(store, session_id: str, run_id: str = "run_t") -> RunContext:
    return RunContext(
        run_id=run_id,
        session_id=session_id,
        settings=Settings(),
        llm=None,  # type: ignore[arg-type]
        kb=None,  # type: ignore[arg-type]
        store=store,
    )


@pytest.fixture
async def ctx(store):
    ses = await store.sessions.create("t")
    c = make_ctx(store, ses.id)
    async with use_tracer(Tracer("run_t", MemorySink())):
        yield c


BP = Blueprint(grade=4, lesson_id="g4b.u1.l01")


async def test_new_then_append_keeps_existing_items_untouched(ctx) -> None:
    p1 = await assemble(ctx, [it(1), it(2)], BP, "new")
    assert p1 is not None and p1.meta == {"grade": 4, "lesson_id": "g4b.u1.l01"}
    before = [x.model_dump() for x in p1.all_items()]
    p2 = await assemble(ctx, [it(3)], BP, "append")
    assert p2 is not None and p2.id == p1.id and p2.rev == p1.rev + 1
    assert [x.model_dump() for x in p2.all_items()[:2]] == before  # 原有题逐字段不变（含 id、rev）
    assert [x.id for x in p2.all_items()] == ["n1", "n2", "n3"]
    h = await ctx.store.papers.list_revisions(ctx.session_id)
    assert [r.summary for r in h] == ["生成 2 道题", "追加 1 道题"]


async def test_replace_swaps_all_items_and_is_undoable(ctx) -> None:
    await assemble(ctx, [it(1), it(2)], BP, "new")
    p = await assemble(ctx, [it(7)], BP, "replace")
    assert p is not None and [x.id for x in p.all_items()] == ["n7"]
    svc = PaperService(ctx.settings, ctx.store)
    await svc.undo(ctx.session_id)
    back = await ctx.store.papers.get_current(ctx.session_id)
    assert back is not None and [x.id for x in back.all_items()] == ["n1", "n2"]


async def test_append_gives_default_scores_only_on_scored_papers(ctx) -> None:
    await assemble(ctx, [it(1, score=4.0)], BP, "new")
    p = await assemble(ctx, [it(2, ItemKind.choice)], BP, "append")
    assert p is not None and p.all_items()[1].score == default_score(ItemKind.choice)
    # 没有分值的试卷：追加不凭空加分值
    ses2 = await ctx.store.sessions.create("t2")
    ctx2 = make_ctx(ctx.store, ses2.id, "r2")
    await assemble(ctx2, [it(5)], BP, "new")
    p2 = await assemble(ctx2, [it(6)], BP, "append")
    assert p2 is not None and all(x.score is None for x in p2.all_items())


async def test_no_items_is_noop(ctx) -> None:
    assert await assemble(ctx, [], BP, "new") is None


@pytest.mark.parametrize(
    ("text", "has", "expect"),
    [
        ("出5道四年级小数加减法", False, "new"),
        ("再来3道", True, "append"),
        ("出5道圆的面积", True, "append"),
        ("重新出一套", True, "replace"),
        ("换一批题", True, "replace"),
        ("全部换掉重来", True, "replace"),
        ("把第3题换个场景", True, "append"),  # 编辑不走这里；出题类请求默认追加
    ],
)
def test_placement(text: str, has: bool, expect: str) -> None:
    assert placement(text, has) == expect


@pytest.mark.parametrize("seed", range(200))
def test_distribute_scores_sums_exactly(seed: int) -> None:
    rnd = random.Random(seed)
    n = rnd.randint(1, 40)
    kinds = [rnd.choice(list(KIND_WEIGHT)) for _ in range(n)]
    total = rnd.randint(n, 150)
    scores = distribute_scores(kinds, total)
    assert sum(scores) == total and all(s >= 1 for s in scores) and len(scores) == n


def test_distribute_scores_reasonable_shape_and_errors() -> None:
    kinds = [ItemKind.choice] * 10 + [ItemKind.fill] * 5 + [ItemKind.application] * 4
    s = distribute_scores(kinds, 100)
    assert sum(s) == 100 and max(s[-4:]) > max(s[:10])  # 应用题分值高于选择题
    with pytest.raises(ValueError):
        distribute_scores([ItemKind.calc] * 5, 3)
    assert distribute_scores([], 100) == []

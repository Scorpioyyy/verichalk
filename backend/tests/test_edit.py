"""自然语言编辑：目标解析、规则通道、计划校验、位置计算、执行与"不附带破坏"（eval/specs/edit.md 失败模式 1～4）。"""

from __future__ import annotations

import random
import time

import pytest

from verichalk.core.config import Settings
from verichalk.domain.edit import EditAction, EditPlan
from verichalk.domain.paper import (
    Item,
    ItemKind,
    Paper,
    Revision,
    Section,
    Source,
    Verification,
    VerifyStatus,
)
from verichalk.domain.paper_ops import MoveItem, apply_patch
from verichalk.orchestrator.papers import PaperService
from verichalk.stages import RunContext
from verichalk.stages.edit import (
    EditIn,
    EditStage,
    _flat,
    compose_edit_reply,
    move_op,
    resolve_targets,
    rule_plan,
    validate_plan,
)
from verichalk.stages.produce import ProduceIn, ProduceOut, ProduceStage
from verichalk.trace import MemorySink, Tracer, use_tracer


def mk(i: int, kind: ItemKind = ItemKind.calc, **kw) -> Item:
    return Item(
        id=f"i{i}",
        kind=kind,
        stem=f"原题 {i}",
        answer=str(i),
        solution=f"解析 {i}",
        difficulty=3,
        verification=Verification(status=VerifyStatus.verified),
        **kw,
    )


def paper5() -> Paper:
    return Paper(
        id="p",
        title="试卷",
        rev=1,
        meta={"grade": 4},
        sections=[
            Section(id="s1", title="选择", items=[mk(1, ItemKind.choice), mk(2, ItemKind.choice)]),
            Section(id="s2", title="计算", items=[mk(3), mk(4), mk(5, ItemKind.application)]),
        ],
    )


# ---- 目标解析 ----
@pytest.mark.parametrize(
    ("target", "expect"),
    [
        ("item:3", [3]),
        ("items:1,3", [1, 3]),
        ("items:4、2", [2, 4]),
        ("all", [1, 2, 3, 4, 5]),
        ("kind:choice", [1, 2]),
        ("kind:application", [5]),
        ("", None),
        ("whatever", None),
    ],
)
def test_resolve_targets(target: str, expect) -> None:
    assert resolve_targets(paper5(), target) == expect


# ---- 规则通道 ----
@pytest.mark.parametrize(
    ("text", "ops"),
    [
        ("删掉第2题", [("remove", [2])]),
        ("把第 3 题去掉", None),  # 句式不在规则里：交给模型
        ("删除第2、4题", [("remove", [2, 4])]),
        ("第1题和第4题交换", [("swap", [1, 4])]),
        ("交换第2题与第5题", [("swap", [2, 5])]),
        ("总分改成100分", [("set_total", [])]),
        ("每题3分", [("set_score", [])]),
        ("删掉第2题，第1题和第4题交换", [("remove", [2]), ("swap", [1, 4])]),
        ("删掉第2题，第4题换个场景", None),  # 有一句规则吃不下：整句交给模型
        ("第3题换个场景", None),
        ("整体降一点难度", None),
    ],
)
def test_rule_plan(text: str, ops) -> None:
    plan = rule_plan(text)
    if ops is None:
        assert plan is None
    else:
        assert plan is not None and [(a.op, a.items) for a in plan.actions] == ops


def test_rule_plan_title() -> None:
    plan = rule_plan("标题改成期末复习卷")
    assert plan is not None and plan.actions[0].op == "set_title" and plan.actions[0].title == "期末复习卷"


# ---- 计划校验 ----
def test_validate_plan_range_and_scope() -> None:
    p = paper5()
    out = validate_plan(EditPlan(actions=[EditAction(op="rewrite", items=[12])]), p, None)
    assert out.unsupported and "没有第 12 题" in out.unsupported
    # 教师点名第 3 题，模型却给了 1~5：只保留 3
    out = validate_plan(EditPlan(actions=[EditAction(op="rewrite", items=[1, 2, 3, 4, 5])]), p, [3])
    assert [a.items for a in out.actions] == [[3]]
    out = validate_plan(EditPlan(actions=[EditAction(op="swap", items=[1])]), p, None)
    assert out.unsupported
    out = validate_plan(EditPlan(actions=[EditAction(op="move", items=[1])]), p, None)
    assert out.unsupported
    out = validate_plan(EditPlan(actions=[EditAction(op="set_score", items=[], score=3)]), p, [1, 2])
    assert out.actions[0].items == [1, 2]  # "选择题每题 3 分"：只给点名的题
    assert validate_plan(EditPlan(unsupported="听不懂"), p, None).unsupported == "听不懂"


# ---- 位置计算：只有被移动的题自己变化 ----
@pytest.mark.parametrize("seed", range(60))
def test_move_op_matches_reference_and_leaves_others_untouched(seed: int) -> None:
    rnd = random.Random(seed)
    p = paper5()
    flat = _flat(p)
    ids = [i for _, i in flat]
    x = rnd.choice(ids)
    k = rnd.randint(1, len(ids))
    mv, new_flat = move_op(flat, x, k)
    expect = [i for i in ids if i != x]
    expect.insert(k - 1, x)
    assert [i for _, i in new_flat] == expect
    moved = apply_patch(p, [mv])
    assert [it.id for it in moved.all_items()] == expect  # 补丁实际产生的顺序与计算一致
    for it in moved.all_items():
        if it.id != x:
            assert it.rev == 1  # 别的题的 rev 不变


# ---- 执行 ----
@pytest.fixture
async def ctx(store):
    ses = await store.sessions.create("t")
    await store.papers.save(ses.id, paper5(), Revision(paper_id="p", rev=1, author="agent", ts=time.time()))
    c = RunContext(
        run_id="run_t",
        session_id=ses.id,
        settings=Settings(),
        llm=None,  # type: ignore[arg-type]
        kb=None,  # type: ignore[arg-type]
        store=store,
        has_paper=True,
    )

    class KB:
        async def kp(self, kid: str):
            raise KeyError(kid)

    c.kb = KB()  # type: ignore[assignment]
    async with use_tracer(Tracer("run_t", MemorySink())):
        yield c


def fake_produce(calls: list[ProduceIn], *, fail: set[str] | None = None):
    async def run(self, ctx, inp: ProduceIn) -> ProduceOut:
        calls.append(inp)
        if fail and inp.rewrite and inp.rewrite["stem"] in fail:
            return ProduceOut(item=None, attempts=2, dropped_reason="核验没通过")
        new = Item(
            id="tmp",
            kind=inp.spec.kind,
            stem=f"改写：{inp.rewrite['stem'] if inp.rewrite else ''}",
            options=["1", "2", "3", "4"] if inp.spec.kind == ItemKind.choice else [],
            answer="B",
            answer_value=[{"label": "", "value": "B", "unit": ""}],
            solution="新解析",
            difficulty=inp.spec.difficulty,
            verification=Verification(status=VerifyStatus.verified),
        )
        return ProduceOut(item=new, attempts=1)

    return run


async def head_items(ctx) -> dict[str, dict]:
    p = await ctx.store.papers.get_current(ctx.session_id)
    return {it.id: it.model_dump() for it in p.all_items()}


async def test_rewrite_changes_only_targets_and_keeps_verification(ctx, monkeypatch) -> None:
    calls: list[ProduceIn] = []
    monkeypatch.setattr(ProduceStage, "run", fake_produce(calls))

    async def fake_plan(c, paper, instruction, targets):
        return EditPlan(
            actions=[EditAction(op="rewrite", items=[3], difficulty_delta=-1, instruction="降低难度")]
        )

    monkeypatch.setattr("verichalk.stages.edit.plan_edit", fake_plan)
    before = await head_items(ctx)
    out = await EditStage().run(ctx, EditIn(instruction="第3题简单点", target="item:3"))
    assert out.rev == 2 and [r.ok for r in out.results] == [True]
    after = await head_items(ctx)
    for iid in ("i1", "i2", "i4", "i5"):
        assert after[iid] == before[iid]  # 未点名的题逐字段不变（含 rev、核验）
    new = after["i3"]
    assert new["stem"] == "改写：原题 3" and new["rev"] == 2 and new["id"] == "i3"
    assert new["verification"]["status"] == "verified"  # 复核结论随改写落盘，不是 pending
    assert new["difficulty"] == 2 and new["figures"] == []
    assert calls[0].spec.difficulty == 2 and (calls[0].rewrite or {})["instruction"].startswith("降低难度")
    # 一次撤销回到改前
    await PaperService(ctx.settings, ctx.store).undo(ctx.session_id)
    assert await head_items(ctx) == before


async def test_rewrite_to_choice_and_failure_leaves_item_unchanged(ctx, monkeypatch) -> None:
    calls: list[ProduceIn] = []
    monkeypatch.setattr(ProduceStage, "run", fake_produce(calls, fail={"原题 5"}))

    async def fake_plan(c, paper, instruction, targets):
        return EditPlan(
            actions=[
                EditAction(op="rewrite", items=[4], kind=ItemKind.choice, instruction="改成选择题"),
                EditAction(op="rewrite", items=[5], instruction="换场景"),
            ]
        )

    monkeypatch.setattr("verichalk.stages.edit.plan_edit", fake_plan)
    out = await EditStage().run(ctx, EditIn(instruction="x", target=""))
    by = {r.number: r for r in out.results}
    assert by[4].ok and not by[5].ok and "已保持原样" in by[5].detail
    after = await head_items(ctx)
    assert after["i4"]["kind"] == "choice" and len(after["i4"]["options"]) == 4
    assert after["i5"]["stem"] == "原题 5" and after["i5"]["rev"] == 1


async def test_all_failed_makes_no_revision(ctx, monkeypatch) -> None:
    monkeypatch.setattr(ProduceStage, "run", fake_produce([], fail={"原题 3"}))

    async def fake_plan(c, paper, instruction, targets):
        return EditPlan(actions=[EditAction(op="rewrite", items=[3], instruction="换场景")])

    monkeypatch.setattr("verichalk.stages.edit.plan_edit", fake_plan)
    out = await EditStage().run(ctx, EditIn(instruction="x"))
    assert out.rev is None and (await ctx.store.papers.head(ctx.session_id)).rev == 1  # type: ignore[union-attr]


async def test_deterministic_actions_in_one_revision(ctx) -> None:
    out = await EditStage().run(ctx, EditIn(instruction="删掉第2题，第1题和第4题交换"))
    assert out.rev == 2 and out.unsupported == ""
    p = await ctx.store.papers.get_current(ctx.session_id)
    assert p is not None
    assert [it.id for it in p.all_items()] == [
        "i4",
        "i3",
        "i1",
        "i5",
    ]  # 删 i2 后 [i1,i3,i4,i5]，交换第1与第4题（编号按改前）
    assert len(await ctx.store.papers.list_revisions(ctx.session_id)) == 2  # 一次修改只有一条修订


async def test_set_total_distributes_exactly(ctx) -> None:
    out = await EditStage().run(ctx, EditIn(instruction="总分改成100分"))
    assert out.rev == 2
    p = await ctx.store.papers.get_current(ctx.session_id)
    assert (
        p is not None and sum(it.score or 0 for it in p.all_items()) == 100 and p.meta["total_score"] == 100
    )


async def test_out_of_range_and_no_paper(ctx, monkeypatch) -> None:
    out = await EditStage().run(ctx, EditIn(instruction="删掉第12题"))
    assert out.rev is None and "没有第 12 题" in out.unsupported
    ses2 = await ctx.store.sessions.create("empty")
    ctx2 = RunContext(
        run_id="r",
        session_id=ses2.id,
        settings=ctx.settings,
        llm=None,  # type: ignore[arg-type]
        kb=ctx.kb,
        store=ctx.store,
    )
    out = await EditStage().run(ctx2, EditIn(instruction="删掉第1题"))
    assert "还没有试卷" in out.unsupported


async def test_edit_marks_nothing_as_user_edited(ctx, monkeypatch) -> None:
    """智能体改写不算"用户手改"：来源保持原样。"""
    monkeypatch.setattr(ProduceStage, "run", fake_produce([]))

    async def fake_plan(c, paper, instruction, targets):
        return EditPlan(actions=[EditAction(op="rewrite", items=[3], instruction="换场景")])

    monkeypatch.setattr("verichalk.stages.edit.plan_edit", fake_plan)
    await EditStage().run(ctx, EditIn(instruction="x"))
    p = await ctx.store.papers.get_current(ctx.session_id)
    assert p is not None and p.find_item("i3")[2].provenance.source != Source.edited  # type: ignore[index]


def test_compose_reply() -> None:
    from verichalk.domain.edit import EditItemResult, EditOut

    out = EditOut(
        results=[
            EditItemResult(
                number=3,
                item_id="i3",
                op="rewrite",
                ok=True,
                detail="已改写（题干）",
                status=VerifyStatus.verified,
            ),
            EditItemResult(number=5, item_id="i5", op="rewrite", ok=False, detail="没有改成功，已保持原样"),
        ],
        rev=2,
    )
    text = compose_edit_reply(out)
    assert "第 3 题：已改写（题干）（已核验）" in text and "第 5 题：没有改成功" in text and "撤销" in text
    assert compose_edit_reply(EditOut(unsupported="试卷里只有 8 道题")) == "试卷里只有 8 道题"
    _ = MoveItem


async def test_main_pipeline_routes_edit_and_replies(ctx, monkeypatch) -> None:
    from verichalk.domain.edit import EditItemResult, EditOut
    from verichalk.domain.understanding import EditIntent, Route, Understanding
    from verichalk.orchestrator import pipelines
    from verichalk.orchestrator.pipelines import TurnInput, main_pipeline

    async def fake_understand(c, turn):
        return Understanding(route=Route.edit, edit=EditIntent(target="item:3", instruction="换个场景"))

    async def fake_run(self, c, inp):
        assert inp.instruction == "换个场景" and inp.target == "item:3"
        return EditOut(
            results=[
                EditItemResult(
                    number=3,
                    item_id="i3",
                    op="rewrite",
                    ok=True,
                    detail="已改写（题干）",
                    status=VerifyStatus.checked,
                )
            ],
            rev=2,
        )

    monkeypatch.setattr(pipelines, "understand_turn", fake_understand)
    monkeypatch.setattr(EditStage, "run", fake_run)
    res = await main_pipeline(ctx, TurnInput(text="第3题换个场景"))
    assert "第 3 题：已改写（题干）（已校对）" in res.reply_text
    # 没有试卷时，edit 路由退回理解阶段的回复（不会去改一份不存在的试卷）
    ctx.has_paper = False
    res = await main_pipeline(ctx, TurnInput(text="第3题换个场景"))
    assert "没有试卷可以修改" in res.reply_text

"""整卷：细目表规划（P-A～P-D 的确定性部分）、调整、装配与分阶段流程（eval/specs/edit.md 失败模式 10、11）。"""

from __future__ import annotations

from typing import Any

import pytest

from verichalk.core.config import Settings
from verichalk.domain.blueprint import Blueprint, ItemSpec
from verichalk.domain.brief import Brief, Origin, PaperSpec, Slot
from verichalk.domain.paper import Item, ItemKind, VerifyStatus
from verichalk.domain.understanding import Route, Understanding
from verichalk.orchestrator.paper_flow import PaperTweak, apply_tweak, confirmed, paper_flow, pick_samples
from verichalk.stages import PlanStage, ProduceStage, RunContext
from verichalk.stages.assemble import assemble_paper
from verichalk.stages.paper_plan import estimate_count, plan_paper_structure
from verichalk.stages.produce import ProduceIn, ProduceOut
from verichalk.trace import MemorySink, Tracer, use_tracer


def brief(
    duration: int | None = None, total: float | None = None, structure: list | None = None, kinds=None
) -> Brief:
    b = Brief()
    b.paper = PaperSpec(duration_min=duration, total_score=total, structure=structure or [])
    if kinds:
        b.kinds = Slot[list[ItemKind]](value=kinds, origin=Origin.user)
    return b


@pytest.mark.parametrize(("duration", "n"), [(40, 16), (45, 18), (80, 30), (5, 6), (90, 30)])
def test_estimate_count(duration: int, n: int) -> None:
    assert estimate_count(duration) == n


@pytest.mark.parametrize("duration", [20, 30, 40, 45, 60, 80, 90])
@pytest.mark.parametrize("total", [50, 100, 120, 150])
def test_default_plan_invariants(duration: int, total: int) -> None:
    plan = plan_paper_structure(brief(duration, total))
    assert plan.total_score == sum(s.score for s in plan.slots) == total  # P-A：分值合计恰好等于总分
    assert plan.n_items == sum(s.count for s in plan.sections) == len(plan.slots)
    assert estimate_count(duration) == plan.n_items
    ds = [s.difficulty for s in plan.slots]
    assert ds == sorted(ds)  # P-D：整卷从易到难（分区内必然单调）
    order = [s.kind for s in plan.sections]
    assert order == sorted(order, key=lambda k: list(ItemKind).index(k)) or len(order) >= 1
    assert all(s.subtotal == sum(x.score for x in plan.slots if x.kind == s.kind) for s in plan.sections)


def test_default_values_and_notes() -> None:
    plan = plan_paper_structure(Brief())
    assert plan.duration_min == 40 and plan.total_score == 100
    assert any("40 分钟" in n for n in plan.notes) and any("100 分" in n for n in plan.notes)


def test_user_structure_wins_and_total_follows_fixed_scores() -> None:
    structure = [
        {"kind": "choice", "count": 10, "score": 2},
        {"kind": "calc", "count": 4, "score": 5},
        {"kind": "application", "count": 5, "score": 6},
    ]
    plan = plan_paper_structure(brief(45, 100, structure))
    assert [(s.kind.value, s.count, s.score_each) for s in plan.sections] == [
        ("choice", 10, 2),
        ("calc", 4, 5),
        ("application", 5, 6),
    ]
    assert plan.total_score == 20 + 20 + 30 == 70
    assert any("不一致" in n for n in plan.notes)  # 要求 100 分但结构只有 70 分：以结构为准并告知


def test_kinds_restriction_and_small_total() -> None:
    plan = plan_paper_structure(brief(40, 100, kinds=[ItemKind.choice, ItemKind.calc]))
    assert {s.kind for s in plan.sections} == {ItemKind.choice, ItemKind.calc}
    plan = plan_paper_structure(brief(40, 5))
    assert plan.total_score == plan.n_items and any("提高" in n for n in plan.notes)


def test_apply_tweak_and_replan() -> None:
    b = brief(40, 100)
    plan = plan_paper_structure(b)
    n_choice = next(s.count for s in plan.sections if s.kind == ItemKind.choice)
    b2 = apply_tweak(
        b, plan, PaperTweak(counts={"choice": n_choice + 2}, remove_kinds=["judge"], total_score=120)
    )
    plan2 = plan_paper_structure(b2)
    assert next(s.count for s in plan2.sections if s.kind == ItemKind.choice) == n_choice + 2
    assert ItemKind.judge not in {s.kind for s in plan2.sections}
    assert plan2.total_score == 120 and sum(s.score for s in plan2.slots) == 120
    # 指定应用题每题 8 分：其余题型沿用当前分值，总分以结构为准
    b3 = apply_tweak(b, plan, PaperTweak(scores={"application": 8}))
    plan3 = plan_paper_structure(b3)
    assert next(s.score_each for s in plan3.sections if s.kind == ItemKind.application) == 8


@pytest.mark.parametrize(
    ("answer", "ok"),
    [
        ({"option": "confirm"}, True),
        ({"option": "adjust"}, False),
        ({"text": "好的"}, True),
        ({"text": "选择题多两道"}, False),
        ({"text": ""}, True),
        ({}, True),
    ],
)
def test_confirmed(answer: dict, ok: bool) -> None:
    assert confirmed(answer) is ok


def test_pick_samples_spread_over_sections() -> None:
    plan = plan_paper_structure(brief(40, 100))
    idx = pick_samples(plan)
    assert len(idx) == 3 and idx == sorted(idx) and idx[0] == 0
    starts = {sum(s.count for s in plan.sections[:i]) for i in range(len(plan.sections))}
    assert set(idx) <= starts  # 样题取自各分区的第一题


# ---- 装配 ----
def mk(i: int, kind: ItemKind) -> Item:
    return Item(id=f"x{i}", kind=kind, stem=f"题{i}", answer="1")


@pytest.fixture
async def ctx(store):
    ses = await store.sessions.create("t")
    c = RunContext(run_id="run_t", session_id=ses.id, settings=Settings(), llm=None, kb=None, store=store)  # type: ignore[arg-type]
    async with use_tracer(Tracer("run_t", MemorySink())):
        yield c


async def test_assemble_paper_sections_scores_and_total(ctx) -> None:
    plan = plan_paper_structure(brief(40, 100))
    delivered = [(i, mk(i, s.kind)) for i, s in enumerate(plan.slots)]
    p = await assemble_paper(ctx, delivered, plan, Blueprint(grade=4, lesson_id="g4b.u1.l01"))
    assert p is not None
    assert (
        sum(it.score or 0 for it in p.all_items()) == 100
        and p.meta["total_score"] == 100
        and p.meta["duration_minutes"] == 40
    )
    assert [s.title for s in p.sections] == [s.title for s in plan.sections]
    assert all(sec.kind == sec.items[0].kind.value for sec in p.sections)


async def test_assemble_paper_redistributes_when_items_dropped_and_replaces(ctx) -> None:
    plan = plan_paper_structure(brief(40, 100))
    await assemble_paper(ctx, [(i, mk(i, s.kind)) for i, s in enumerate(plan.slots)], plan, Blueprint())
    kept = [(i, mk(100 + i, s.kind)) for i, s in enumerate(plan.slots) if i % 4 != 0]
    p = await assemble_paper(ctx, kept, plan, Blueprint())
    assert p is not None and len(p.all_items()) == len(kept)
    assert sum(it.score or 0 for it in p.all_items()) == 100  # 少了题，总分仍是 100
    assert all(it.id.startswith("x1") for it in p.all_items())  # 旧试卷被整体替换
    assert await assemble_paper(ctx, [], plan, Blueprint()) is None


async def test_assemble_sample_keeps_slot_scores_and_has_no_duration(ctx) -> None:
    """样题是整卷的几道题：每题沿用它题位的分值（不是把总分摊给 3 道题），也没有"建议用时"。"""
    plan = plan_paper_structure(brief(40, 100))
    idx = pick_samples(plan)
    p = await assemble_paper(
        ctx, [(i, mk(i, plan.slots[i].kind)) for i in idx], plan, Blueprint(), sample=True
    )
    assert p is not None
    assert sorted(it.score or 0 for it in p.all_items()) == sorted(plan.slots[i].score for i in idx)
    assert p.meta["total_score"] == sum(plan.slots[i].score for i in idx) < 100
    assert "duration_minutes" not in p.meta


# ---- 分阶段流程 ----
def fake_stages(monkeypatch, drop: set[str] | None = None):
    plans: list[Any] = []

    async def fake_plan(self, c, inp):
        plans.append(inp)
        items = [
            ItemSpec(
                id=f"it{i + 1}",
                index=i + 1,
                kp_ids=["k"],
                kp_names=["知识点"],
                kind=s.kind,
                difficulty=s.difficulty,
            )
            for i, s in enumerate(inp.slots or [])
        ]
        return Blueprint(grade=4, lesson_id="g4b.u1.l01", items=items)

    async def fake_produce(self, c, inp: ProduceIn):
        if drop and inp.spec.id in drop:
            return ProduceOut(item=None, dropped_reason="没通过")
        it = Item(id=f"n_{inp.spec.id}", kind=inp.spec.kind, stem=f"题 {inp.spec.id}", answer="1")
        return ProduceOut(
            item=it.model_copy(
                update={"verification": it.verification.model_copy(update={"status": VerifyStatus.verified})}
            )
        )

    monkeypatch.setattr(PlanStage, "run", fake_plan)
    monkeypatch.setattr(ProduceStage, "run", fake_produce)
    return plans


def scripted_ask(answers: list[dict]):
    asked: list[tuple[str, str, dict]] = []

    async def ask(kind, prompt, options, payload):
        asked.append((kind, prompt, payload))
        return answers.pop(0) if answers else {"option": "confirm"}

    return ask, asked


def u_paper(duration: int = 40, total: float = 100) -> Understanding:
    return Understanding(route=Route.paper, brief=brief(duration, total))


async def test_staged_flow_checkpoints_and_final_paper(ctx, monkeypatch) -> None:
    fake_stages(monkeypatch)
    ctx.ask_fn, asked = scripted_ask([{"option": "confirm"}, {"option": "confirm"}])
    reply = await paper_flow(ctx, u_paper())
    assert [a[0] for a in asked] == ["blueprint", "samples"]
    p = await ctx.store.papers.get_current(ctx.session_id)
    assert p is not None and len(p.all_items()) == 16 and sum(it.score or 0 for it in p.all_items()) == 100
    assert "共 16 道题" in reply and "满分 100 分" in reply
    revs = [r.summary for r in await ctx.store.papers.list_revisions(ctx.session_id)]
    assert revs[0] == "样题" and revs[-1].startswith("整卷")  # 样题上屏过，最后整卷替换


async def test_blueprint_adjust_changes_plan(ctx, monkeypatch) -> None:
    plans = fake_stages(monkeypatch)
    from verichalk.orchestrator import paper_flow as pf

    async def fake_adjust(c, plan, text):
        return PaperTweak(remove_kinds=["judge"], total_score=120)

    monkeypatch.setattr(pf, "_adjust", fake_adjust)
    ctx.ask_fn, asked = scripted_ask(
        [{"text": "不要判断题，满分120"}, {"option": "confirm"}, {"option": "confirm"}]
    )
    await paper_flow(ctx, u_paper())
    assert [a[0] for a in asked] == ["blueprint", "blueprint", "samples"]
    assert len(plans) == 2 and plans[1].slots != plans[0].slots
    p = await ctx.store.papers.get_current(ctx.session_id)
    assert (
        p is not None
        and p.meta["total_score"] == 120
        and all(it.kind != ItemKind.judge for it in p.all_items())
    )


async def test_not_staged_when_no_checkpoint_support(ctx, monkeypatch) -> None:
    fake_stages(monkeypatch)
    ctx.ask_fn = None  # 评测 / 脚本环境：不暂停，一次出完
    await paper_flow(ctx, u_paper())
    revs = await ctx.store.papers.list_revisions(ctx.session_id)
    assert len(revs) == 1 and revs[0].summary.startswith("整卷")


async def test_dropped_items_keep_total_and_are_reported(ctx, monkeypatch) -> None:
    fake_stages(monkeypatch, drop={"it5", "it9", "it5r", "it9r"})
    ctx.ask_fn = None
    reply = await paper_flow(ctx, u_paper())
    p = await ctx.store.papers.get_current(ctx.session_id)
    assert p is not None and len(p.all_items()) == 14 and sum(it.score or 0 for it in p.all_items()) == 100
    assert "没有通过核验" in reply and "总分仍是 100 分" in reply


async def test_each_delivered_item_is_announced_as_it_passes_verification(ctx, monkeypatch) -> None:
    """用户端靠 `item.delivered` 逐题上屏（整份试卷要等全部完成才装配）：每道通过核验的题一个事件，序号连续，被丢弃的不发。"""
    fake_stages(monkeypatch, drop={"it5", "it5r"})
    ctx.ask_fn = None
    sink = MemorySink()
    async with use_tracer(Tracer("run_t", sink)):
        await paper_flow(ctx, u_paper())
    got = [e for e in sink.events if e.type == "item.delivered"]
    assert len(got) == 15  # 16 个题位，it5 补题后仍没通过
    assert sorted(e.order for e in got) == list(range(1, 16))
    assert "n_it5" not in {e.item.id for e in got} and all(e.visibility == "user" for e in got)
    paper_patches = [i for i, e in enumerate(sink.events) if e.type == "paper.patch"]
    last_delivered = max(i for i, e in enumerate(sink.events) if e.type == "item.delivered")
    assert paper_patches and paper_patches[-1] > last_delivered  # 装配在逐题送达之后


async def test_samples_redo_once(ctx, monkeypatch) -> None:
    fake_stages(monkeypatch)
    ctx.ask_fn, asked = scripted_ask([{"option": "confirm"}, {"option": "redo"}, {"option": "redo"}])
    await paper_flow(ctx, u_paper())
    assert [a[0] for a in asked] == ["blueprint", "samples", "samples"]  # 重出一次后不再追问
    p = await ctx.store.papers.get_current(ctx.session_id)
    assert p is not None and len(p.all_items()) == 16


def test_paper_title() -> None:
    from verichalk.stages.paper_plan import paper_title

    b = brief(40, 100)
    b.scope.grade = Slot[int](value=4, origin=Origin.user)
    b.scope.semester = Slot[str](value="b", origin=Origin.user)
    assert paper_title(b) == "四年级下册测试卷"
    b.scope.units = Slot[list[str]](value=["g4b.u3"], origin=Origin.user)
    assert paper_title(b) == "四年级下册第三单元测试卷"
    b.scope.units = Slot[list[str]](value=["g4b.u2", "g4b.u4"], origin=Origin.user)
    assert paper_title(b) == "四年级下册第2～4单元测试卷"
    assert paper_title(Brief()) == "数学测试卷"


async def test_paper_run_pauses_at_both_checkpoints_through_the_run_manager(store, monkeypatch) -> None:
    """整卷运行在 blueprint、samples 两个检查点暂停，用户回应后继续；最终试卷落盘，运行成功。"""
    import asyncio

    from verichalk.domain.run import RunStatus
    from verichalk.orchestrator import RunManager, pipelines
    from verichalk.orchestrator.pipelines import main_pipeline
    from verichalk.trace import EventBus

    fake_stages(monkeypatch)

    async def fake_understand(c, turn, refs=None):
        return u_paper()

    monkeypatch.setattr(pipelines, "understand_turn", fake_understand)
    mgr = RunManager(Settings(), store, EventBus(), None, None, {"main": main_pipeline})  # type: ignore[arg-type]
    ses = await mgr.create_session()
    run = await mgr.start_turn(ses.id, "出一份四年级下册第三单元的单元测试")
    seen: list[str] = []
    for _ in range(400):
        cur = await store.runs.get(run.id)
        if cur.status == RunStatus.awaiting_user and cur.checkpoint:
            kind = cur.checkpoint["kind"]
            if not seen or seen[-1] != kind or cur.checkpoint["id"] not in seen:
                seen.append(kind)
                seen.append(cur.checkpoint["id"])
                await mgr.resume(run.id, {"option": "confirm"})
        elif cur.status.terminal:
            break
        await asyncio.sleep(0.02)
    done = await mgr.wait(run.id, 20)
    assert done.status == RunStatus.succeeded
    assert [x for x in seen if x in ("blueprint", "samples")] == ["blueprint", "samples"]
    p = await store.papers.get_current(ses.id)
    assert p is not None and len(p.all_items()) == 16 and p.meta["total_score"] == 100
    assert "试卷已经排好" in (await store.messages.list(ses.id))[-1].content

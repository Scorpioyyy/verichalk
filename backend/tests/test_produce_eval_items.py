"""出题评测的计分：丢弃后补上的题（`produce:{id}r`）计入交付。"""

from __future__ import annotations

from verichalk.domain.blueprint import Blueprint, ItemSpec
from verichalk.domain.paper import Item, ItemKind
from verichalk.eval.produce_eval import items_from_state
from verichalk.stages import ProduceOut


def test_replacement_items_count_as_delivered() -> None:
    bp = Blueprint(
        items=[ItemSpec(id="it1", index=1, kp_ids=["k"]), ItemSpec(id="it2", index=2, kp_ids=["k"])]
    )
    item = Item(id="x", kind=ItemKind.calc, stem="1+1=____", answer="2")
    state = {
        "produce:it1": ProduceOut(attempts=3, dropped_reason="超出单题时间预算").model_dump(),
        "produce:it1r": ProduceOut(item=item, attempts=1).model_dump(),
        "produce:it2": ProduceOut(attempts=4, dropped_reason="盲解不一致").model_dump(),
    }
    recs = items_from_state(state, bp)
    assert [r.item is not None for r in recs] == [True, False]
    assert recs[0].attempts == 4


def test_saved_runs_load_back_for_rescoring() -> None:
    from verichalk.eval.cases import Case, TurnSpec
    from verichalk.eval.produce_eval import CaseRec, ItemRec, load_runs, save_runs

    item = Item(id="x", kind=ItemKind.calc, stem="1+1=____", answer="2")
    rec = CaseRec(
        case=Case(id="c1", turns=[TurnSpec(user="出1道")]),
        status="succeeded",
        lesson_id="g1a.u1.l01",
        n_requested=2,
        items=[ItemRec(item, 1), ItemRec(None, 2, "超出单题时间预算", 3)],
        e2e_s=12.5,
    )
    (back,) = load_runs(save_runs([rec]))
    assert back.case.id == "c1" and back.lesson_id == "g1a.u1.l01" and back.e2e_s == 12.5
    assert [it.id for it in back.delivered] == ["x"] and back.items[1].dropped_reason == "超出单题时间预算"


async def test_auditors_read_the_figure_description() -> None:
    # 审计模型看不到图片：题面里的图要换成与线上核验同一份文字描述，否则看图题一律"无法确定"
    from types import SimpleNamespace

    from verichalk.domain.paper import FigureSpec
    from verichalk.eval.cases import Case, TurnSpec
    from verichalk.eval.produce_eval import CaseRec, ItemRec, audit_records

    fig = FigureSpec(id="f1", kind="bar_chart", params={"categories": ["一班", "二班"], "values": [24, 30]})
    item = Item(
        id="x",
        kind=ItemKind.fill,
        stem="如图，二班比一班多____本。\n\n![图](fig:f1)",
        answer="6",
        figures=[fig],
    )
    rec = CaseRec(case=Case(id="c", turns=[TurnSpec(user="出题")]), items=[ItemRec(item, 1)])
    seen: list[str] = []

    async def audit(ai):  # type: ignore[no-untyped-def]
        seen.append(ai.stem)
        return None

    async def graph():  # type: ignore[no-untyped-def]
        return SimpleNamespace(nodes={})

    await audit_records(SimpleNamespace(audit=audit), SimpleNamespace(graph=graph), [rec])  # type: ignore[arg-type]
    assert "fig:" not in seen[0] and "一班 24" in seen[0] and "二班 30" in seen[0]


def test_tokens_spent_on_dropped_slots_count_as_waste() -> None:
    # 题位 it1 被丢弃后由 it1r 补上：it1 上的调用算浪费，it1r 与 it2 上的不算
    from verichalk.domain.events import LLMCall, SpanKind, SpanStarted
    from verichalk.domain.llm import LLMCallRecord, Usage
    from verichalk.eval.produce_eval import token_waste

    def span(sid: str, parent: str | None, key: str | None) -> SpanStarted:
        e = SpanStarted(
            kind=SpanKind.stage, name="produce" if key else "llm", attrs={"key": key} if key else {}
        )
        e.span_id, e.parent_id = sid, parent
        return e

    def call(sid: str, n: int) -> LLMCall:
        e = LLMCall(
            record=LLMCallRecord(
                id=sid, role="smart", model="m", profile="intl", usage=Usage(prompt_tokens=n)
            )
        )
        e.span_id = sid
        return e

    events = [
        span("a", None, "it1"),
        span("a1", "a", None),
        call("a1", 100),
        span("b", None, "it1r"),
        call("b", 50),
        span("c", None, "it2"),
        call("c", 30),
    ]
    state = {
        "produce:it1": {"item": None},
        "produce:it1r": {"item": {"id": "x"}},
        "produce:it2": {"item": {"id": "y"}},
    }
    assert token_waste(events, state) == (180, 100)

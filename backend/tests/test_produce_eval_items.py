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

"""课本式同类题（`template` 来源）：同一次出题里不交付重复的题；超纲仍由正常的抽取 + 判定检查。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from verichalk.domain.blueprint import ItemSpec
from verichalk.domain.paper import Item
from verichalk.stages.produce import ProduceIn, ProduceStage


class _KB:
    def __init__(self, stems: list[str]) -> None:
        self.stems = stems

    async def instantiate(
        self, aid: str, *, seed: int, lesson_id: str | None, only_in_bounds: bool
    ) -> dict[str, Any]:
        return {"problem": self.stems[seed % len(self.stems)], "answer": "1", "archetype_id": aid}


async def test_duplicate_template_stems_are_not_delivered_twice(monkeypatch) -> None:
    stage = ProduceStage()
    delivered: list[str] = []

    async def fake_item(ctx, inp, problem):  # type: ignore[no-untyped-def]
        delivered.append(problem["problem"])
        return Item.model_construct(), ""

    monkeypatch.setattr(stage, "_template_item", fake_item)
    ctx = SimpleNamespace(scratch={}, run_id="r", kb=_KB(["12+35=", "12+35="]))
    spec = ItemSpec(id="it1", index=1, kp_ids=["k"], source="template", archetype_ids=["a1", "a2"])
    first = await stage._template(ctx, ProduceIn(spec=spec))  # type: ignore[arg-type]
    second = await stage._template(ctx, ProduceIn(spec=spec.model_copy(update={"id": "it2"})))  # type: ignore[arg-type]
    assert first.item is not None and delivered == ["12+35="]
    assert second.item is None and "重复" in second.dropped_reason


def test_number_signature_ignores_order_and_context() -> None:
    from verichalk.stages.produce import number_signature

    assert number_signature("口算：$28+35=$____") == number_signature(
        "果园里上午摘了 35 个，下午摘了 28 个，一共？"
    )
    assert number_signature("计算：$52-37=$____") != number_signature("计算：$52-27=$____")
    assert number_signature("钟面上是 8 时") == ""  # 少于两个数不比较

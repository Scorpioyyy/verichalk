from __future__ import annotations

import pytest

from verichalk.core.errors import InvalidRequest
from verichalk.domain.paper import Item, ItemKind, Paper, Section
from verichalk.domain.paper_ops import (
    AddItem,
    AddSection,
    MoveItem,
    RemoveItem,
    ReplaceField,
    SetMeta,
    SetTitle,
    apply_patch,
    parse_ops,
)


def _item(p: Paper, item_id: str):
    found = p.find_item(item_id)
    assert found is not None
    return found[2]


def make_paper() -> Paper:
    p = Paper(id="p1", title="t")
    return apply_patch(
        p,
        [
            AddSection(section=Section(id="s1", title="一、填空题")),
            AddSection(section=Section(id="s2", title="二、应用题")),
            AddItem(section_id="s1", item=Item(id="i1", kind=ItemKind.fill, stem="$1+1=$____")),
            AddItem(section_id="s1", item=Item(id="i2", kind=ItemKind.fill, stem="$2+2=$____")),
        ],
    )


def test_apply_is_pure_and_bumps_revs():
    p0 = make_paper()
    assert p0.rev == 1
    p1 = apply_patch(p0, [ReplaceField(item_id="i1", field="stem", value="$3+3=$____")])
    assert _item(p0, "i1").stem == "$1+1=$____"  # 入参不变
    assert p1.rev == 2
    assert _item(p1, "i1").rev == 2 and _item(p1, "i2").rev == 1  # 只有被触及的题 rev 增加


def test_move_remove_meta_title():
    p = make_paper()
    p = apply_patch(
        p,
        [
            MoveItem(item_id="i1", section_id="s2", index=0),
            SetTitle(title="新标题"),
            SetMeta(key="class", value="四(2)班"),
            RemoveItem(item_id="i2"),
        ],
    )
    assert [i.id for i in p.sections[1].items] == ["i1"]
    assert p.sections[0].items == []
    assert p.title == "新标题" and p.meta["class"] == "四(2)班"


@pytest.mark.parametrize(
    "ops",
    [
        [RemoveItem(item_id="nope")],
        [ReplaceField(item_id="i1", field="id", value="x")],  # 不可改的字段
        [ReplaceField(item_id="i1", field="difficulty", value="很难")],  # 类型不合法
        [AddItem(section_id="s1", item=Item(id="i1", kind=ItemKind.fill, stem="dup"))],
        [AddItem(section_id="zzz", item=Item(id="i9", kind=ItemKind.fill, stem="x"))],
    ],
)
def test_invalid_patches_are_atomic(ops):
    p = make_paper()
    snapshot = p.model_dump()
    with pytest.raises(InvalidRequest):
        apply_patch(p, ops)
    assert p.model_dump() == snapshot


def test_parse_ops_roundtrip():
    ops = parse_ops([{"op": "set_title", "title": "x"}, {"op": "remove_item", "item_id": "i1"}])
    assert isinstance(ops[0], SetTitle) and isinstance(ops[1], RemoveItem)

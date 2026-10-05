"""试卷补丁（Patch）：手动编辑、自然语言编辑、Agent 生成都产出 Patch，由这里统一应用。

`apply_patch` 是纯函数：不修改入参，失败时抛 `InvalidRequest` 且不产生部分应用的结果（原子性）。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter

from ..core.errors import InvalidRequest
from .paper import Item, Paper, Section

# 允许通过 replace_field 修改的题目字段（id、rev、provenance 由系统维护）
EDITABLE_ITEM_FIELDS = frozenset(
    {
        "kind",
        "stem",
        "options",
        "answer",
        "answer_value",
        "solution",
        "kp_ids",
        "difficulty",
        "tier",
        "score",
        "figures",
        "verification",
    }
)


class AddSection(BaseModel):
    op: Literal["add_section"] = "add_section"
    section: Section
    index: int | None = None


class RemoveSection(BaseModel):
    op: Literal["remove_section"] = "remove_section"
    section_id: str


class AddItem(BaseModel):
    op: Literal["add_item"] = "add_item"
    section_id: str
    item: Item
    index: int | None = None


class RemoveItem(BaseModel):
    op: Literal["remove_item"] = "remove_item"
    item_id: str


class ReplaceField(BaseModel):
    op: Literal["replace_field"] = "replace_field"
    item_id: str
    field: str
    value: Any


class MoveItem(BaseModel):
    op: Literal["move_item"] = "move_item"
    item_id: str
    section_id: str
    index: int | None = None


class SetTitle(BaseModel):
    op: Literal["set_title"] = "set_title"
    title: str


class SetMeta(BaseModel):
    op: Literal["set_meta"] = "set_meta"
    key: str
    value: Any


Op = Annotated[
    AddSection | RemoveSection | AddItem | RemoveItem | ReplaceField | MoveItem | SetTitle | SetMeta,
    Field(discriminator="op"),
]
OpAdapter: TypeAdapter[Op] = TypeAdapter(Op)
OpsAdapter: TypeAdapter[list[Op]] = TypeAdapter(list[Op])


def parse_ops(raw: list[dict[str, Any]]) -> list[Op]:
    return OpsAdapter.validate_python(raw)


def _insert(seq: list, index: int | None, value: Any) -> None:
    if index is None or index >= len(seq):
        seq.append(value)
    elif index < 0:
        raise InvalidRequest("index 不能为负数")
    else:
        seq.insert(index, value)


def apply_patch(paper: Paper, ops: list[Op]) -> Paper:
    """应用补丁，返回 rev 加一的新试卷。被修改或移动的题目 `rev` 同步加一（新增的题保持其自带的 rev）。"""
    new = paper.model_copy(deep=True)
    touched: set[str] = set()

    for op in ops:
        if isinstance(op, AddSection):
            if any(s.id == op.section.id for s in new.sections):
                raise InvalidRequest(f"分区已存在：{op.section.id}")
            _insert(new.sections, op.index, op.section.model_copy(deep=True))
        elif isinstance(op, RemoveSection):
            before = len(new.sections)
            new.sections = [s for s in new.sections if s.id != op.section_id]
            if len(new.sections) == before:
                raise InvalidRequest(f"分区不存在：{op.section_id}")
        elif isinstance(op, AddItem):
            sec = next((s for s in new.sections if s.id == op.section_id), None)
            if sec is None:
                raise InvalidRequest(f"分区不存在：{op.section_id}")
            if new.find_item(op.item.id):
                raise InvalidRequest(f"题目已存在：{op.item.id}")
            _insert(sec.items, op.index, op.item.model_copy(deep=True))
        elif isinstance(op, RemoveItem):
            found = new.find_item(op.item_id)
            if not found:
                raise InvalidRequest(f"题目不存在：{op.item_id}")
            sec, i, _ = found
            del sec.items[i]
        elif isinstance(op, ReplaceField):
            found = new.find_item(op.item_id)
            if not found:
                raise InvalidRequest(f"题目不存在：{op.item_id}")
            if op.field not in EDITABLE_ITEM_FIELDS:
                raise InvalidRequest(f"字段不可修改：{op.field}")
            _, _, item = found
            data = item.model_dump()
            data[op.field] = op.value
            try:
                updated = Item.model_validate(data)
            except Exception as e:  # pydantic.ValidationError
                raise InvalidRequest(f"字段值不合法：{op.field}") from e
            sec, i, _ = found
            sec.items[i] = updated
            touched.add(op.item_id)
        elif isinstance(op, MoveItem):
            found = new.find_item(op.item_id)
            if not found:
                raise InvalidRequest(f"题目不存在：{op.item_id}")
            dst = next((s for s in new.sections if s.id == op.section_id), None)
            if dst is None:
                raise InvalidRequest(f"分区不存在：{op.section_id}")
            src, i, item = found
            del src.items[i]
            _insert(dst.items, op.index, item)
            touched.add(op.item_id)
        elif isinstance(op, SetTitle):
            new.title = op.title
        elif isinstance(op, SetMeta):
            new.meta[op.key] = op.value

    ids = [it.id for it in new.all_items()]
    if len(ids) != len(set(ids)):
        raise InvalidRequest("补丁导致题目 ID 重复")
    for sec in new.sections:
        for k, it in enumerate(sec.items):
            if it.id in touched:
                sec.items[k] = it.model_copy(update={"rev": it.rev + 1})
    new.rev = paper.rev + 1
    return new

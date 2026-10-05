"""试卷编辑的共用部分：补丁的内容规范化、说明、改后提示，以及"应用补丁并落成一条修订"。

手动编辑（`PaperService`）与自然语言编辑（`EditStage`）走同一条路径，因此规范化、`edited` 标记、核验状态与历史的约定只有一份。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from ..core.errors import InvalidRequest
from ..domain.paper import Paper, Revision
from ..domain.paper_ops import (
    AddItem,
    AddSection,
    MoveItem,
    Op,
    RemoveItem,
    RemoveSection,
    ReplaceField,
    SetMeta,
    SetTitle,
    apply_user_edit,
)
from ..store import Store
from ..verify import normalize_text
from ..verify.content import check_math, structure_issues

_TEXT_FIELDS = ("stem", "solution", "answer")
_FIELD_CN = {
    "stem": "题干",
    "options": "选项",
    "answer": "答案",
    "solution": "解析",
    "kind": "题型",
    "score": "分值",
    "difficulty": "难度",
    "tier": "档位",
    "kp_ids": "知识点",
    "figures": "插图",
}


def number_of(paper: Paper, item_id: str) -> int | None:
    n = 0
    for sec in paper.sections:
        for it in sec.items:
            n += 1
            if it.id == item_id:
                return n
    return None


def normalize_ops(ops: list[Op]) -> list[Op]:
    """入库前的内容规范化（D27）：手改的文字与模型写的文字走同一套规则。"""
    out: list[Op] = []
    for op in ops:
        if isinstance(op, ReplaceField):
            v = op.value
            if op.field in _TEXT_FIELDS and isinstance(v, str):
                v = normalize_text(v)
            elif op.field == "options" and isinstance(v, list):
                v = [normalize_text(x) if isinstance(x, str) else x for x in v]
            op = op.model_copy(update={"value": v})
        elif isinstance(op, AddItem):
            it = op.item
            op = op.model_copy(
                update={
                    "item": it.model_copy(
                        update={
                            "stem": normalize_text(it.stem),
                            "options": [normalize_text(o) for o in it.options],
                            "solution": normalize_text(it.solution),
                        }
                    )
                }
            )
        out.append(op)
    return out


def describe_ops(paper: Paper, ops: list[Op]) -> str:
    """给修订写一句教师能懂的说明："修改了第 3 题的题干与答案"。"""
    edited: dict[str, list[str]] = {}
    parts: list[str] = []
    for op in ops:
        if isinstance(op, ReplaceField):
            edited.setdefault(op.item_id, []).append(_FIELD_CN.get(op.field, op.field))
        elif isinstance(op, AddItem):
            parts.append("新增一道题")
        elif isinstance(op, RemoveItem):
            n = number_of(paper, op.item_id)
            parts.append(f"删除第 {n} 题" if n else "删除一道题")
        elif isinstance(op, MoveItem):
            n = number_of(paper, op.item_id)
            parts.append(f"移动第 {n} 题" if n else "移动一道题")
        elif isinstance(op, SetTitle):
            parts.append("修改标题")
        elif isinstance(op, SetMeta):
            parts.append("修改试卷信息")
        elif isinstance(op, AddSection):
            parts.append("新增分区")
        elif isinstance(op, RemoveSection):
            parts.append("删除分区")
    for iid, fields in edited.items():
        n = number_of(paper, iid)
        label = f"第 {n} 题" if n else "一道题"
        parts.append(f"修改{label}的" + "、".join(dict.fromkeys(fields)))
    return "；".join(parts) or "修改试卷"


def edit_warnings(paper: Paper, item_ids: list[str]) -> list[str]:
    """改完立即给出的确定性提示（不等模型复核）：结构问题与公式语法。"""
    out: list[str] = []
    for iid in item_ids:
        found = paper.find_item(iid)
        if found is None:
            continue
        it = found[2]
        n = number_of(paper, iid)
        issues = structure_issues(
            kind=it.kind.value,
            stem=it.stem,
            options=it.options,
            answer_values=[it.answer] if it.answer.strip() else [],
            solution=it.solution,
        )
        issues += check_math("\n".join([it.stem, *it.options, it.solution]))
        out += [f"第 {n} 题：{x}" for x in issues]
    return out


@dataclass
class Committed:
    paper: Paper
    revision: Revision
    changed: list[str]  # 内容被改（或新增）的题 id
    warnings: list[str]


async def commit_ops(
    store: Store,
    session_id: str,
    paper: Paper,
    ops: list[Op],
    *,
    author: str,
    summary: str = "",
    run_id: str | None = None,
) -> Committed:
    """应用补丁（原子）并写成一条 `edit` 修订。`paper` 必须是当前的试卷（版本号不符会由存储层报冲突）。"""
    if not ops:
        raise InvalidRequest("补丁为空", user_message="没有需要保存的修改。")
    ops = normalize_ops(ops)
    new, changed = apply_user_edit(paper, ops, author_is_user=(author == "user"))
    saved = await store.papers.save(
        session_id,
        new,
        Revision(
            paper_id=new.id,
            rev=new.rev,
            author=author,
            run_id=run_id,
            ts=time.time(),
            patch=[op.model_dump(mode="json") for op in ops],
            summary=summary or describe_ops(paper, ops),
            kind="edit",
        ),
    )
    return Committed(new, saved, changed, edit_warnings(new, changed))

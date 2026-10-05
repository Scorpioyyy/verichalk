"""试卷与题目的中间表示（IR）。

内容文本统一为 **Pandoc Markdown + TeX 数学**（D7）：`$…$` / `$$…$$` 公式，填空横线 `____`，
插图以 `![](fig:<id>)` 引用 `Item.figures` 中的结构化 `FigureSpec`。
所有修改都表达为 `Patch`（见 `paper_ops.py`），因此撤销、重做、diff、版本回退共用一套机制（D22）。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class ItemKind(StrEnum):
    choice = "choice"
    fill = "fill"
    calc = "calc"
    judge = "judge"
    application = "application"
    open = "open"


class Tier(StrEnum):
    consolidate = "consolidate"  # 巩固
    variation = "variation"  # 变式
    integrated = "integrated"  # 综合


class Source(StrEnum):
    novel = "novel"
    template = "template"
    edited = "edited"


class CheckStatus(StrEnum):
    passed = "pass"
    warn = "warn"
    fail = "fail"
    skip = "skip"


class VerifyStatus(StrEnum):
    pending = "pending"
    verified = "verified"  # 求解程序与盲解两路一致
    checked = "checked"  # 单路通过
    needs_review = "needs_review"
    rejected = "rejected"


class CheckResult(BaseModel):
    name: str
    status: CheckStatus
    detail: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)


class Verification(BaseModel):
    status: VerifyStatus = VerifyStatus.pending
    checks: list[CheckResult] = Field(default_factory=list)


class FigureSpec(BaseModel):
    """图形的结构化规格；渲染为 SVG（M6 细化各类型）。"""

    id: str
    kind: str
    params: dict[str, Any] = Field(default_factory=dict)
    alt: str = ""  # 文字描述（无障碍与核验用）


class Provenance(BaseModel):
    source: Source = Source.novel
    archetype_ids: list[str] = Field(default_factory=list)
    reference_ids: list[str] = Field(default_factory=list)
    run_id: str | None = None
    model: str | None = None


class Item(BaseModel):
    id: str
    kind: ItemKind
    stem: str
    options: list[str] = Field(default_factory=list)
    answer: str = ""
    answer_value: Any | None = None  # 精确答案的规范表示（字符串化的 Fraction / Decimal 等）
    solution: str = ""
    kp_ids: list[str] = Field(default_factory=list)
    difficulty: int = 3
    tier: Tier = Tier.consolidate
    score: float | None = None
    figures: list[FigureSpec] = Field(default_factory=list)
    provenance: Provenance = Field(default_factory=Provenance)
    verification: Verification = Field(default_factory=Verification)
    rev: int = 1


class Section(BaseModel):
    id: str
    title: str = ""
    kind: str = ""
    items: list[Item] = Field(default_factory=list)


class Paper(BaseModel):
    id: str
    title: str = ""
    rev: int = 0
    sections: list[Section] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)

    def all_items(self) -> list[Item]:
        return [it for s in self.sections for it in s.items]

    def find_item(self, item_id: str) -> tuple[Section, int, Item] | None:
        for s in self.sections:
            for i, it in enumerate(s.items):
                if it.id == item_id:
                    return s, i, it
        return None


RevisionKind = Literal["edit", "undo", "redo", "restore", "review"]


class Revision(BaseModel):
    """试卷的一条修订。历史是线性的、只增不删；撤销 / 重做 / 回退也是新增修订（内容取自某个更早的"逻辑版本"）。

    - `logical`：这条修订所代表的"逻辑版本"。edit / restore 是它自己；undo / redo 是被恢复的那个逻辑版本；
      review（后台复核只更新核验状态）与它所复核的逻辑版本相同，因此对撤销栈是透明的。
    - `parent`：仅 edit / restore，它所基于的逻辑版本（撤销沿着 parent 往回走）。
    - `redo`：此刻的重做栈（逻辑版本号，栈顶在前）；任何新的 edit / restore 都会清空它。
    未填写（None）的 `logical / parent / redo` 由存储层按 `kind` 补全，调用方通常只需给出 `kind`。
    """

    paper_id: str
    rev: int
    author: str  # "agent" | "user" | "system"
    run_id: str | None = None
    ts: float
    patch: list[dict[str, Any]] = Field(default_factory=list)
    summary: str = ""
    kind: RevisionKind = "edit"
    logical: int | None = None
    parent: int | None = None
    redo: list[int] | None = None

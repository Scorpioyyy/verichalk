"""试卷与题目的中间表示（IR）。

内容文本统一为 **Pandoc Markdown + TeX 数学**（D7）：`$…$` / `$$…$$` 公式，填空横线 `____`，
插图以 `![](fig:<id>)` 引用 `Item.figures` 中的结构化 `FigureSpec`。
所有修改都表达为 `Patch`（见 `paper_ops.py`），因此撤销、重做、diff、版本回退共用一套机制（D22）。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

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


class Revision(BaseModel):
    paper_id: str
    rev: int
    author: str  # "agent" | "user"
    run_id: str | None = None
    ts: float
    patch: list[dict[str, Any]] = Field(default_factory=list)
    summary: str = ""

"""Brief：把用户的自然语言（及图片）解析成的结构化需求。

每个字段是 `Slot[T]`，带来源标记：`user`（用户明说）> `inferred`（推断）> `default`（系统默认）。
"默认创新"等先验只作用于 `default` 与 `inferred`；用户明说的永远优先（PRD §4.2、D5）。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

from .paper import ItemKind, Tier

T = TypeVar("T")


class Origin(StrEnum):
    user = "user"
    inferred = "inferred"
    default = "default"


class Slot(BaseModel, Generic[T]):
    """带来源与置信度的字段值。"""

    value: T
    origin: Origin = Origin.default
    confidence: float = 1.0


class Action(StrEnum):
    generate = "generate"
    paper = "paper"
    review = "review"


class SourceMode(StrEnum):
    auto = "auto"
    novel = "novel"
    template = "template"


class Scope(BaseModel):
    grade: Slot[int] | None = None
    semester: Slot[str] | None = None  # "a" 上册 / "b" 下册
    units: Slot[list[str]] | None = None  # 单元 ID，如 g4b.u2
    lesson_ids: Slot[list[str]] | None = None
    kp_ids: Slot[list[str]] | None = None
    kp_topics: dict[str, str] = Field(
        default_factory=dict
    )  # 多主题请求：知识点 ID → 它对应的用户主题（综合题要在主题之间组合）
    topics: Slot[list[str]] | None = None  # 未能映射到知识点的自由文本主题


class ReferenceItem(BaseModel):
    """来自照片或用户粘贴的题：既是"学生做过什么"的上下文，也是防雷同集合。"""

    id: str
    text: str
    instruction: str = ""  # 这道题所属大题的共同题干（"化简各数。"）
    no: str = ""  # 原题号
    kp_ids: list[str] = Field(default_factory=list)
    difficulty: int | None = None
    kind: ItemKind | None = None
    has_figure: bool = False
    figure_desc: str = ""
    source: str = ""  # 如 photo:att_xxx#3
    confidence: float = 1.0
    uncertain: str = ""  # 转写时看不清的说明（教师确认后清空）


class PaperSpec(BaseModel):
    duration_min: int | None = None
    total_score: float | None = None
    structure: list[dict[str, Any]] = Field(default_factory=list)  # 大题结构：题型、题量、分值


class Brief(BaseModel):
    action: Slot[Action] = Field(default_factory=lambda: Slot[Action](value=Action.generate))
    scope: Scope = Field(default_factory=Scope)
    count: Slot[int] | None = None
    difficulty: Slot[list[int]] | None = None  # 难度范围 [lo, hi]（1～5，闭区间）
    kinds: Slot[list[ItemKind]] | None = None
    tier_mix: Slot[dict[Tier, float]] | None = None
    source: Slot[SourceMode] = Field(default_factory=lambda: Slot[SourceMode](value=SourceMode.auto))
    scenes: Slot[list[str]] | None = None
    constraints: Slot[list[str]] | None = None  # 如 "数字不要太大""不要图形题"
    target_lesson: Slot[str] | None = None  # 能力边界的参照课时：学生"学到哪"
    references: list[ReferenceItem] = Field(default_factory=list)
    paper: PaperSpec | None = None
    assumptions: list[str] = Field(default_factory=list)  # 展示给用户的"本次假设"

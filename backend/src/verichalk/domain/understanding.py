"""意图理解的输出：路由 + Brief + 澄清 + 展示给用户的"芯片"。"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from .brief import Brief, PaperSpec, SourceMode
from .paper import ItemKind, Tier

Method = Literal["llm", "rules", "rules_fallback"]


class Route(StrEnum):
    generate = "generate"  # 出题 / 追加 / 调整需求后重出
    paper = "paper"  # 整卷
    edit = "edit"  # 修改已有的题或试卷
    ask = "ask"  # 针对已有题或知识的提问
    export = "export"  # 导出
    offtopic = "offtopic"  # 与小学数学命题无关或超出范围


class ClarifyOption(BaseModel):
    id: str
    label: str


class ClarifyRequest(BaseModel):
    """一次只问一个问题，带 2～4 个可点选项（PRD FR-3）。"""

    prompt: str
    options: list[ClarifyOption] = Field(default_factory=list)


class EditIntent(BaseModel):
    target: str  # 如 "item:3"、"items:1,3"、"all"、"kind:choice"
    instruction: str


class Chip(BaseModel):
    """展示在结果顶部的"本次假设"芯片（教师语言，可点击修改）。"""

    key: str  # grade / semester / unit / topics / count / difficulty / kinds / tier / scenes / constraints
    label: str  # 如 "年级"
    value: str  # 如 "四年级下册"
    origin: Literal["user", "inferred", "default"]


class Understanding(BaseModel):
    route: Route
    brief: Brief | None = None
    clarify: ClarifyRequest | None = None
    edit: EditIntent | None = None
    reason: str = ""
    chips: list[Chip] = Field(default_factory=list)
    method: Method = "llm"
    notes: list[str] = Field(default_factory=list)  # 降级、截断等需要告知用户的说明


class RawParse(BaseModel):
    """语言模型 / 规则解析的原始结果（扁平结构）。来源标记、默认值、知识点映射、澄清判断都在后处理里由代码完成。

    `explicit` 列出用户在这句话里**明说**的字段名；有值但不在 `explicit` 里的字段视为推断。
    """

    route: Route = Route.generate
    reason: str = ""
    grade: int | None = Field(default=None, ge=1, le=6)
    semester: Literal["a", "b"] | None = None
    unit_ordinals: list[int] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)  # 用户提到的知识主题（教材规范叫法）
    count: int | None = Field(default=None, ge=1)
    difficulty: list[int] | None = None  # [lo, hi]，1～5
    kinds: list[ItemKind] = Field(default_factory=list)
    tier: Tier | None = None
    source: SourceMode = SourceMode.auto
    scenes: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    action: Literal["generate", "paper", "review"] = "generate"
    paper: PaperSpec | None = None
    edit: EditIntent | None = None
    explicit: list[str] = Field(default_factory=list)

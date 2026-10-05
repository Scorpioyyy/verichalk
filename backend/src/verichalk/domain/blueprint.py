"""蓝图（规划阶段的输出）与创作阶段的数据模型。

`ItemSpec` 是"这道题要考什么、怎么考"的规格；`Blueprint` 是一次出题的全部规格。创作阶段把 `ItemSpec` 变成 `Item`。
答案用结构化的 `AnswerPart`（每问一个值），显示用的答案串由程序拼装，而不是让模型自由书写（produce.md 失败模式 4）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .brief import SourceMode
from .paper import ItemKind, Tier


class ItemSpec(BaseModel):
    id: str
    index: int
    kp_ids: list[str]  # 单点题 1 个；综合 / 复习题 2～3 个
    kp_names: list[str] = Field(default_factory=list)  # 展示与提示词用的教师可读名称
    roles: dict[str, str] = Field(default_factory=dict)  # 知识点 ID → 它在解答中起的作用（防"伪综合"）
    tier: Tier = Tier.consolidate
    difficulty: int = 3
    kind: ItemKind = ItemKind.application
    scene: str = ""  # 情境主题（来自情境库或用户指定）
    scene_hint: str = ""  # 该年级下情境里的典型量与数值范围
    angle: str = ""  # 问法 / 结构设想：学生要做什么，各知识点分别在哪一步起作用
    design: str = ""  # 考法结构：逆向求原数 / 比较择优 / 多步混合 / 方案选择 / 纠错辨析 / 规律探究 / 估算与检验 / 数据分析
    target_error: str = ""  # 这道题要暴露的学生典型错误（取自知识库）
    number_hint: str = ""  # 数值范围提示（贴合目标课时的能力边界）
    source: Literal["novel", "template"] = "novel"
    archetype_ids: list[str] = Field(
        default_factory=list
    )  # novel：作"学生做过什么"的上下文；template：要实例化的题型
    review: bool = False  # 螺旋复习项：含已学前置知识点
    rationale: str = ""  # 选择这个知识点 / 组合的理由（图检索的证据）


class Blueprint(BaseModel):
    lesson_id: str | None = None  # 目标课时：学生"学到哪"
    grade: int | None = None
    items: list[ItemSpec] = Field(default_factory=list)
    method: Literal["llm", "rules"] = "rules"  # 情境与问法由模型构想，还是确定性分配
    notes: list[str] = Field(default_factory=list)
    source: SourceMode = SourceMode.auto


class AnswerPart(BaseModel):
    """一问的结构化答案。数值用字符串（`"11.10"` / `"3/4"` / `"1又1/2"`），选择题填选项字母，判断题填"对 / 错"。"""

    label: str = ""  # 这一问求什么（多问时用，如"商""余数"）
    value: str
    unit: str = ""


class WriteOut(BaseModel):
    """写题模型的输出。`scratch` 是草稿区（允许自我修正，不展示给教师）；其余字段必须是定稿。"""

    scratch: str = ""
    solver_code: str = ""  # 求解程序：`def solve():` 返回与 `answers` 同序的精确值列表
    stem: str
    options: list[str] = Field(default_factory=list)
    answers: list[AnswerPart] = Field(default_factory=list)
    solution: str = ""
    kp_use: dict[str, str] = Field(default_factory=dict)  # 声称涉及的知识点 → 在解答里怎样用到
    difficulty: int = 3

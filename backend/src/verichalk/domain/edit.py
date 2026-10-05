"""自然语言编辑：模型把教师的一句话译成**编辑计划**（小词表里的动作），执行由确定性代码完成。

模型只决定"做什么、对哪几题、带什么参数"；怎样改成补丁、怎样保证别的题不被动，都不由模型负责（失败模式 2、4）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .paper import ItemKind, VerifyStatus

ActionOp = Literal[
    "rewrite",  # 按要求改写题目（换情境 / 调难度 / 改题型 / 数字调整 / 换考法）：复用创作与核验
    "rewrite_text",  # 只改文字表述（解析讲细一点、题干说得更通俗），答案不变
    "remove",
    "move",  # 把一道题移到第 `to` 题的位置
    "swap",  # 交换两道题
    "set_score",  # 给指定题（items 为空 = 全部）设分值
    "set_total",  # 把总分设为 score，按题型权重重新分配每题分值
    "set_title",
]


class EditAction(BaseModel):
    op: ActionOp
    items: list[int] = Field(default_factory=list)  # 题号（界面与导出使用的连续编号，从 1 起）
    kind: ItemKind | None = None  # rewrite：改成这种题型
    difficulty: int | None = Field(default=None, ge=1, le=5)  # rewrite：目标难度（绝对值）
    difficulty_delta: int | None = Field(default=None, ge=-3, le=3)  # rewrite：相对原题的难度变化
    scene: str = ""  # rewrite：新情境
    instruction: str = ""  # rewrite / rewrite_text：教师的要求，保留原话要点
    field: Literal["solution", "stem"] = "solution"  # rewrite_text：改哪个字段
    to: int | None = None  # move：目标题号
    score: float | None = None  # set_score / set_total
    title: str = ""  # set_title


class EditPlan(BaseModel):
    actions: list[EditAction] = Field(default_factory=list)
    unsupported: str = ""  # 做不了或没听明白时，用教师的话说明原因（不执行任何动作）


class EditItemResult(BaseModel):
    number: int  # 题号（编辑前）
    item_id: str
    op: str
    ok: bool
    detail: str = ""  # 教师能懂的说明：改了什么 / 没改成的原因
    status: VerifyStatus | None = None  # 改写后的核验状态


class EditOut(BaseModel):
    results: list[EditItemResult] = Field(default_factory=list)
    summary: str = ""
    rev: int | None = None  # 新修订的版本号；没有任何改动时为 None
    unsupported: str = ""

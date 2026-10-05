"""整卷的结构计划（细目表）：分区 × 题型 × 题量 × 分值 × 难度梯度。

由确定性规则从 `Brief.paper` 生成（时长、总分、教师指定的题型结构），教师在"蓝图"检查点看到并可调整；
之后每个题位（slot）对应规划阶段的一道题规格。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .paper import ItemKind


class Slot(BaseModel):
    """一个题位：这一位置的题型、难度与分值。"""

    kind: ItemKind
    difficulty: int = 3
    score: int = 0


class SectionPlan(BaseModel):
    title: str  # 如"选择题"（编号"一、"由导出 / 视图按分区顺序加）
    kind: ItemKind
    count: int
    score_each: int  # 同一分区内分值不同时取最小值（尾差补在后面的题上），展示用
    subtotal: int
    difficulty: tuple[int, int] = (1, 5)  # 分区内难度范围（从易到难）


class PaperPlan(BaseModel):
    title: str = ""
    duration_min: int = 40
    total_score: int = 100
    sections: list[SectionPlan] = Field(default_factory=list)
    slots: list[Slot] = Field(default_factory=list)  # 按试卷顺序展开的题位
    notes: list[str] = Field(default_factory=list)  # 需要告知教师的说明（题量按时长估计、总分与结构不一致…）

    @property
    def n_items(self) -> int:
        return len(self.slots)

    def table(self) -> list[dict[str, object]]:
        """给检查点 / 界面用的细目表。"""
        return [
            {
                "title": s.title,
                "kind": s.kind.value,
                "count": s.count,
                "score_each": s.score_each,
                "subtotal": s.subtotal,
                "difficulty": list(s.difficulty),
            }
            for s in self.sections
        ]

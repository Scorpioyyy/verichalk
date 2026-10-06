"""Badcase 记录（evaluation §6、FR-12）：调试台发现的坏例子，入库后按根因类别在对应层修复。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# 根因类别（决定修在哪一层）：见 docs/evaluation.md §6
RootCause = Literal["U", "P", "R", "W", "V", "B", "E", "X", "S", "K", "UX"]
Severity = Literal["S1", "S2", "S3"]
BadcaseStatus = Literal["open", "fixed", "wontfix"]


class BadcaseIn(BaseModel):
    """调试台提交的坏例子。输入与观察到的问题由人填写；运行 / 题目由调试台带上。"""

    run_id: str
    item_id: str | None = None
    input: str = ""  # 教师的原话
    problem: str  # 观察到的问题
    expected: str = ""  # 期望的表现
    root_cause: RootCause
    severity: Severity = "S2"
    stage: str = ""  # 涉及的阶段（可选）


class Badcase(BadcaseIn):
    id: str
    created_at: float
    status: BadcaseStatus = "open"
    fix_commit: str = ""
    regression_case: str = ""
    tags: list[str] = Field(default_factory=list)

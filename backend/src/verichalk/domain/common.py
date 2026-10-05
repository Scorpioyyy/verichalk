"""跨模块共用的小型数据模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ErrorInfo(BaseModel):
    """可序列化的错误信息（进入事件与运行记录）。`message` 已脱敏；`user_message` 是教师语言。"""

    code: str
    message: str = ""
    user_message: str = ""
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)

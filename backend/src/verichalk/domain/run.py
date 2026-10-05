"""会话、消息、运行的数据模型。"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from .common import ErrorInfo


class RunStatus(StrEnum):
    created = "created"
    running = "running"
    awaiting_user = "awaiting_user"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in (RunStatus.succeeded, RunStatus.failed, RunStatus.cancelled)


class Session(BaseModel):
    id: str
    created_at: float
    updated_at: float
    title: str = ""


class AttachmentRef(BaseModel):
    id: str
    filename: str
    mime: str
    size: int
    sha256: str


class Attachment(AttachmentRef):
    session_id: str
    path: str  # 相对数据目录
    ts: float


class MessageRole(StrEnum):
    user = "user"
    assistant = "assistant"
    system = "system"


class Message(BaseModel):
    id: str
    session_id: str
    role: MessageRole
    content: str
    attachments: list[AttachmentRef] = Field(default_factory=list)
    run_id: str | None = None
    ts: float


class Run(BaseModel):
    id: str
    session_id: str
    message_id: str | None = None
    pipeline: str
    status: RunStatus = RunStatus.created
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    error: ErrorInfo | None = None
    tags: list[str] = Field(default_factory=list)
    state: dict[str, Any] = Field(default_factory=dict)  # 各阶段输出快照（暂停恢复与重放用）
    checkpoint: dict[str, Any] | None = None  # 进行中的检查点请求

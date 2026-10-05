"""API 的请求 / 响应模型（OpenAPI 与前端类型的来源）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..domain.common import ErrorInfo
from ..domain.paper import Paper
from ..domain.run import Message, Run, RunStatus, Session
from ..metrics import RunMetrics


class HealthOut(BaseModel):
    status: str = "ok"
    version: str
    chalkbase_version: str
    data_version: str
    profile: str
    llm_mode: str
    models: dict[str, str] = Field(default_factory=dict)


class SessionState(BaseModel):
    session: Session
    messages: list[Message]
    paper: Paper | None = None
    active_run_id: str | None = None


class TurnAccepted(BaseModel):
    session_id: str
    run_id: str
    message_id: str


class CheckpointAnswer(BaseModel):
    answer: dict[str, Any] = Field(default_factory=dict)


class RunView(BaseModel):
    """面向用户端的运行视图（不含内部阶段快照）。"""

    id: str
    session_id: str
    pipeline: str
    status: RunStatus
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    error: ErrorInfo | None = None
    checkpoint: dict[str, Any] | None = None

    @classmethod
    def of(cls, run: Run) -> RunView:
        return cls(**run.model_dump(include=set(cls.model_fields)))


class ErrorBody(BaseModel):
    error: ErrorInfo


class DebugRunItem(BaseModel):
    run: Run
    metrics: RunMetrics


class DebugRunDetail(BaseModel):
    run: Run
    metrics: RunMetrics
    n_events: int

"""API 的请求 / 响应模型（OpenAPI 与前端类型的来源）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..domain.common import ErrorInfo
from ..domain.paper import Paper, Revision
from ..domain.paper_ops import Op
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
    input_text: str = ""  # 触发这次运行的教师原话（复核等没有输入的运行为空）


class DebugRunDetail(BaseModel):
    run: Run
    metrics: RunMetrics
    n_events: int
    input_text: str = ""


class WarmupOut(BaseModel):
    state: str  # started | running | fresh | disabled
    last_age_s: float | None = None


class PaperPatchBody(BaseModel):
    """手动编辑：一组补丁（原子）。`base_rev` 是客户端看到的版本，用于发现别处的内容修改。"""

    base_rev: int | None = None
    ops: list[Op]


class RestoreBody(BaseModel):
    rev: int


class PaperUpdate(BaseModel):
    """一次修改（手改 / 撤销 / 重做 / 回退）的结果。`review_run_id` 非空时，订阅该运行的事件可看到核验状态更新。"""

    paper: Paper
    revision: Revision
    warnings: list[str] = Field(default_factory=list)
    review_run_id: str | None = None
    can_undo: bool = False
    can_redo: bool = False


class PaperHistory(BaseModel):
    revisions: list[Revision]
    head_rev: int
    can_undo: bool
    can_redo: bool

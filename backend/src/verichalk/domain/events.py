"""事件协议（D6、architecture §6）：一切可观测的东西都是事件。

- 信封字段：`seq`（运行内单调递增）、`run_id`、`span_id`、`parent_id`、`ts`、`visibility`。
- `type` 是判别字段；事件模型是带判别字段的联合类型，随 OpenAPI 导出，前端据此生成类型。
- 只增不改：新增事件类型或可选字段时升 `SCHEMA_VERSION` 的次版本（见 tests/test_event_schema.py 的快照）。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter

from .common import ErrorInfo
from .knowledge import RetrievalPayload
from .llm import LLMCallRecord, Usage
from .paper import CheckResult, VerifyStatus

SCHEMA_VERSION = "1.0"

CheckpointKind = Literal["clarify", "blueprint", "samples", "confirm"]


class Visibility(StrEnum):
    user = "user"
    debug = "debug"


class SpanKind(StrEnum):
    run = "run"
    stage = "stage"
    llm = "llm"
    tool = "tool"
    check = "check"
    other = "other"


class SpanStatus(StrEnum):
    ok = "ok"
    error = "error"
    cancelled = "cancelled"


class EventBase(BaseModel):
    seq: int = 0
    run_id: str = ""
    span_id: str | None = None
    parent_id: str | None = None
    ts: float = 0.0
    visibility: Visibility = Visibility.debug


# ---- 运行生命周期 ----
class RunStarted(EventBase):
    type: Literal["run.started"] = "run.started"
    visibility: Visibility = Visibility.user
    session_id: str
    pipeline: str
    schema_version: str = SCHEMA_VERSION


class RunFinished(EventBase):
    type: Literal["run.finished"] = "run.finished"
    visibility: Visibility = Visibility.user
    status: Literal["succeeded", "failed", "cancelled"]
    error: ErrorInfo | None = None


class RunPaused(EventBase):
    type: Literal["run.paused"] = "run.paused"
    visibility: Visibility = Visibility.user
    checkpoint_id: str


# ---- span ----
class SpanStarted(EventBase):
    type: Literal["span.started"] = "span.started"
    kind: SpanKind
    name: str
    attrs: dict[str, Any] = Field(default_factory=dict)


class SpanFinished(EventBase):
    type: Literal["span.finished"] = "span.finished"
    kind: SpanKind
    name: str
    status: SpanStatus
    duration_ms: float
    attrs: dict[str, Any] = Field(default_factory=dict)
    error: ErrorInfo | None = None


# ---- 面向用户的进度与内容 ----
class Progress(EventBase):
    type: Literal["progress"] = "progress"
    visibility: Visibility = Visibility.user
    label: str  # 教师语言，如"正在回顾四下前三单元学过的内容"
    current: int | None = None
    total: int | None = None


class MessageDelta(EventBase):
    type: Literal["message.delta"] = "message.delta"
    visibility: Visibility = Visibility.user
    message_id: str
    text: str


class MessageDone(EventBase):
    type: Literal["message.done"] = "message.done"
    visibility: Visibility = Visibility.user
    message_id: str
    text: str


class ItemStatus(EventBase):
    type: Literal["item.status"] = "item.status"
    visibility: Visibility = Visibility.user
    item_id: str
    status: VerifyStatus
    checks: list[CheckResult] = Field(default_factory=list)


class PaperPatched(EventBase):
    type: Literal["paper.patch"] = "paper.patch"
    visibility: Visibility = Visibility.user
    paper_id: str
    rev: int
    ops: list[dict[str, Any]] = Field(default_factory=list)
    summary: str = ""


class CheckpointRequested(EventBase):
    type: Literal["checkpoint.requested"] = "checkpoint.requested"
    visibility: Visibility = Visibility.user
    checkpoint_id: str
    kind: CheckpointKind
    prompt: str
    options: list[dict[str, Any]] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)


# ---- 调试与指标 ----
class LLMCall(EventBase):
    type: Literal["llm.call"] = "llm.call"
    record: LLMCallRecord


class RetrievalResult(EventBase):
    type: Literal["retrieval.result"] = "retrieval.result"
    payload: RetrievalPayload


class UsageUpdate(EventBase):
    type: Literal["usage.update"] = "usage.update"
    usage: Usage
    cost: float | None = None
    currency: str = "CNY"


Event = Annotated[
    RunStarted
    | RunFinished
    | RunPaused
    | SpanStarted
    | SpanFinished
    | Progress
    | MessageDelta
    | MessageDone
    | ItemStatus
    | PaperPatched
    | CheckpointRequested
    | LLMCall
    | RetrievalResult
    | UsageUpdate,
    Field(discriminator="type"),
]
EventAdapter: TypeAdapter[Event] = TypeAdapter(Event)

TERMINAL_EVENT_TYPES = frozenset({"run.finished"})

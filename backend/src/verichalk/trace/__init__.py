"""事件与 trace（L2）：业务代码通过 `from verichalk import trace` 使用 `trace.span(...)` 等入口。"""

from .api import (
    check_span,
    llm_call,
    message_delta,
    message_done,
    progress,
    retrieval,
    span,
    stage_span,
    tool_span,
    usage_update,
)
from .sinks import EventBus, EventSink, MemorySink, StoreSink
from .tracer import SpanHandle, Tracer, current_span_id, current_tracer, error_info, use_tracer

__all__ = [
    "EventBus",
    "EventSink",
    "MemorySink",
    "SpanHandle",
    "StoreSink",
    "Tracer",
    "check_span",
    "current_span_id",
    "current_tracer",
    "error_info",
    "llm_call",
    "message_delta",
    "message_done",
    "progress",
    "retrieval",
    "span",
    "stage_span",
    "tool_span",
    "usage_update",
    "use_tracer",
]

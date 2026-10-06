"""事件与 trace（L2）：业务代码通过 `from verichalk import trace` 使用 `trace.span(...)` 等入口。"""

from .api import (
    check_span,
    item_delivered,
    item_status,
    llm_call,
    message_delta,
    message_done,
    paper_patched,
    progress,
    retrieval,
    span,
    stage_span,
    tool_span,
    understanding_ready,
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
    "item_delivered",
    "item_status",
    "llm_call",
    "message_delta",
    "message_done",
    "paper_patched",
    "progress",
    "retrieval",
    "span",
    "stage_span",
    "tool_span",
    "understanding_ready",
    "usage_update",
    "use_tracer",
]

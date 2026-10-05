"""业务代码使用的 trace 便捷入口：`trace.span(...)`、`trace.progress(...)` 等。"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Any

from ..domain.events import (
    LLMCall,
    MessageDelta,
    MessageDone,
    Progress,
    RetrievalResult,
    SpanKind,
    UsageUpdate,
    Visibility,
)
from ..domain.knowledge import RetrievalPayload
from ..domain.llm import LLMCallRecord, Usage
from .tracer import SpanHandle, current_tracer


def span(kind: SpanKind, name: str, **attrs: Any) -> AbstractAsyncContextManager[SpanHandle]:
    return current_tracer().span(kind, name, **attrs)


def stage_span(name: str, **attrs: Any) -> AbstractAsyncContextManager[SpanHandle]:
    return span(SpanKind.stage, name, **attrs)


def tool_span(name: str, **attrs: Any) -> AbstractAsyncContextManager[SpanHandle]:
    return span(SpanKind.tool, name, **attrs)


def check_span(name: str, **attrs: Any) -> AbstractAsyncContextManager[SpanHandle]:
    return span(SpanKind.check, name, **attrs)


async def progress(label: str, current: int | None = None, total: int | None = None) -> None:
    await current_tracer().emit(Progress(label=label, current=current, total=total))


async def message_delta(message_id: str, text: str) -> None:
    await current_tracer().emit(MessageDelta(message_id=message_id, text=text))


async def message_done(message_id: str, text: str) -> None:
    await current_tracer().emit(MessageDone(message_id=message_id, text=text))


async def llm_call(record: LLMCallRecord) -> None:
    await current_tracer().emit(LLMCall(record=record, visibility=Visibility.debug))


async def retrieval(payload: RetrievalPayload) -> None:
    await current_tracer().emit(RetrievalResult(payload=payload))


async def usage_update(usage: Usage, cost: float | None, currency: str = "CNY") -> None:
    await current_tracer().emit(UsageUpdate(usage=usage, cost=cost, currency=currency))

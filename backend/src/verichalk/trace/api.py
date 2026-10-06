"""业务代码使用的 trace 便捷入口：`trace.span(...)`、`trace.progress(...)` 等。"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Any

from ..domain.events import (
    ItemDelivered,
    ItemStatus,
    LLMCall,
    MessageDelta,
    MessageDone,
    PaperPatched,
    PerceptionReady,
    Progress,
    RetrievalResult,
    SpanKind,
    UnderstandingReady,
    UsageUpdate,
    Visibility,
)
from ..domain.knowledge import RetrievalPayload
from ..domain.llm import LLMCallRecord, Usage
from ..domain.paper import CheckResult, Item, VerifyStatus
from ..domain.perception import ReferenceSet
from ..domain.understanding import Understanding
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


async def understanding_ready(u: Understanding) -> None:
    await current_tracer().emit(UnderstandingReady(understanding=u))


async def perception_ready(refs: ReferenceSet) -> None:
    await current_tracer().emit(PerceptionReady(references=refs))


async def item_status(item_id: str, status: VerifyStatus, checks: list[CheckResult]) -> None:
    await current_tracer().emit(ItemStatus(item_id=item_id, status=status, checks=checks))


async def item_delivered(item: Item, order: int) -> None:
    await current_tracer().emit(ItemDelivered(item=item, order=order))


async def paper_patched(paper_id: str, rev: int, ops: list[dict[str, Any]], summary: str = "") -> None:
    await current_tracer().emit(PaperPatched(paper_id=paper_id, rev=rev, ops=ops, summary=summary))

"""Tracer：span 与事件的唯一入口（architecture §2 规则 4）。

- 一个运行一个 `Tracer`；`seq` 在运行内单调递增，写入顺序 = seq 顺序（发射时持锁）。
- 当前 tracer 与当前 span 通过 `contextvars` 传递，因此深层代码无需层层传参；
  `asyncio.create_task` 会复制上下文，并行子任务自然挂在发起它的 span 之下。
- 任何退出路径（正常、异常、取消）都会闭合 span。
"""

from __future__ import annotations

import asyncio
import contextvars
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from ..core.errors import VerichalkError
from ..core.ids import new_id
from ..core.redact import redact
from ..domain.common import ErrorInfo
from ..domain.events import Event, SpanFinished, SpanKind, SpanStarted, SpanStatus
from .sinks import EventSink

_current_tracer: contextvars.ContextVar[Tracer | None] = contextvars.ContextVar(
    "verichalk_tracer", default=None
)
_current_span: contextvars.ContextVar[str | None] = contextvars.ContextVar("verichalk_span", default=None)


def error_info(exc: BaseException) -> ErrorInfo:
    """把任意异常转成可序列化、已脱敏的 ErrorInfo。"""
    if isinstance(exc, VerichalkError):
        return ErrorInfo(**exc.to_dict())
    return ErrorInfo(
        code="internal_error",
        message=redact(f"{type(exc).__name__}: {exc}"),
        user_message=VerichalkError.user_message,
    )


class SpanHandle:
    def __init__(self, span_id: str, parent_id: str | None, attrs: dict[str, Any]) -> None:
        self.id = span_id
        self.parent_id = parent_id
        self.attrs = attrs

    def set(self, **kv: Any) -> None:
        self.attrs.update(kv)


class Tracer:
    def __init__(self, run_id: str, sink: EventSink | None, *, start_seq: int = 0) -> None:
        self.run_id = run_id
        self._sink = sink
        self._seq = start_seq
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self._sink is not None

    async def emit(self, event: Event, *, span_id: str | None = None) -> Event:
        """补全信封字段并写入。`span_id` 缺省取当前 span。"""
        if self._sink is None:
            return event
        async with self._lock:
            self._seq += 1
            event.seq = self._seq
            event.run_id = self.run_id
            event.ts = time.time()
            if span_id is None:
                span_id = _current_span.get()
            event.span_id = event.span_id or span_id
            await self._sink.write(event)
        return event

    @asynccontextmanager
    async def span(self, kind: SpanKind, name: str, **attrs: Any) -> AsyncIterator[SpanHandle]:
        parent = _current_span.get()
        handle = SpanHandle(new_id("spn"), parent, dict(attrs))
        start = time.perf_counter()
        started = SpanStarted(kind=kind, name=name, attrs=dict(handle.attrs))
        started.span_id, started.parent_id = handle.id, parent
        await self.emit(started, span_id=handle.id)
        token = _current_span.set(handle.id)
        status, err = SpanStatus.ok, None
        try:
            yield handle
        except asyncio.CancelledError:
            status = SpanStatus.cancelled
            raise
        except BaseException as e:
            status, err = SpanStatus.error, error_info(e)
            raise
        finally:
            _current_span.reset(token)
            fin = SpanFinished(
                kind=kind,
                name=name,
                status=status,
                duration_ms=(time.perf_counter() - start) * 1000,
                attrs=dict(handle.attrs),
                error=err,
            )
            fin.span_id, fin.parent_id = handle.id, parent
            # shield：即使外层被取消，也要把闭合事件写完
            await asyncio.shield(self.emit(fin, span_id=handle.id))


def current_tracer() -> Tracer:
    t = _current_tracer.get()
    return t if t is not None else _NULL


_NULL = Tracer("", None)


@asynccontextmanager
async def use_tracer(tracer: Tracer, root_span: str | None = None) -> AsyncIterator[Tracer]:
    """把 tracer 绑定到当前上下文（运行开始时调用一次）。"""
    t1 = _current_tracer.set(tracer)
    t2 = _current_span.set(root_span)
    try:
        yield tracer
    finally:
        _current_span.reset(t2)
        _current_tracer.reset(t1)


def current_span_id() -> str | None:
    return _current_span.get()

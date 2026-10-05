"""SSE：把事件日志变成可续传的流（D11）。

- `id:` 是事件 seq；客户端断线后用 `Last-Event-ID` 续传，服务端从该 seq 之后重读存储，不丢不重。
- 终止条件是 `run.finished` 事件；对已经结束且无新事件的运行，直接关闭。
- 防"丢失唤醒"：先取通知对象、再查存储、最后等待（见 EventBus）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request

from ..domain.events import Event
from ..orchestrator import Container

KEEPALIVE_S = 15.0


def format_sse(event: Event) -> str:
    return f"id: {event.seq}\nevent: {event.type}\ndata: {event.model_dump_json()}\n\n"


async def event_stream(
    c: Container,
    run_id: str,
    *,
    after: int,
    visibility: str | None,
    request: Request | None = None,
    keepalive_s: float = KEEPALIVE_S,
) -> AsyncIterator[str]:
    seq = after
    while True:
        wake = c.bus.current(run_id)
        events = await c.store.events.list(run_id, after_seq=seq, visibility=visibility)
        for e in events:
            yield format_sse(e)
            seq = e.seq
            if e.type == "run.finished":
                return
        if not events:
            run = await c.store.runs.get(run_id)
            if run.status.terminal:
                return
            if request is not None and await request.is_disconnected():
                return
            if not await c.bus.wait(wake, keepalive_s):
                yield ": keepalive\n\n"

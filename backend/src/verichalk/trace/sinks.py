"""事件的落点：持久化到存储并通知等待者；或在内存里收集（测试）。"""

from __future__ import annotations

import asyncio
from typing import Protocol

from ..core.redact import redact
from ..domain.events import Event
from ..store import Store


class EventBus:
    """进程内通知。持久化先于通知；订阅者被唤醒后再按 seq 从存储读取，因此不会丢事件。

    防"丢失唤醒"：订阅者必须先 `current(run_id)` 拿到事件对象，再查存储，最后 `await` 它——
    这样在"查存储"与"等待"之间到达的通知也会置位同一个事件对象。
    """

    def __init__(self) -> None:
        self._events: dict[str, asyncio.Event] = {}

    def current(self, run_id: str) -> asyncio.Event:
        ev = self._events.get(run_id)
        if ev is None:
            ev = self._events[run_id] = asyncio.Event()
        return ev

    def notify(self, run_id: str) -> None:
        ev = self._events.pop(run_id, None)
        if ev is not None:
            ev.set()

    async def wait(self, ev: asyncio.Event, timeout: float) -> bool:
        try:
            await asyncio.wait_for(ev.wait(), timeout)
            return True
        except TimeoutError:
            return False


class EventSink(Protocol):
    async def write(self, event: Event) -> None: ...


class StoreSink:
    """把事件脱敏后写入存储，再通知总线。"""

    def __init__(self, store: Store, bus: EventBus) -> None:
        self._store, self._bus = store, bus

    async def write(self, event: Event) -> None:
        text = redact(event.model_dump_json())
        await self._store.events.append(
            event.run_id,
            event.seq,
            event.ts,
            event.type,
            event.span_id,
            event.parent_id,
            event.visibility.value,
            text,
        )
        self._bus.notify(event.run_id)


class MemorySink:
    """测试用：事件收集在内存列表里（同样经过脱敏的序列化往返，行为与存储一致）。"""

    def __init__(self) -> None:
        self.events: list[Event] = []

    async def write(self, event: Event) -> None:
        from ..domain.events import EventAdapter

        self.events.append(EventAdapter.validate_json(redact(event.model_dump_json())))

"""并发小工具。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


async def gather_all(*aws: Coroutine[Any, Any, T] | Awaitable[T]) -> list[T]:
    """并行执行，按传入顺序返回结果。任一任务出错时，先取消其余任务并等它们收尾（各自的 span 正常闭合、
    事件在本次运行结束前写完），再抛出最先发生的那个异常——`asyncio.gather` 会让其余任务留在后台继续跑。"""

    async def run(a: Awaitable[T]) -> T:
        return await a

    try:
        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(run(a)) for a in aws]
    except BaseExceptionGroup as eg:
        raise eg.exceptions[0] from None
    return [t.result() for t in tasks]

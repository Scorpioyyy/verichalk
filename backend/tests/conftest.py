from __future__ import annotations

import pytest
import pytest_asyncio

from verichalk.store import Store
from verichalk.trace import EventBus, StoreSink, Tracer


@pytest_asyncio.fixture
async def store():
    s = await Store.open(":memory:")
    yield s
    await s.close()


@pytest_asyncio.fixture
async def traced(store):
    """返回 (tracer, store, bus)：事件写入内存数据库。"""
    bus = EventBus()
    tracer = Tracer("run_test", StoreSink(store, bus))
    return tracer, store, bus


@pytest.fixture(scope="session")
def kb_service():
    """真实 chalkbase（离线词法检索）：不带密钥，避免测试触发网络。"""
    import os
    import warnings

    from chalkbase import Curriculum

    from verichalk.knowledge import KnowledgeService

    saved = {k: os.environ.pop(k, None) for k in ("DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL")}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            yield KnowledgeService(Curriculum())  # pyright: ignore[reportCallIssue]
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v

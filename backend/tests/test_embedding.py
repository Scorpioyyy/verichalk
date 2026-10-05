from __future__ import annotations

import asyncio
import warnings

import httpx
import pytest
from pydantic import SecretStr

from verichalk.core.config import Credentials
from verichalk.core.errors import KnowledgeError
from verichalk.knowledge.embedding import QueryEmbedder

CREDS = Credentials(api_key=SecretStr("k" * 12), base_url="https://example.invalid/compatible-mode/v1")


def make(handler, **kw) -> QueryEmbedder:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return QueryEmbedder(CREDS, asyncio.get_running_loop(), client=client, **kw)


def ok(request: httpx.Request) -> httpx.Response:
    import json

    n = len(json.loads(request.content)["input"])
    # 故意乱序返回，验证按 index 排序
    return httpx.Response(
        200, json={"data": [{"index": i, "embedding": [float(i)]} for i in reversed(range(n))]}
    )


async def test_parses_and_sorts_by_index():
    e = make(ok)
    assert await e.aembed(["a", "b", "c"]) == [[0.0], [1.0], [2.0]]


async def test_retries_server_errors_then_succeeds():
    calls = {"n": 0}

    def flaky(request):
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else ok(request)

    assert await make(flaky).aembed(["a"]) == [[0.0]] and calls["n"] == 3


async def test_gives_up_with_typed_error_and_no_secrets():
    e = make(lambda r: httpx.Response(500), retries=1)
    with pytest.raises(KnowledgeError) as ei:
        await e.aembed(["a"])
    assert "k" * 12 not in str(ei.value)


async def test_client_errors_are_not_retried():
    calls = {"n": 0}

    def bad(request):
        calls["n"] += 1
        return httpx.Response(400)

    with pytest.raises(KnowledgeError):
        await make(bad).aembed(["a"])
    assert calls["n"] == 1


async def test_sync_call_from_worker_thread_works():
    e = make(ok)
    assert await asyncio.to_thread(e, ["x", "y"]) == [[0.0], [1.0]]


async def test_service_degrades_to_lexical_when_embedding_fails(monkeypatch):
    """向量接口不可用时检索降级为词法检索，而不是整个失败。"""
    import chalkbase.query.embed as embed_mod

    if not hasattr(embed_mod, "set_embedder"):
        pytest.skip("需要提供 set_embedder 的 chalkbase 版本")
    from chalkbase import Curriculum

    from verichalk.knowledge import KnowledgeService

    monkeypatch.setenv("CHALKBASE_CACHE", "NUL_does_not_exist_for_test")
    monkeypatch.setattr(embed_mod, "_mem", None)
    svc = KnowledgeService(Curriculum())  # pyright: ignore[reportCallIssue]
    failing = make(lambda r: httpx.Response(500), retries=0)

    def sync_embed(texts):
        try:
            return failing(texts)
        except KnowledgeError as e:
            raise embed_mod.EmbeddingUnavailable(str(e)) from e

    embed_mod.set_embedder(sync_embed)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            hits = await svc.search("一条从未缓存过的查询：小数加减法", k=3)
    finally:
        embed_mod.set_embedder(None)
    assert hits and hits[0].name

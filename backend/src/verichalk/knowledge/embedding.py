"""查询向量化：连接池复用、不走本机代理、可并发（注入 chalkbase 的 `set_embedder`）。

为什么要自己做：chalkbase 默认用标准库 `urllib` 发同步请求——走环境代理、每次新建连接（TLS 握手）、并在锁内串行。
本机实测中位数 4.8s（直连但每次新建连接 1.4s）；复用连接直连只要 0.64s（最快 0.25s）。见 design.md D28。
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from ..core.config import Credentials
from ..core.errors import KnowledgeError
from ..core.redact import redact

log = logging.getLogger("verichalk.embedding")
MODEL = "text-embedding-v4"  # 必须与知识点向量的模型一致（chalkbase 随包向量记录了模型 ID）


class QueryEmbedder:
    """同步可调用对象（供 chalkbase 在工作线程里调用），内部用一个常驻的异步 HTTP 客户端。"""

    def __init__(
        self,
        creds: Credentials,
        loop: asyncio.AbstractEventLoop,
        *,
        timeout_s: float = 20.0,
        retries: int = 2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._creds, self._loop, self._timeout, self._retries = creds, loop, timeout_s, retries
        self._client = client or httpx.AsyncClient(
            trust_env=False,  # 不走本机 HTTP 代理（对阿里云端点不稳定）
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
        )

    async def aembed(self, texts: list[str]) -> list[list[float]]:
        body = {"model": MODEL, "input": texts, "encoding_format": "float"}
        headers = {"Authorization": f"Bearer {self._creds.api_key.get_secret_value()}"}
        last = "unknown"
        for attempt in range(self._retries + 1):
            try:
                r = await self._client.post(
                    f"{self._creds.base_url}/embeddings", json=body, headers=headers, timeout=self._timeout
                )
                if r.status_code == 200:
                    data = sorted(r.json()["data"], key=lambda d: d["index"])
                    return [d["embedding"] for d in data]
                last = f"HTTP {r.status_code}"
                if r.status_code < 500 and r.status_code != 429:
                    break
            except httpx.HTTPError as e:
                last = type(e).__name__
            await asyncio.sleep(0.4 * (2**attempt))
        raise KnowledgeError(redact(f"向量接口调用失败：{last}"), retryable=True)

    def __call__(self, texts: list[str]) -> list[list[float]]:
        """在工作线程里被 chalkbase 调用：把请求交给事件循环，等待结果。"""
        return asyncio.run_coroutine_threadsafe(self.aembed(texts), self._loop).result(
            self._timeout * (self._retries + 1) + 5
        )

    async def warm(self) -> None:
        """预热：发一个极短的请求，建立到向量接口的连接（后续查询复用）。"""
        try:
            await self.aembed(["预热"])
        except KnowledgeError:
            log.warning("embedding warm-up failed")

    async def aclose(self) -> None:
        await self._client.aclose()

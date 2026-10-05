"""HTTP 传输：OpenAI 兼容的流式 `chat/completions`（DashScope）。

- 对 `.aliyuncs.com` 直连，不走本机 HTTP 代理（`trust_env=False`）；本机代理在并发下不稳定（见 CLAUDE.md §4）。
- 只负责"发请求、解析 SSE、把 HTTP 错误映射为带类型的错误"；重试、计时、成本在网关层。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, Protocol

import httpx

from ..core.config import Credentials
from ..core.errors import (
    LLMBadResponse,
    LLMContentFiltered,
    LLMError,
    LLMRateLimited,
    LLMTimeout,
)
from ..core.redact import redact


class Transport(Protocol):
    def stream(self, body: dict[str, Any], *, timeout: float) -> AsyncIterator[dict[str, Any]]: ...

    async def aclose(self) -> None: ...


def map_http_error(status: int, text: str) -> LLMError:
    """把 HTTP 状态与响应体映射为带类型的错误（文本脱敏后才进入异常）。"""
    msg = redact(f"HTTP {status}: {text[:300]}")
    low = text.lower()
    if status == 429:
        return LLMRateLimited(msg)
    if status == 400 and (
        "data_inspection" in low or "inappropriate" in low or ("content" in low and "filter" in low)
    ):
        return LLMContentFiltered(msg)
    if status in (400, 404, 422):
        return LLMBadResponse(msg)
    if status in (401, 403):
        return LLMBadResponse(msg, user_message="模型服务鉴权失败，请联系管理员。", retryable=False)
    if status >= 500:
        return LLMError(msg)
    return LLMBadResponse(msg)


class HttpxTransport:
    def __init__(self, creds: Credentials, *, client: httpx.AsyncClient | None = None) -> None:
        self._creds = creds
        self._client = client or httpx.AsyncClient(
            trust_env=False,
            http2=False,
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
        )

    async def stream(self, body: dict[str, Any], *, timeout: float) -> AsyncIterator[dict[str, Any]]:
        url = f"{self._creds.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._creds.api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        try:
            async with self._client.stream(
                "POST",
                url,
                json=body,
                headers=headers,
                timeout=httpx.Timeout(timeout, connect=10.0),
            ) as resp:
                if resp.status_code != 200:
                    text = (await resp.aread()).decode("utf-8", "replace")
                    raise map_http_error(resp.status_code, text)
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        return
                    try:
                        yield json.loads(payload)
                    except json.JSONDecodeError as e:
                        raise LLMBadResponse(f"无法解析流式分片：{payload[:80]}") from e
        except httpx.TimeoutException as e:
            raise LLMTimeout(redact(f"请求超时：{type(e).__name__}")) from e
        except httpx.TransportError as e:
            raise LLMError(redact(f"网络错误：{type(e).__name__}: {e}")) from e

    async def aclose(self) -> None:
        await self._client.aclose()

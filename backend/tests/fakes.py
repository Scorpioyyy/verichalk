"""测试替身：脚本化的假传输层。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any


def content_chunks(text: str, *, usage: dict[str, Any] | None = None, split: int = 3) -> list[Any]:
    """把文本切成若干 delta 分片，末尾带 finish_reason 与（可选）usage 分片。"""
    step = max(1, len(text) // split) if text else 1
    parts = [text[i : i + step] for i in range(0, len(text), step)] or [""]
    chunks: list[Any] = [{"choices": [{"delta": {"content": p}, "finish_reason": None}]} for p in parts]
    chunks.append({"choices": [{"delta": {}, "finish_reason": "stop"}]})
    if usage is not None:
        chunks.append({"choices": [], "usage": usage})
    return chunks


def usage(prompt: int = 100, completion: int = 20, cached: int = 0, reasoning: int = 0) -> dict[str, Any]:
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "prompt_tokens_details": {"cached_tokens": cached},
        "completion_tokens_details": {"reasoning_tokens": reasoning},
    }


class FakeTransport:
    """每次 `stream` 调用消费一个脚本；脚本是分片列表，其中的异常实例会在该位置抛出。"""

    def __init__(self, *scripts: list[Any]) -> None:
        self.scripts = list(scripts)
        self.bodies: list[dict[str, Any]] = []

    async def stream(self, body: dict[str, Any], *, timeout: float) -> AsyncIterator[dict[str, Any]]:
        self.bodies.append(body)
        script = self.scripts.pop(0) if len(self.scripts) > 1 else self.scripts[0]
        for item in script:
            if isinstance(item, BaseException):
                raise item
            yield item

    async def aclose(self) -> None:
        return None

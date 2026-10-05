"""LLM 网关：业务代码调用模型的唯一入口（architecture §7，D10）。

职责：角色 → 模型解析；流式调用与聚合（TTFT、usage、cached_tokens）；重试与退避；并发限制；
预算检查；成本计算；录制回放；每次调用产生一个 `llm` span 与一条 `llm.call` 事件。
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .. import trace
from ..core.config import LLMMode, Settings
from ..core.errors import (
    LLMBadResponse,
    LLMError,
    LLMRateLimited,
    ReplayMiss,
    VerichalkError,
)
from ..core.ids import new_id
from ..domain.common import ErrorInfo
from ..domain.events import SpanKind
from ..domain.llm import ChatMessage, LLMCallRecord, PromptRef, Role, ToolCall, Usage
from .budget import current_budget
from .cassette import CassetteEntry, CassetteStore, request_key
from .registry import ModelRegistry, PriceTable
from .transport import Transport

DeltaCallback = Callable[[str], Awaitable[None]]


@dataclass
class LLMRequest:
    role: Role
    messages: list[ChatMessage]
    purpose: str = ""
    prompt: PromptRef | None = None
    json_mode: bool = False
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    thinking: bool | None = None
    timeout_s: float | None = None
    on_delta: DeltaCallback | None = field(default=None, repr=False)


@dataclass
class LLMResult:
    text: str
    reasoning_text: str
    tool_calls: list[ToolCall]
    finish_reason: str | None
    usage: Usage
    model: str
    ttfb_ms: float | None
    ttft_ms: float | None
    total_ms: float
    retries: int
    cost: float | None
    currency: str
    from_cassette: bool
    call_id: str


def _content_text(m: ChatMessage) -> str:
    if isinstance(m.content, str):
        return m.content
    return " ".join(str(p.get("text", "")) for p in (m.content or []) if isinstance(p, dict))


def _usage_from(raw: dict[str, Any] | None) -> Usage:
    if not raw:
        return Usage()
    details = raw.get("prompt_tokens_details") or {}
    cdetails = raw.get("completion_tokens_details") or {}
    return Usage(
        prompt_tokens=int(raw.get("prompt_tokens") or 0),
        completion_tokens=int(raw.get("completion_tokens") or 0),
        cached_tokens=int(details.get("cached_tokens") or 0),
        reasoning_tokens=int(cdetails.get("reasoning_tokens") or 0),
    )


class LLMGateway:
    def __init__(
        self,
        settings: Settings,
        registry: ModelRegistry,
        prices: PriceTable,
        transport: Transport | None,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.settings, self.registry, self.prices = settings, registry, prices
        self._transport = transport
        self._sleep = sleep
        self._sem = asyncio.Semaphore(settings.llm_concurrency)
        self._cassette = CassetteStore(settings.cassette_path, settings.cassette_namespace)

    # ---- 对外 ----
    async def complete(self, req: LLMRequest) -> LLMResult:
        spec = self.registry.role(req.role)
        params: dict[str, Any] = {
            **(
                {"temperature": spec.temperature if req.temperature is None else req.temperature}
                if spec.send_temperature
                else {}
            ),
            "max_tokens": spec.max_tokens if req.max_tokens is None else req.max_tokens,
            "enable_thinking": spec.thinking if req.thinking is None else req.thinking,
            **spec.extra,
        }
        if req.json_mode:
            if not any("json" in _content_text(m).lower() for m in req.messages):
                raise ValueError(
                    "json_mode 要求提示词里出现 JSON 字样（服务端约束），请在静态提示中说明输出格式"
                )
            params["response_format"] = {"type": "json_object"}
        if req.tools:
            params["tools"] = req.tools
            if req.tool_choice is not None:
                params["tool_choice"] = req.tool_choice
        messages = [m.model_dump(exclude_none=True) for m in req.messages]
        key = request_key(spec.model, messages, params)
        timeout = req.timeout_s or spec.timeout_s or self.settings.llm_timeout_s
        mode = self.settings.llm_mode

        async with trace.span(
            SpanKind.llm,
            f"{req.role.value}:{req.purpose or 'call'}",
            model=spec.model,
            role=req.role.value,
            purpose=req.purpose,
        ) as sp:
            budget = current_budget()
            if budget:
                budget.check()
            result: LLMResult | None = None
            error: BaseException | None = None
            try:
                if mode in (LLMMode.replay, LLMMode.replay_or_live) and self._cassette.exists(key):
                    result = self._replay(key, spec.model)
                    if req.on_delta and result.text:
                        await req.on_delta(result.text)
                elif mode == LLMMode.replay:
                    raise ReplayMiss(f"replay 未命中：{req.role.value}:{req.purpose} key={key}")
                else:
                    result = await self._live(spec.model, messages, params, timeout, req)
                    if mode == LLMMode.record:
                        self._cassette.save(
                            CassetteEntry(
                                key=key,
                                model=spec.model,
                                purpose=req.purpose,
                                text=result.text,
                                reasoning_text=result.reasoning_text,
                                tool_calls=result.tool_calls,
                                finish_reason=result.finish_reason,
                                usage=result.usage,
                                ttfb_ms=result.ttfb_ms,
                                ttft_ms=result.ttft_ms,
                                total_ms=result.total_ms,
                            )
                        )
            except BaseException as e:
                error = e
                raise
            finally:
                await self._record(req, spec.model, params, messages, result, error, key, sp)
            if budget and result:
                budget.add(result.usage, result.cost)
                await trace.usage_update(budget.usage, budget.cost, self.prices.currency)
            assert result is not None
            sp.set(
                usage=result.usage.model_dump(),
                ttft_ms=result.ttft_ms,
                cost=result.cost,
                retries=result.retries,
                from_cassette=result.from_cassette,
            )
            return result

    # ---- 回放 ----
    def _replay(self, key: str, model: str) -> LLMResult:
        e = self._cassette.load(key)
        return LLMResult(
            text=e.text,
            reasoning_text=e.reasoning_text,
            tool_calls=e.tool_calls,
            finish_reason=e.finish_reason,
            usage=e.usage,
            model=model,
            ttfb_ms=e.ttfb_ms,
            ttft_ms=e.ttft_ms,
            total_ms=e.total_ms,
            retries=0,
            cost=self.prices.cost(model, e.usage),
            currency=self.prices.currency,
            from_cassette=True,
            call_id=new_id("llm"),
        )

    # ---- 实调（含重试）----
    async def _live(
        self,
        model: str,
        messages: list[dict[str, Any]],
        params: dict[str, Any],
        timeout: float,
        req: LLMRequest,
    ) -> LLMResult:
        if self._transport is None:
            raise LLMBadResponse("未配置模型传输层（缺少密钥？）", retryable=False)
        body = {
            "model": model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            **params,
        }
        attempts = self.settings.llm_max_retries + 1
        for attempt in range(attempts):
            try:
                async with self._sem:
                    return await self._stream_once(body, timeout, req, model, retries=attempt)
            except _PreContentError as e:
                err = e.cause
                if attempt + 1 >= attempts or not err.retryable:
                    raise err from None
                delay = min(8.0, 0.8 * (2**attempt)) * (0.5 + random.random() / 2)
                if isinstance(err, LLMRateLimited):
                    delay *= 2
                await self._sleep(delay)
        raise LLMError("重试耗尽")  # 理论不可达

    async def _stream_once(
        self, body: dict[str, Any], timeout: float, req: LLMRequest, model: str, retries: int
    ) -> LLMResult:
        assert self._transport is not None
        t0 = time.perf_counter()
        ttfb = ttft = None
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        finish: str | None = None
        usage = Usage()
        got_content = False
        try:
            async for chunk in self._transport.stream(body, timeout=timeout):
                if ttfb is None:
                    ttfb = (time.perf_counter() - t0) * 1000
                if chunk.get("usage"):
                    usage = _usage_from(chunk["usage"])
                for ch in chunk.get("choices") or []:
                    delta = ch.get("delta") or {}
                    if ch.get("finish_reason"):
                        finish = ch["finish_reason"]
                    if delta.get("reasoning_content"):
                        reasoning_parts.append(delta["reasoning_content"])
                        if ttft is None:
                            ttft = (time.perf_counter() - t0) * 1000
                    if delta.get("content"):
                        got_content = True
                        if ttft is None:
                            ttft = (time.perf_counter() - t0) * 1000
                        text_parts.append(delta["content"])
                        if req.on_delta:
                            await req.on_delta(delta["content"])
                    for tc in delta.get("tool_calls") or []:
                        slot = calls.setdefault(
                            int(tc.get("index", 0)), {"id": "", "name": "", "arguments": ""}
                        )
                        got_content = True
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["name"] = fn["name"]
                        if fn.get("arguments"):
                            slot["arguments"] += fn["arguments"]
        except VerichalkError as e:
            # 已向调用方转发过内容后失败：不能安全重试（会重复输出），直接抛出
            if got_content:
                raise
            raise _PreContentError(e) from e
        total = (time.perf_counter() - t0) * 1000
        tool_calls = [
            ToolCall(id=v["id"] or f"call_{i}", name=v["name"], arguments=v["arguments"])
            for i, v in sorted(calls.items())
        ]
        return LLMResult(
            text="".join(text_parts),
            reasoning_text="".join(reasoning_parts),
            tool_calls=tool_calls,
            finish_reason=finish,
            usage=usage,
            model=model,
            ttfb_ms=ttfb,
            ttft_ms=ttft,
            total_ms=total,
            retries=retries,
            cost=self.prices.cost(model, usage),
            currency=self.prices.currency,
            from_cassette=False,
            call_id=new_id("llm"),
        )

    # ---- 记录 ----
    async def _record(
        self,
        req: LLMRequest,
        model: str,
        params: dict[str, Any],
        messages: list[dict[str, Any]],
        result: LLMResult | None,
        error: BaseException | None,
        key: str,
        sp: trace.SpanHandle,
    ) -> None:
        err_info: ErrorInfo | None = trace.error_info(error) if error is not None else None
        rec = LLMCallRecord(
            id=result.call_id if result else new_id("llm"),
            role=req.role.value,
            model=model,
            profile=self.settings.profile.value,
            purpose=req.purpose,
            prompt=req.prompt,
            params={k: v for k, v in params.items() if k not in ("tools",)},
            messages=messages,
            response_text=result.text if result else "",
            reasoning_text=result.reasoning_text if result else "",
            tool_calls=result.tool_calls if result else [],
            finish_reason=result.finish_reason if result else None,
            usage=result.usage if result else Usage(),
            ttfb_ms=result.ttfb_ms if result else None,
            ttft_ms=result.ttft_ms if result else None,
            total_ms=result.total_ms if result else 0.0,
            retries=result.retries if result else 0,
            cost=result.cost if result else None,
            currency=self.prices.currency,
            from_cassette=result.from_cassette if result else False,
            error=err_info,
        )
        await trace.llm_call(rec)


class _PreContentError(Exception):
    """流在产生任何内容之前失败：可重试。仅在网关内部使用。"""

    def __init__(self, cause: VerichalkError) -> None:
        super().__init__(str(cause))
        self.cause = cause

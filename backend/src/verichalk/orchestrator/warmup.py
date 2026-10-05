"""预热（特性开关 `warmup`）：建立模型连接、写入前缀缓存。

做法：对每个已发布提示词的**静态前缀**发一次 `max_tokens=1` 的请求（不输出任何内容）。
服务商按前缀缓存，真实调用的前缀与之相同，因此能命中；连接池里的 TLS 连接也随之建立。
触发时机：服务启动、用户打开页面（`POST /api/warmup`，在有效期内重复触发不会再发请求）。

这是一个**需要被消融验证的模块**：是否值得保留，取决于 `scripts/ablate_warmup.py` 的实测收益（见 design.md D28）。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Literal

from ..core.config import LLMMode, Settings
from ..core.errors import VerichalkError
from ..domain.llm import ChatMessage, Role
from ..llm import LLMGateway, LLMRequest, all_prompt_ids, get_prompt

log = logging.getLogger("verichalk.warmup")

WarmupState = Literal["started", "running", "fresh", "disabled"]


@dataclass
class WarmupStatus:
    state: WarmupState
    last_age_s: float | None = None
    n_requests: int = 0
    errors: int = 0
    duration_ms: float | None = None


class Warmer:
    def __init__(self, settings: Settings, llm: LLMGateway) -> None:
        self._s, self._llm = settings, llm
        self._last_at: float | None = None
        self._running = False
        self._last = WarmupStatus("fresh")
        self._task: asyncio.Task[None] | None = None

    @property
    def enabled(self) -> bool:
        return self._s.features.enabled("warmup") and self._s.llm_mode in (LLMMode.live, LLMMode.record)

    def status(self) -> WarmupStatus:
        age = None if self._last_at is None else time.monotonic() - self._last_at
        return WarmupStatus(
            state="disabled" if not self.enabled else ("running" if self._running else "fresh"),
            last_age_s=age,
            n_requests=self._last.n_requests,
            errors=self._last.errors,
            duration_ms=self._last.duration_ms,
        )

    def trigger(self, *, force: bool = False) -> WarmupStatus:
        """非阻塞触发：需要预热就在后台启动，立即返回当前状态。"""
        if not self.enabled:
            return WarmupStatus("disabled")
        if self._running:
            return WarmupStatus("running")
        fresh = self._last_at is not None and time.monotonic() - self._last_at < self._s.warmup_ttl_s
        if fresh and not force:
            return WarmupStatus("fresh", last_age_s=time.monotonic() - (self._last_at or 0))
        self._running = True
        self._task = asyncio.ensure_future(self._run())
        return WarmupStatus("started")

    async def wait(self) -> None:
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)

    async def _ping(self, role: Role, system: str) -> bool:
        try:
            await self._llm.complete(
                LLMRequest(
                    role=role,
                    purpose="warmup",
                    max_tokens=1,
                    thinking=False,
                    messages=[
                        ChatMessage(role="system", content=system),
                        ChatMessage(role="user", content="ping"),
                    ],
                )
            )
            return True
        except VerichalkError as e:
            log.warning("warmup ping failed: %s", e.code)
            return False

    async def _run(self) -> None:
        t0 = time.perf_counter()
        seen: set[tuple[str, str]] = set()
        jobs = []
        for pid in all_prompt_ids():
            tpl = get_prompt(pid)
            key = (tpl.role, tpl.static)
            if key not in seen:  # 相同角色与相同静态前缀只需预热一次
                seen.add(key)
                jobs.append(self._ping(Role(tpl.role), tpl.static))
        try:
            results = await asyncio.gather(*jobs)
            ok = [r for r in results if r]
            self._last = WarmupStatus(
                "fresh",
                n_requests=len(jobs),
                errors=len(jobs) - len(ok),
                duration_ms=(time.perf_counter() - t0) * 1000,
            )
            self._last_at = time.monotonic()
        finally:
            self._running = False

"""试卷服务：围绕会话当前试卷的操作（导出；M5 起还有补丁、撤销 / 重做）。

不属于某次"运行"（没有模型调用、不进事件流），所以是独立于 `RunManager` 的小服务，由 API 直接调用。
"""

from __future__ import annotations

import asyncio
import logging
import time

from ..core.config import Settings
from ..core.errors import NotFound
from ..domain.export import ExportOptions
from ..render import ExportResult, export_paper
from ..store import Store

log = logging.getLogger("verichalk.papers")


class PaperService:
    def __init__(self, settings: Settings, store: Store) -> None:
        self.settings, self.store = settings, store

    async def current(self, session_id: str):
        await self.store.sessions.get(session_id)
        paper = await self.store.papers.get_current(session_id)
        if paper is None:
            raise NotFound(f"会话 {session_id} 还没有试卷", user_message="还没有可导出的试卷，先出几道题吧。")
        return paper

    async def export(self, session_id: str, opts: ExportOptions) -> ExportResult:
        paper = await self.current(session_id)
        font_dirs = (str(self.settings.font_dir),) if self.settings.font_dir else ()
        t0 = time.perf_counter()
        # pandoc 与 Typst 是阻塞调用，放到线程里，避免卡住事件循环（SSE 等）
        res = await asyncio.to_thread(export_paper, paper, opts, font_dirs=font_dirs)
        log.info(
            "export %s %s rev=%s %.0fms %dB warnings=%d",
            opts.format.value,
            opts.version.value,
            paper.rev,
            (time.perf_counter() - t0) * 1000,
            len(res.data),
            len(res.warnings),
        )
        return res

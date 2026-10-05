"""试卷服务：围绕会话当前试卷的操作——手动编辑（补丁）、撤销 / 重做 / 回退、历史与 diff、导出。

不属于某次"运行"（手改本身没有模型调用、不进事件流），所以是独立于 `RunManager` 的小服务，由 API 直接调用。
手改之后的复核是一个独立的运行（`RunManager.start_review`），通过 `reviewer` 注入，返回的运行 id 交给前端订阅。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ..core.config import Settings
from ..core.errors import Conflict, InvalidRequest, NotFound
from ..domain.export import ExportOptions
from ..domain.paper import Paper, Revision
from ..domain.paper_ops import PaperDiff, diff_papers, parse_ops
from ..render import ExportResult, export_paper
from ..stages.paper_edit import commit_ops
from ..store import Store

log = logging.getLogger("verichalk.papers")

Reviewer = Callable[[str, list[tuple[str, int]]], Awaitable[object]]  # 返回带 `.id` 的运行


@dataclass
class EditOutcome:
    paper: Paper
    revision: Revision
    warnings: list[str] = field(default_factory=list)
    review_run_id: str | None = None


@dataclass
class HistoryView:
    revisions: list[Revision]
    head_rev: int
    can_undo: bool
    can_redo: bool


class PaperService:
    def __init__(self, settings: Settings, store: Store, reviewer: Reviewer | None = None) -> None:
        self.settings, self.store, self.reviewer = settings, store, reviewer

    # ---- 读 ----
    async def current(self, session_id: str) -> Paper:
        await self.store.sessions.get(session_id)
        paper = await self.store.papers.get_current(session_id)
        if paper is None:
            raise NotFound(f"会话 {session_id} 还没有试卷", user_message="还没有可操作的试卷，先出几道题吧。")
        return paper

    async def history(self, session_id: str) -> HistoryView:
        await self.current(session_id)
        revs = await self.store.papers.list_revisions(session_id)
        head = revs[-1]
        by_rev = {r.rev: r for r in revs}
        row = by_rev.get(head.logical or head.rev)
        return HistoryView(
            revisions=revs,
            head_rev=head.rev,
            can_undo=bool(row and row.parent is not None),
            can_redo=bool(head.redo),
        )

    async def diff(self, session_id: str, rev_from: int, rev_to: int) -> PaperDiff:
        await self.current(session_id)
        a = await self.store.papers.get_revision(session_id, rev_from)
        b = await self.store.papers.get_revision(session_id, rev_to)
        return diff_papers(a, b)

    # ---- 写 ----
    async def edit(
        self,
        session_id: str,
        raw_ops: list[dict],
        *,
        base_rev: int | None = None,
        author: str = "user",
        run_id: str | None = None,
        summary: str = "",
        review: bool = True,
    ) -> EditOutcome:
        """应用一组补丁（原子）：成功返回新试卷与修订，并为内容被改的题发起复核。

        `base_rev` 是客户端看到的版本：若自那以后有过别的**内容**修改（后台复核不算）则拒绝，避免覆盖。"""
        paper = await self.current(session_id)
        head = await self.store.papers.head(session_id)
        assert head is not None
        if base_rev is not None and base_rev != head.rev:
            base = await self.store.papers.revision(session_id, base_rev)
            if base.logical != head.logical:
                raise Conflict(
                    f"试卷已更新：客户端版本 {base_rev}，当前 {head.rev}",
                    user_message="试卷已经有了新的修改，请刷新后再改。",
                )
        try:
            ops = parse_ops(raw_ops)
        except Exception as e:
            raise InvalidRequest(
                f"补丁格式不合法：{str(e)[:200]}", user_message="这次修改的内容不合法。"
            ) from e
        done = await commit_ops(
            self.store, session_id, paper, ops, author=author, summary=summary, run_id=run_id
        )
        new, changed = done.paper, done.changed
        out = EditOutcome(new, done.revision, warnings=done.warnings)
        if review and changed:
            out.review_run_id = await self._start_review(session_id, new, changed)
        return out

    async def _start_review(self, session_id: str, paper: Paper, item_ids: list[str]) -> str | None:
        if self.reviewer is None or not self.settings.features.enabled("edit.review"):
            return None
        targets = [(i, paper.find_item(i)[2].rev) for i in item_ids if paper.find_item(i)]  # type: ignore[index]
        if not targets:
            return None
        try:
            run = await self.reviewer(session_id, targets)
        except NotFound:
            log.warning("review pipeline not registered; skipping review")
            return None
        return getattr(run, "id", None)

    async def _restore_state(
        self, session_id: str, content: Paper, head: Revision, rev: Revision
    ) -> Revision:
        new = content.model_copy(update={"rev": head.rev + 1})
        return await self.store.papers.save(session_id, new, rev)

    async def undo(self, session_id: str) -> EditOutcome:
        paper = await self.current(session_id)
        head = await self.store.papers.head(session_id)
        assert head is not None
        row = await self.store.papers.revision(session_id, head.logical or head.rev)
        if row.parent is None:
            raise InvalidRequest("没有可撤销的操作", user_message="已经是最早的版本了，没有可以撤销的操作。")
        content = await self.store.papers.state_of(session_id, row.parent)
        saved = await self._restore_state(
            session_id,
            content,
            head,
            Revision(
                paper_id=paper.id,
                rev=head.rev + 1,
                author="user",
                ts=time.time(),
                summary=f"撤销：{row.summary}",
                kind="undo",
                logical=row.parent,
                redo=[row.rev, *head.redo] if head.redo is not None else [row.rev],
            ),
        )
        return EditOutcome(content.model_copy(update={"rev": saved.rev}), saved)

    async def redo(self, session_id: str) -> EditOutcome:
        paper = await self.current(session_id)
        head = await self.store.papers.head(session_id)
        assert head is not None
        if not head.redo:
            raise InvalidRequest("没有可重做的操作", user_message="没有可以重做的操作。")
        target, rest = head.redo[0], head.redo[1:]
        row = await self.store.papers.revision(session_id, target)
        content = await self.store.papers.state_of(session_id, target)
        saved = await self._restore_state(
            session_id,
            content,
            head,
            Revision(
                paper_id=paper.id,
                rev=head.rev + 1,
                author="user",
                ts=time.time(),
                summary=f"重做：{row.summary}",
                kind="redo",
                logical=target,
                redo=rest,
            ),
        )
        return EditOutcome(content.model_copy(update={"rev": saved.rev}), saved)

    async def restore(self, session_id: str, rev: int) -> EditOutcome:
        """回到历史上的某一版：新增一条修订，内容取自那一版（历史不删）。这一步本身可以撤销。"""
        paper = await self.current(session_id)
        head = await self.store.papers.head(session_id)
        assert head is not None
        content = await self.store.papers.get_revision(session_id, rev)
        saved = await self._restore_state(
            session_id,
            content,
            head,
            Revision(
                paper_id=paper.id,
                rev=head.rev + 1,
                author="user",
                ts=time.time(),
                summary=f"回到第 {rev} 版",
                kind="restore",
            ),
        )
        return EditOutcome(content.model_copy(update={"rev": saved.rev}), saved)

    # ---- 导出 ----
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

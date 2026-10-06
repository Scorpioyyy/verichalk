"""感知阶段：照片 → `ReferenceSet`（逐题转写 + 知识点映射 + 学生上下文）。

流程：预处理（确定性）→ 视觉模型逐张识别（并行）→ 扁平化与"看不清"标记 → 对页面标题与各题知识主题并行检索 → 学生上下文。
任何一张图失败都不拖垮整个阶段：它被标成不可用并给出教师语言的原因，其余照片照常使用。
"""

from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel, Field

from .. import trace
from ..core.errors import BudgetExceeded, KnowledgeError, LLMError
from ..domain.knowledge import KPHit
from ..domain.llm import ChatMessage, Role
from ..domain.perception import PageRead, RawPage, ReferenceSet, StudentContext
from ..domain.run import Attachment
from ..llm import LLMRequest, complete_json, get_prompt
from ..perception import PrepareError, prepare, to_data_uri
from .base import RunContext, Stage
from .perceive_post import assemble_set, assign_kps, build_context, flatten, rejected_page

log = logging.getLogger("verichalk.perceive")

SEARCH_K = 4


class PerceiveIn(BaseModel):
    attachments: list[Attachment] = Field(default_factory=list)


class PerceiveStage(Stage[PerceiveIn, ReferenceSet]):
    name = "perceive"
    input_model = PerceiveIn
    output_model = ReferenceSet

    async def run(self, ctx: RunContext, inp: PerceiveIn) -> ReferenceSet:
        n = len(inp.attachments)
        await trace.progress("正在识别照片", 0, n)
        pages = list(
            await asyncio.gather(*(self._read_page(ctx, att, i, n) for i, att in enumerate(inp.attachments)))
        )
        hits = await self._search(ctx, pages)
        use_topics = ctx.settings.features.enabled("perceive.topic_search")
        for p in pages:
            if p.verdict.usable:
                assign_kps(p, hits, use_topics=use_topics)
        context = build_context(pages, hits)
        if context.kp_ids:
            context.lesson_id = await self._target_lesson(ctx, context, hits)
        rs = assemble_set(pages, context)
        await trace.progress("照片识别完成", n, n)
        return rs

    # ---- 读一张图 ----
    async def _read_page(self, ctx: RunContext, att: Attachment, index: int, total: int) -> PageRead:
        feats = ctx.settings.features
        path = ctx.settings.data_path / att.path
        try:
            data = await asyncio.to_thread(path.read_bytes)
            prep = await asyncio.to_thread(
                prepare,
                data,
                max_side=ctx.settings.perceive_max_side,
                resize=feats.enabled("perceive.preprocess"),
            )
        except (OSError, PrepareError) as e:
            log.warning("perceive: cannot prepare %s: %s", att.id, e)
            return rejected_page(att.id, att.filename, "这张图片无法打开，请重新上传")
        if prep.reject:
            return rejected_page(att.id, att.filename, prep.reject, prep.quality)
        built = get_prompt("perceive.read").render(dynamic={"index": index + 1, "total": total})
        system, user = built.messages[0], built.messages[-1]
        messages = [
            system,
            ChatMessage(
                role="user",
                content=[
                    {"type": "image_url", "image_url": {"url": to_data_uri(prep)}},
                    {"type": "text", "text": str(user.content)},
                ],
            ),
        ]
        try:
            raw, _ = await complete_json(
                ctx.llm,
                LLMRequest(
                    role=Role.vision,
                    messages=messages,
                    purpose="perceive.read",
                    prompt=built.ref,
                    max_tokens=ctx.settings.perceive_max_tokens,
                ),
                RawPage,
            )
        except (LLMError, BudgetExceeded) as e:
            log.warning("perceive: vision call failed for %s: %s", att.id, getattr(e, "code", e))
            return rejected_page(att.id, att.filename, "图片识别服务暂时不可用，请稍后重试", prep.quality)
        page = flatten(raw, attachment_id=att.id, filename=att.filename, page_index=index)
        page.quality = prep.quality
        return page

    # ---- 知识点检索 ----
    async def _search(self, ctx: RunContext, pages: list[PageRead]) -> dict[str, list[KPHit]]:
        use_topics = ctx.settings.features.enabled("perceive.topic_search")
        queries: list[str] = []
        for p in pages:
            if not p.verdict.usable:
                continue
            queries += [p.title] if p.title else []
            if use_topics:
                queries += [it.topic for it in p.items if it.topic]
        queries = list(dict.fromkeys(q for q in queries if q.strip()))

        async def one(q: str) -> tuple[str, list[KPHit]]:
            try:
                return q, await ctx.kb.search(q, k=SEARCH_K)
            except KnowledgeError as e:
                log.warning("perceive: kb search failed: %s", e.code)
                return q, []

        return dict(await asyncio.gather(*(one(q) for q in queries)))

    async def _target_lesson(
        self, ctx: RunContext, context: StudentContext, hits: dict[str, list[KPHit]]
    ) -> str | None:
        """学生"学到哪"：学段内所有知识点所在课时里最靠后的一个。"""
        by_id = {h.id: h for hs in hits.values() for h in hs}
        lessons = [by_id[k].lesson_id for k in context.kp_ids if k in by_id and by_id[k].lesson_id]
        try:
            return await ctx.kb.latest_lesson([x for x in lessons if x])
        except KnowledgeError as e:
            log.warning("perceive: latest_lesson failed: %s", e.code)
            return None

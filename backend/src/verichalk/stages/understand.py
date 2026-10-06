"""意图理解阶段：一句话（+ 会话上下文）→ `Understanding`（路由 + Brief + 澄清 + 芯片）。

两条解析路径共用同一套后处理（`understand_post.finalize`）：
- 模型路径：`understand.parse` 提示词 + JSON 校验；知识点检索与模型解析**并行**（检索的查询向量要在线计算，约 1.5～3s，
  与模型调用重叠才不会拖慢首个反馈；见 design.md D28）；
- 规则路径：`understand_rules.parse_rules`（基线，也是模型不可用 / 超预算时的降级）。
"""

from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel, Field

from .. import trace
from ..core.errors import BudgetExceeded, KnowledgeError, LLMError
from ..domain.brief import Brief
from ..domain.knowledge import KPHit
from ..domain.llm import Role
from ..domain.perception import StudentContext
from ..domain.understanding import Method, RawParse, Understanding
from ..llm import LLMRequest, complete_json, get_prompt
from .base import RunContext, Stage
from .understand_post import finalize
from .understand_rules import parse_rules

log = logging.getLogger("verichalk.understand")


class UnderstandIn(BaseModel):
    text: str
    has_paper: bool = False
    prev_brief: Brief | None = None
    recent: list[str] = Field(default_factory=list)  # 预留：最近几轮的摘要
    photo: StudentContext | None = None  # 教师上传了照片：学生上下文（范围、难度、题型）


def prev_summary(prev: Brief | None) -> str:
    """上一轮需求的紧凑摘要（只含范围与明说的字段），给模型做上下文。"""
    if prev is None:
        return "无"
    parts = []
    s = prev.scope
    if s.grade:
        parts.append(
            f"{s.grade.value}年级"
            + ({"a": "上册", "b": "下册"}.get(s.semester.value, "") if s.semester else "")
        )
    if s.topics:
        parts.append("主题：" + "、".join(s.topics.value))
    if prev.count and prev.count.origin.value != "default":
        parts.append(f"{prev.count.value}道")
    return "；".join(parts) or "无"


class UnderstandStage(Stage[UnderstandIn, Understanding]):
    name = "understand"
    input_model = UnderstandIn
    output_model = Understanding

    async def run(self, ctx: RunContext, inp: UnderstandIn) -> Understanding:
        feats = ctx.settings.features
        use_context = feats.enabled("understand.context")
        has_paper = inp.has_paper and use_context
        prev = inp.prev_brief if use_context else None
        use_llm = feats.enabled("understand.llm")

        async def search() -> list[KPHit]:
            try:
                return await ctx.kb.search(inp.text, k=6)
            except KnowledgeError as e:
                log.warning("understand: kb search failed: %s", e.code)
                return []

        method: Method = "llm" if use_llm else "rules"
        search_task = asyncio.ensure_future(search()) if feats.enabled("understand.parallel_search") else None
        try:
            if use_llm:
                try:
                    raw = await self._llm_parse(ctx, inp, has_paper, prev)
                except (LLMError, BudgetExceeded) as e:
                    log.warning("understand: llm failed (%s), falling back to rules", e.code)
                    method, raw = "rules_fallback", parse_rules(inp.text, has_paper=has_paper)
            else:
                raw = parse_rules(inp.text, has_paper=has_paper)
            hits = await (search_task if search_task is not None else search())
        finally:
            if search_task is not None and not search_task.done():
                search_task.cancel()
        if raw.topics and feats.enabled("understand.topic_research"):
            hits = await self._reconcile(ctx, raw, hits)
        topic_hits: dict[str, list[KPHit]] | None = None
        if len(raw.topics) >= 2:
            try:
                found = await asyncio.gather(*(ctx.kb.search(tp, k=3) for tp in raw.topics))
                topic_hits = dict(zip(raw.topics, found, strict=True))
            except KnowledgeError as e:
                log.warning("understand: per-topic search failed: %s", e.code)
        u = await finalize(
            raw, hits=hits, kb=ctx.kb, prev=prev, method=method, topic_hits=topic_hits, photo=inp.photo
        )
        if method == "rules_fallback":
            u.notes.append("智能解析暂时不可用，已用基础规则理解您的需求，可能不够准确")
        return u

    async def _reconcile(self, ctx: RunContext, raw: RawParse, hits: list[KPHit]) -> list[KPHit]:
        """用原话检索时，数字与套话（"5道""不要图形题"）会把检索带偏。解析出主题后做一致性检查：
        没有任何命中的名称 / 别名 / 主线包含解析出的主题，就按主题重新检索（只在可疑时多付一次检索）。"""
        texts = await ctx.kb.kp_texts([h.id for h in hits]) if hits else {}
        if any(t in text for t in raw.topics for text in texts.values()):
            return hits
        try:
            again = await ctx.kb.search(" ".join(raw.topics), k=6)
        except KnowledgeError as e:
            log.warning("understand: topic re-search failed: %s", e.code)
            return hits
        return again or hits

    async def _llm_parse(
        self, ctx: RunContext, inp: UnderstandIn, has_paper: bool, prev: Brief | None
    ) -> RawParse:
        built = get_prompt("understand.parse").render(
            stable={"fewshot": ctx.settings.features.enabled("understand.fewshot")},
            dynamic={
                "text": inp.text.strip(),
                "has_paper": has_paper,
                "prev": prev_summary(prev),
                "photo": inp.photo.summary if inp.photo else "",
            },
        )
        await trace.progress("正在理解您的需求")
        raw, _ = await complete_json(
            ctx.llm,
            LLMRequest(
                role=Role.fast,
                messages=built.messages,
                purpose="understand.parse",
                prompt=built.ref,
                max_tokens=700,
            ),
            RawParse,
        )
        return raw

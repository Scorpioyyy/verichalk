"""追问阶段（architecture §5 `answer`，场景 S7）：回答教师对试卷里题目的提问。只读，不改试卷。

上下文 = 被问到的题（题面、选项、答案、解析、核验状态）+ 知识点说明（`answer.context`）+ 试卷概览。
题号由确定性规则从问题里解析（"第 3 题""第三题""最后一题"）；没有题号时只给概览，由模型判断是否需要教师指明。
"""

from __future__ import annotations

import logging
import re

from pydantic import BaseModel

from .. import trace
from ..core.errors import KnowledgeError, LLMError, NotFound
from ..core.ids import new_id
from ..domain.llm import Role
from ..domain.paper import Item, Paper
from ..llm import LLMRequest, get_prompt
from .base import RunContext, Stage
from .plan import _KIND_CN

log = logging.getLogger("verichalk.answer")

_CN = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_STATUS = {
    "verified": "已核验",
    "checked": "已校对",
    "needs_review": "需复核",
    "pending": "待核验",
    "rejected": "未通过",
}
MAX_ITEMS_IN_CONTEXT = 3
MAX_OVERVIEW = 30


def cn_to_int(s: str) -> int | None:
    s = s.strip()
    if s.isdigit():
        return int(s)
    if s == "十":
        return 10
    if s.startswith("十") and len(s) == 2 and s[1] in _CN:
        return 10 + _CN[s[1]]
    if len(s) == 2 and s[0] in _CN and s[1] == "十":
        return _CN[s[0]] * 10
    if len(s) == 3 and s[0] in _CN and s[1] == "十" and s[2] in _CN:
        return _CN[s[0]] * 10 + _CN[s[2]]
    if len(s) == 1 and s in _CN:
        return _CN[s]
    return None


def mentioned_numbers(question: str, n_items: int) -> list[int]:
    """问题里提到的题号（去重、保持出现顺序、限定在试卷范围内）。"""
    nums: list[int] = []
    for m in re.finditer(
        r"第\s*((?:[0-9]+\s*[、,，和与及]\s*)*[0-9]+|[零一二两三四五六七八九十]{1,3})\s*[题道]", question
    ):
        for part in re.split(r"[、,，和与及]", m.group(1)):
            v = cn_to_int(part)
            if v is not None and 1 <= v <= n_items and v not in nums:
                nums.append(v)
    if re.search(r"最后一[题道]", question) and n_items not in nums:
        nums.append(n_items)
    if re.search(r"第一[题道]", question) and 1 not in nums:
        nums.insert(0, 1)
    return nums[:MAX_ITEMS_IN_CONTEXT]


class AnswerIn(BaseModel):
    question: str


class AnswerOut(BaseModel):
    message_id: str
    reply: str
    numbers: list[int] = []  # 被问到的题号


def _short(text: str, n: int = 40) -> str:
    return re.sub(r"\s+", " ", text)[:n]


class AnswerStage(Stage[AnswerIn, AnswerOut]):
    name = "answer"
    input_model = AnswerIn
    output_model = AnswerOut

    async def run(self, ctx: RunContext, inp: AnswerIn) -> AnswerOut:
        paper = await ctx.store.papers.get_current(ctx.session_id)
        message_id = new_id("msg")
        if paper is None or not paper.all_items():
            text = "现在还没有题目可以讲解，先让我出几道题吧。"
            await trace.message_done(message_id, text)
            return AnswerOut(message_id=message_id, reply=text)
        items = paper.all_items()
        numbers = mentioned_numbers(inp.question, len(items))
        rows = [
            {"number": n, "kind": _KIND_CN[it.kind], "stem": _short(it.stem)}
            for n, it in enumerate(items[:MAX_OVERVIEW], start=1)
        ]
        ctx_items = [await self._item_context(ctx, paper, n, items[n - 1]) for n in numbers]
        built = get_prompt("answer.ask").render(
            dynamic={"items": ctx_items, "n": len(items), "overview": rows, "question": inp.question}
        )

        async def on_delta(t: str) -> None:
            await trace.message_delta(message_id, t)

        try:
            res = await ctx.llm.complete(
                LLMRequest(
                    role=Role.smart,
                    messages=built.messages,
                    purpose="answer.ask",
                    prompt=built.ref,
                    on_delta=on_delta,
                    max_tokens=900,
                )
            )
            text = res.text.strip()
        except LLMError as e:
            log.warning("answer failed: %s", e.code)
            text = "这次没能回答，请稍后再问一次。"
        await trace.message_done(message_id, text)
        return AnswerOut(message_id=message_id, reply=text, numbers=numbers)

    async def _item_context(self, ctx: RunContext, paper: Paper, number: int, item: Item) -> dict:
        kps: list[dict] = []
        for kid in item.kp_ids[:3]:
            try:
                d = await ctx.kb.kp(kid)
                kps.append(
                    {
                        "name": d.name,
                        "desc": d.description[:120]
                        if ctx.settings.features.enabled("answer.context")
                        else "",
                        "errors": d.typical_errors[:2]
                        if ctx.settings.features.enabled("answer.context")
                        else [],
                    }
                )
            except (KnowledgeError, NotFound):
                kps.append({"name": kid, "desc": "", "errors": []})
        return {
            "number": number,
            "kind": _KIND_CN[item.kind],
            "difficulty": item.difficulty,
            "status": _STATUS.get(item.verification.status.value, ""),
            "stem": item.stem,
            "options": item.options,
            "answer": item.answer,
            "solution": item.solution,
            "kps": kps,
        }

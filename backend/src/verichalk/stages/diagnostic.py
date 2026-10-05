"""诊断阶段：M1 用来端到端验证"网关 → 知识层 → trace → SSE"的最小阶段。M2 起由真实阶段取代入口，本阶段保留作健康自检。"""

from __future__ import annotations

from pydantic import BaseModel

from .. import trace
from ..core.ids import new_id
from ..domain.llm import Role
from ..llm import LLMRequest, get_prompt
from .base import RunContext, Stage


class DiagnosticIn(BaseModel):
    text: str


class DiagnosticOut(BaseModel):
    message_id: str
    reply: str
    kp_names: list[str]


class DiagnosticStage(Stage[DiagnosticIn, DiagnosticOut]):
    name = "diagnostic"
    input_model = DiagnosticIn
    output_model = DiagnosticOut

    async def run(self, ctx: RunContext, inp: DiagnosticIn) -> DiagnosticOut:
        await trace.progress("正在查找相关知识点")
        hits = await ctx.kb.search(inp.text, k=3)
        names = [h.name for h in hits]
        await trace.progress("正在组织回复")
        built = get_prompt("diagnostic.reply").render(
            dynamic={"text": inp.text, "kp_names": "、".join(names) or "（无）"}
        )
        message_id = new_id("msg")

        async def on_delta(t: str) -> None:
            await trace.message_delta(message_id, t)

        res = await ctx.llm.complete(
            LLMRequest(
                role=Role.fast,
                messages=built.messages,
                purpose="diagnostic.reply",
                prompt=built.ref,
                on_delta=on_delta,
                max_tokens=200,
            )
        )
        await trace.message_done(message_id, res.text)
        return DiagnosticOut(message_id=message_id, reply=res.text, kp_names=names)

"""有界工具循环（AgentLoop）：模型在预算步数内调用工具探索，随后给出最终结论（D20 的兜底路径）。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from typing import Any

from .. import trace
from ..domain.events import SpanKind
from ..domain.llm import ChatMessage, Role
from ..llm import LLMGateway, LLMRequest, LLMResult
from .registry import ToolContext, ToolRegistry


@dataclass
class AgentLoopResult:
    text: str
    steps: int
    messages: list[ChatMessage]
    tool_log: list[dict[str, Any]] = field(default_factory=list)
    exhausted: bool = False  # 步数用尽后被迫收尾
    last: LLMResult | None = None


async def run_agent_loop(
    gateway: LLMGateway,
    registry: ToolRegistry,
    ctx: ToolContext,
    *,
    role: Role,
    messages: list[ChatMessage],
    purpose: str,
    tool_names: list[str] | None = None,
    max_steps: int = 6,
) -> AgentLoopResult:
    """步骤：模型请求 → 若有工具调用则并行执行并回填 → 重复；无工具调用即为最终回答。

    步数用尽时关闭工具再请求一次，要求基于已有信息给出结论（保证总有输出，且成本有界）。
    """
    specs = registry.specs(tool_names)
    msgs = list(messages)
    log: list[dict[str, Any]] = []
    async with trace.span(SpanKind.other, f"agent_loop:{purpose}", max_steps=max_steps) as sp:
        for step in range(1, max_steps + 1):
            res = await gateway.complete(
                LLMRequest(
                    role=role, messages=msgs, purpose=f"{purpose}#step{step}", tools=specs, tool_choice="auto"
                )
            )
            if not res.tool_calls:
                sp.set(steps=step)
                return AgentLoopResult(
                    res.text, step, [*msgs, ChatMessage(role="assistant", content=res.text)], log, False, res
                )
            msgs.append(
                ChatMessage(
                    role="assistant",
                    content=res.text or None,
                    tool_calls=[
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": c.arguments},
                        }
                        for c in res.tool_calls
                    ],
                )
            )
            outcomes = await asyncio.gather(
                *(registry.call(c.name, c.arguments, ctx) for c in res.tool_calls)
            )
            for c, o in zip(res.tool_calls, outcomes, strict=True):
                msgs.append(ChatMessage(role="tool", tool_call_id=c.id, content=o.content))
                log.append({"step": step, "tool": c.name, "arguments": c.arguments, "ok": o.ok})
        # 步数用尽：关闭工具，强制收尾
        msgs.append(
            ChatMessage(role="user", content="工具调用次数已用完。请基于已获得的信息直接给出最终结论。")
        )
        res = await gateway.complete(
            replace(LLMRequest(role=role, messages=msgs, purpose=f"{purpose}#final"), tools=None)
        )
        sp.set(steps=max_steps, exhausted=True)
        return AgentLoopResult(
            res.text, max_steps, [*msgs, ChatMessage(role="assistant", content=res.text)], log, True, res
        )

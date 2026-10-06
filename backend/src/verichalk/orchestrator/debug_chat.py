"""调试台的运行分析助手：围绕"某一次运行"的多轮对话（D56）。

上下文由 `run_digest` 组织：摘要常驻在系统提示里（缓存友好：同一次运行的多轮对话共享前缀），细节通过工具按需查询。
本模块只负责对话循环：流式输出（文本增量、工具调用提示、结束 / 错误），工具步数有上限。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

from pydantic import BaseModel, Field

from ..core.errors import VerichalkError
from ..domain.llm import ChatMessage, Role
from ..llm import LLMRequest, get_prompt
from ..tools.registry import Tool, ToolContext, ToolRegistry
from ..trace import Tracer, use_tracer
from .container import Container
from .run_digest import (
    RunIndex,
    build_index,
    call_detail,
    find_events,
    item_detail,
    render_digest,
    span_detail,
    stage_output,
)

MAX_STEPS = 6  # 一轮回答里最多调用几次工具
MAX_HISTORY = 12  # 带上的历史消息条数
MAX_QUESTION = 4000

_TOOL_LABEL = {
    "get_llm_call": "查看一次模型调用的完整提示与回复",
    "get_span": "查看一个 span 的详情",
    "get_item": "查看一道题的完整核验证据",
    "get_stage_output": "查看阶段快照",
    "find_events": "搜索事件",
}


class ChatTurn(BaseModel):
    role: str  # user | assistant
    content: str = Field(max_length=20_000)


class _CallArgs(BaseModel):
    call_id: str = Field(description="摘要清单里的 call_id（形如 llm_…），或清单序号")


class _SpanArgs(BaseModel):
    span_id: str = Field(description="span 的 id（摘要的阶段列表里方括号中的 id）")


class _ItemArgs(BaseModel):
    item_id: str = Field(description="交付题的 id（摘要的题目列表里方括号中的 id）")


class _StageArgs(BaseModel):
    key: str | None = Field(
        default=None, description="阶段快照的键，如 plan、understand、produce:s1；不填则列出全部键"
    )


class _FindArgs(BaseModel):
    type: str | None = Field(
        default=None, description="事件类型，如 progress、item.status、retrieval.result、checkpoint.requested"
    )
    text: str | None = Field(default=None, description="事件 JSON 里要包含的文字")


def build_registry(idx: RunIndex) -> ToolRegistry:
    """这一次运行专属的只读工具集（闭包持有索引）。"""
    reg = ToolRegistry()

    def add(
        name: str,
        desc: str,
        params: type[BaseModel],
        fn: Callable[[Any], dict[str, Any]],
        max_chars: int = 14_000,
    ) -> None:
        async def handler(args: Any, ctx: ToolContext) -> dict[str, Any]:
            return fn(args)

        reg.register(Tool(name=name, description=desc, params=params, handler=handler, max_chars=max_chars))

    add(
        "get_llm_call",
        "取一次模型调用的完整信息：提示词各段、模型回复、参数、token、耗时、错误。",
        _CallArgs,
        lambda a: call_detail(idx, a.call_id),
    )
    add(
        "get_span",
        "取一个 span 的详情：属性、错误、子 span 与它直接包含的模型调用。",
        _SpanArgs,
        lambda a: span_detail(idx, a.span_id),
    )
    add(
        "get_item",
        "取一道交付题的完整内容与每项核验的证据（程序输出、盲解过程、边界特征等）。",
        _ItemArgs,
        lambda a: item_detail(idx, a.item_id),
    )
    add(
        "get_stage_output",
        "取阶段快照（plan 的蓝图、understand 的结果、produce 各题位的尝试与被拦原因等）；不填 key 则列出全部键。",
        _StageArgs,
        lambda a: stage_output(idx, a.key),
    )
    add(
        "find_events",
        "按类型或文字搜索事件，最多返回 25 条。",
        _FindArgs,
        lambda a: find_events(idx, a.type, a.text),
    )
    return reg


async def stream_analysis(
    c: Container, run_id: str, input_text: str, turns: list[ChatTurn]
) -> AsyncIterator[dict[str, Any]]:
    """流式回答。产出事件：`{"type": "delta", "text"}`、`{"type": "tool", "name", "label"}`、`{"type": "done"}`、`{"type": "error", "message"}`。"""
    run = await c.store.runs.get(run_id)
    events = await c.store.events.list(run_id)
    idx = build_index(run, events, input_text)
    reg = build_registry(idx)
    question = turns[-1].content.strip()[:MAX_QUESTION]
    history = [
        ChatMessage(role=t.role, content=t.content)
        for t in turns[:-1][-MAX_HISTORY:]
        if t.role in ("user", "assistant")
    ]  # type: ignore[arg-type]
    built = get_prompt("debug.analyst").render(
        stable={"digest": render_digest(idx)}, dynamic={"question": question}, history=history
    )
    q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def push_delta(text: str) -> None:
        await q.put({"type": "delta", "text": text})

    async def work() -> None:
        try:
            async with use_tracer(Tracer("debug_chat", None)):
                msgs = list(built.messages)
                for step in range(1, MAX_STEPS + 2):
                    final = step > MAX_STEPS
                    res = await c.llm.complete(
                        LLMRequest(
                            role=Role.smart,
                            messages=msgs,
                            purpose=f"debug.analyst#{step}",
                            prompt=built.ref,
                            tools=None if final else reg.specs(),
                            tool_choice=None if final else "auto",
                            temperature=0.3,
                            max_tokens=2200,
                            thinking=False,
                            on_delta=push_delta,
                        )
                    )
                    if not res.tool_calls or final:
                        break
                    msgs.append(
                        ChatMessage(
                            role="assistant",
                            content=res.text or None,
                            tool_calls=[
                                {
                                    "id": t.id,
                                    "type": "function",
                                    "function": {"name": t.name, "arguments": t.arguments},
                                }
                                for t in res.tool_calls
                            ],
                        )
                    )
                    for t in res.tool_calls:
                        await q.put(
                            {
                                "type": "tool",
                                "name": t.name,
                                "label": _TOOL_LABEL.get(t.name, t.name),
                                "arguments": _args(t.arguments),
                            }
                        )
                    outs = await asyncio.gather(
                        *(reg.call(t.name, t.arguments, ToolContext(kb=c.kb)) for t in res.tool_calls)
                    )
                    for t, o in zip(res.tool_calls, outs, strict=True):
                        msgs.append(ChatMessage(role="tool", tool_call_id=t.id, content=o.content))
                    if step == MAX_STEPS:
                        msgs.append(
                            ChatMessage(
                                role="user",
                                content="工具调用次数已用完。请基于已获得的信息直接给出结论，不要再调用工具。",
                            )
                        )
            await q.put({"type": "done"})
        except VerichalkError as e:
            await q.put({"type": "error", "message": e.user_message})
        except Exception as e:  # 分析助手出错不能影响调试台其他功能
            await q.put({"type": "error", "message": f"分析助手出错：{type(e).__name__}"})
        finally:
            await q.put(None)

    task = asyncio.create_task(work())
    try:
        while (item := await q.get()) is not None:
            yield item
    finally:
        if not task.done():
            task.cancel()


def _args(raw: str) -> dict[str, Any]:
    try:
        v = json.loads(raw or "{}")
        return v if isinstance(v, dict) else {}
    except json.JSONDecodeError:
        return {}

"""工具注册表：带类型的工具定义，供有界工具循环与阶段调用（architecture §10）。

- 参数用 Pydantic 模型声明，自动生成 OpenAI 兼容的函数 schema；
- 参数校验失败不抛异常，而是把错误作为工具结果返回给模型，让它自行修正；
- 结果序列化后按 `max_chars` 截断，控制上下文预算。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from .. import trace
from ..core.errors import VerichalkError
from ..knowledge import KnowledgeService


@dataclass
class ToolContext:
    kb: KnowledgeService


@dataclass
class Tool:
    name: str
    description: str
    params: type[BaseModel]
    handler: Callable[[Any, ToolContext], Awaitable[Any]]
    read_only: bool = True
    max_chars: int = 3500

    def spec(self) -> dict[str, Any]:
        schema = self.params.model_json_schema()
        schema.pop("title", None)
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": schema},
        }


@dataclass
class ToolOutcome:
    ok: bool
    content: str  # 给模型看的文本
    data: Any = None  # 结构化结果（供调试与阶段使用）
    error: str | None = None


def _serialize(obj: Any) -> str:
    if isinstance(obj, BaseModel):
        return obj.model_dump_json()
    if isinstance(obj, list) and obj and isinstance(obj[0], BaseModel):
        return json.dumps([o.model_dump(mode="json") for o in obj], ensure_ascii=False)
    return json.dumps(obj, ensure_ascii=False, default=str)


@dataclass
class ToolRegistry:
    _tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ValueError(f"工具重名：{tool.name}")
        self._tools[tool.name] = tool
        return tool

    def names(self) -> list[str]:
        return sorted(self._tools)

    def get(self, name: str) -> Tool:
        return self._tools[name]

    def specs(self, names: list[str] | None = None) -> list[dict[str, Any]]:
        return [self._tools[n].spec() for n in (names or self.names())]

    async def call(self, name: str, arguments: str, ctx: ToolContext) -> ToolOutcome:
        tool = self._tools.get(name)
        if tool is None:
            return ToolOutcome(
                False,
                f"错误：没有名为 {name} 的工具。可用工具：{', '.join(self.names())}",
                error="unknown_tool",
            )
        async with trace.tool_span(f"tool.{name}", arguments=arguments[:500]) as sp:
            try:
                args = tool.params.model_validate_json(arguments or "{}")
            except ValidationError as e:
                msg = "；".join(f"{'.'.join(map(str, er['loc']))}: {er['msg']}" for er in e.errors()[:4])
                sp.set(error="invalid_arguments")
                return ToolOutcome(False, f"错误：参数不合法（{msg}）", error="invalid_arguments")
            try:
                data = await tool.handler(args, ctx)
            except VerichalkError as e:
                sp.set(error=e.code)
                return ToolOutcome(False, f"错误：{e}", error=e.code)
            text = _serialize(data)
            if len(text) > tool.max_chars:
                text = text[: tool.max_chars] + "…[结果已截断]"
            sp.set(result_chars=len(text))
            return ToolOutcome(True, text, data=data)

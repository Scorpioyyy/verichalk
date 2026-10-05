"""模型调用相关的数据模型：消息、用量、调用记录（进入 `llm.call` 事件与录制文件）。"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from .common import ErrorInfo


class Role(StrEnum):
    """业务代码使用的模型角色；角色到具体模型的映射在 `config/models.yaml`。"""

    fast = "fast"
    smart = "smart"
    vision = "vision"
    judge = "judge"
    solver = "solver"


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[dict[str, Any]] | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: str  # 模型给出的 JSON 字符串，解析与校验由调用方负责


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            cached_tokens=self.cached_tokens + other.cached_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
        )


class SegmentInfo(BaseModel):
    """提示词分段（缓存友好布局：static → stable → history → dynamic）。"""

    name: str
    chars: int
    hash: str


class PromptRef(BaseModel):
    id: str
    version: int
    hash: str  # 渲染后的完整提示词哈希
    prefix_hash: str = ""  # static + stable 部分的哈希：同一值意味着前缀可被缓存
    segments: list[SegmentInfo] = Field(default_factory=list)


class LLMCallRecord(BaseModel):
    """一次模型调用的完整记录。调试台的"步骤检查器"、E2/D5/E1 指标都读它。"""

    id: str
    role: str
    model: str
    profile: str
    purpose: str = ""
    prompt: PromptRef | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    messages: list[dict[str, Any]] = Field(default_factory=list)
    response_text: str = ""
    reasoning_text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str | None = None
    usage: Usage = Field(default_factory=Usage)
    ttfb_ms: float | None = None  # 首个响应字节
    ttft_ms: float | None = None  # 首个内容 token
    total_ms: float = 0.0
    retries: int = 0
    cost: float | None = None  # 价格表缺失时为 None
    currency: str = "CNY"
    from_cassette: bool = False
    error: ErrorInfo | None = None

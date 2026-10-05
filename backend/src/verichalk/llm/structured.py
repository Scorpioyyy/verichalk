"""结构化输出：JSON 模式 + Pydantic 校验 + 带错误反馈的有限重试（metrics E5）。"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .. import trace
from ..core.errors import LLMSchemaError
from ..domain.llm import ChatMessage
from .gateway import LLMGateway, LLMRequest, LLMResult

T = TypeVar("T", bound=BaseModel)

_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.S)


def extract_json(text: str) -> object:
    """从模型输出里取出 JSON：容忍 ```json 围栏与前后多余文字。"""
    s = text.strip()
    m = _FENCE.match(s)
    if m:
        s = m.group(1)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        start = min((i for i in (s.find("{"), s.find("[")) if i >= 0), default=-1)
        if start < 0:
            raise
        decoder = json.JSONDecoder()
        obj, _ = decoder.raw_decode(s[start:])
        return obj


async def complete_json(
    gateway: LLMGateway, req: LLMRequest, model: type[T], *, max_repairs: int = 2
) -> tuple[T, LLMResult]:
    """调用模型并校验为 `model`。失败时把错误反馈给模型重试至多 `max_repairs` 次。

    返回 (解析结果, 最后一次调用结果)。`schema_retries` 记录在外层 span 上，供 E5 统计。
    """
    req = replace(req, json_mode=True)
    messages = list(req.messages)
    last_err = ""
    async with trace.tool_span("structured_output", schema=model.__name__) as sp:
        for attempt in range(max_repairs + 1):
            res = await gateway.complete(replace(req, messages=messages))
            try:
                obj = extract_json(res.text)
                parsed = model.model_validate(obj)
                sp.set(schema_retries=attempt)
                return parsed, res
            except (json.JSONDecodeError, ValidationError, ValueError) as e:
                last_err = _brief_error(e)
                messages = [
                    *messages,
                    ChatMessage(role="assistant", content=res.text),
                    ChatMessage(
                        role="user",
                        content=f"上一次输出不符合要求：{last_err}\n请只输出修正后的 JSON 对象，不要任何其他文字。",
                    ),
                ]
        sp.set(schema_retries=max_repairs + 1, failed=True)
        raise LLMSchemaError(f"结构化输出在 {max_repairs + 1} 次尝试后仍不合法：{last_err}")


def _brief_error(e: Exception) -> str:
    if isinstance(e, ValidationError):
        parts = [f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()[:5]]
        return "；".join(parts)
    return f"不是合法的 JSON（{e}）"

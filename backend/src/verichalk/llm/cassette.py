"""录制回放（cassette）：模型响应以请求哈希为键存储，回放时逐位复现。

键 = hash(模型, 消息, 影响输出的参数)；不含角色名、purpose、回调——换角色指向同一模型不应使录制失效。
文件落盘前统一脱敏。回放用于回归评测与集成测试（零成本、确定）；提示词或模型变了则键变，需要重新录制。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..core.errors import ReplayMiss
from ..core.redact import redact
from ..domain.llm import ToolCall, Usage


class CassetteEntry(BaseModel):
    key: str
    model: str
    purpose: str = ""
    text: str = ""
    reasoning_text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str | None = None
    usage: Usage = Field(default_factory=Usage)
    ttfb_ms: float | None = None
    ttft_ms: float | None = None
    total_ms: float = 0.0


def request_key(model: str, messages: list[dict[str, Any]], params: dict[str, Any]) -> str:
    canon = json.dumps(
        {"m": model, "msgs": messages, "p": params}, ensure_ascii=False, sort_keys=True, default=str
    )
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:32]


class CassetteStore:
    def __init__(self, root: Path, namespace: str = "default") -> None:
        self.dir = root / namespace

    def _path(self, key: str) -> Path:
        return self.dir / f"{key}.json"

    def load(self, key: str) -> CassetteEntry:
        p = self._path(key)
        if not p.exists():
            raise ReplayMiss(f"没有找到录制：{key}（提示词、模型或参数变了？请用 record 重新录制）")
        return CassetteEntry.model_validate_json(p.read_text(encoding="utf-8"))

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def save(self, entry: CassetteEntry) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        text = redact(entry.model_dump_json(indent=1))
        self._path(entry.key).write_text(text, encoding="utf-8")

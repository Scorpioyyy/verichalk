"""提示词管理：版本化的提示词文件 + 缓存友好的分段布局（architecture §7，metrics E2）。

文件格式（`prompts/<阶段>/<名称>.md`）：

    ---
    id: understand.brief
    version: 1
    role: fast
    ---
    <!-- segment:static -->     所有调用完全相同（不得含变量）→ 最稳定的前缀
    <!-- segment:stable -->     会话内基本不变的上下文（可含变量）
    <!-- segment:dynamic -->    本次调用的内容（可含变量）

渲染顺序固定为 [system = static + stable][history…][user = dynamic]，只允许在尾部追加，
这样同一阶段的重复调用、同一会话的多轮调用能最大化命中服务商的前缀缓存。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import jinja2
import yaml

from ..core.errors import ConfigError
from ..domain.llm import ChatMessage, PromptRef, SegmentInfo

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_SEG_RE = re.compile(r"<!--\s*segment:(static|stable|dynamic)\s*-->")
_FRONT_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)

_env = jinja2.Environment(
    undefined=jinja2.StrictUndefined,
    keep_trailing_newline=False,
    trim_blocks=True,
    lstrip_blocks=True,
    autoescape=False,
)


def _h(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class BuiltPrompt:
    messages: list[ChatMessage]
    ref: PromptRef


@dataclass(frozen=True)
class PromptTemplate:
    id: str
    version: int
    role: str
    static: str
    stable: str
    dynamic: str
    description: str = ""

    def render(
        self,
        *,
        stable: dict[str, Any] | None = None,
        dynamic: dict[str, Any] | None = None,
        history: list[ChatMessage] | None = None,
    ) -> BuiltPrompt:
        static_text = self.static.strip()
        stable_text = (
            _env.from_string(self.stable).render(**(stable or {})).strip() if self.stable.strip() else ""
        )
        dynamic_text = _env.from_string(self.dynamic).render(**(dynamic or {})).strip()
        system = static_text + (("\n\n" + stable_text) if stable_text else "")
        msgs = [ChatMessage(role="system", content=system)]
        msgs += list(history or [])
        msgs.append(ChatMessage(role="user", content=dynamic_text))
        hist_text = "".join(str(m.content) for m in (history or []))
        segments = [SegmentInfo(name="static", chars=len(static_text), hash=_h(static_text))]
        if stable_text:
            segments.append(SegmentInfo(name="stable", chars=len(stable_text), hash=_h(stable_text)))
        if history:
            segments.append(SegmentInfo(name="history", chars=len(hist_text), hash=_h(hist_text)))
        segments.append(SegmentInfo(name="dynamic", chars=len(dynamic_text), hash=_h(dynamic_text)))
        full = system + hist_text + dynamic_text
        ref = PromptRef(
            id=self.id, version=self.version, hash=_h(full), prefix_hash=_h(system), segments=segments
        )
        return BuiltPrompt(messages=msgs, ref=ref)


def parse_prompt(text: str, source: str = "") -> PromptTemplate:
    m = _FRONT_RE.match(text.replace("\r\n", "\n"))
    if not m:
        raise ConfigError(f"提示词缺少前置元数据：{source}")
    meta = yaml.safe_load(m.group(1)) or {}
    for k in ("id", "version", "role"):
        if k not in meta:
            raise ConfigError(f"提示词元数据缺少 {k}：{source}")
    body = text.replace("\r\n", "\n")[m.end() :]
    parts = _SEG_RE.split(body)  # ['', 'static', text, 'stable', text, ...]
    segs: dict[str, str] = {}
    for name, content in zip(parts[1::2], parts[2::2], strict=True):
        if name in segs:
            raise ConfigError(f"提示词分段重复 {name}：{source}")
        segs[name] = content.strip()
    if "static" not in segs or "dynamic" not in segs:
        raise ConfigError(f"提示词必须包含 static 与 dynamic 分段：{source}")
    # static 段不得含 Jinja 变量或语句：它必须在所有调用中逐字相同
    if re.search(r"\{\{|\{%", segs["static"]):
        raise ConfigError(f"static 分段不得含模板变量（会破坏前缀缓存）：{source}")
    return PromptTemplate(
        id=str(meta["id"]),
        version=int(meta["version"]),
        role=str(meta["role"]),
        static=segs["static"],
        stable=segs.get("stable", ""),
        dynamic=segs["dynamic"],
        description=str(meta.get("description", "")),
    )


@cache
def get_prompt(prompt_id: str) -> PromptTemplate:
    """按 id 加载提示词：`understand.brief` → `prompts/understand/brief.md`。"""
    path = PROMPTS_DIR.joinpath(*prompt_id.split(".")).with_suffix(".md")
    if not path.exists():
        raise ConfigError(f"提示词不存在：{prompt_id}")
    tpl = parse_prompt(path.read_text(encoding="utf-8"), str(path))
    if tpl.id != prompt_id:
        raise ConfigError(f"提示词 id 与路径不一致：{prompt_id} vs {tpl.id}")
    return tpl


def all_prompt_ids() -> list[str]:
    return sorted(
        ".".join(p.relative_to(PROMPTS_DIR).with_suffix("").parts) for p in PROMPTS_DIR.rglob("*.md")
    )

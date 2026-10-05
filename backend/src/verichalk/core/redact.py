"""脱敏：密钥与端点主机名不得出现在日志、事件、录制、错误文本里（CLAUDE.md §2.10、§4）。

两层防御：①按模式（`sk-…`、`Bearer …`、`*.aliyuncs.com`）；②按进程环境里实际存在的密钥与端点主机名。
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlparse

_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{12,}"), "sk-***"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}"), "Bearer ***"),
    (re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-]*\.aliyuncs\.com"), "<llm-host>"),
]
_ENV_KEYS = ("DASHSCOPE_API_KEY", "DASHSCOPE_INTL_API_KEY", "TWINE_PASSWORD", "VERICHALK_DEBUG_TOKEN")
_ENV_URLS = ("DASHSCOPE_BASE_URL", "DASHSCOPE_INTL_BASE_URL")


def _secrets() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for k in _ENV_KEYS:
        v = os.environ.get(k)
        if v and len(v) >= 8:
            out.append((v, "***"))
    for k in _ENV_URLS:
        host = urlparse(os.environ.get(k, "")).hostname
        if host:
            out.append((host, "<llm-host>"))
    return out


def redact(text: object) -> str:
    """返回脱敏后的文本。非字符串输入先 `str()`。"""
    s = text if isinstance(text, str) else str(text)
    for secret, repl in _secrets():
        if secret in s:
            s = s.replace(secret, repl)
    for pat, repl in _PATTERNS:
        s = pat.sub(repl, s)
    return s

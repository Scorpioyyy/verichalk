"""日志：统一格式 + 脱敏过滤器（所有记录在输出前经 `redact`）。"""

from __future__ import annotations

import logging

from .redact import redact


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        if record.exc_info:
            # 异常文本在格式化时才生成；这里预先格式化并脱敏，避免堆栈里带出端点主机名
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    for h in list(root.handlers):
        if getattr(h, "_verichalk", False):
            root.removeHandler(h)
    handler = logging.StreamHandler()
    handler._verichalk = True  # type: ignore[attr-defined]
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter())
    root.addHandler(handler)
    # httpx 在 INFO 级别会打印完整 URL（含端点主机名），降级
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

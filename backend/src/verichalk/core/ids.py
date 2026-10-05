"""带前缀、按时间可排序的 ID（ULID 风格：48 位毫秒时间戳 + 80 位随机数，Crockford Base32）。"""

from __future__ import annotations

import os
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_id(prefix: str) -> str:
    """生成如 `run_01K8…` 的 ID；同一毫秒内的次序不保证，但全局唯一。"""
    n = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), "big")
    chars = []
    for _ in range(26):
        chars.append(_ALPHABET[n & 31])
        n >>= 5
    return f"{prefix}_{''.join(reversed(chars))}"

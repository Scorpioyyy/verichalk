"""统计工具：分位数与 Wilson 置信区间（所有比例类指标都报告区间，metrics.md §1.4）。"""

from __future__ import annotations

import math
from collections.abc import Sequence


def percentile(values: Sequence[float], q: float) -> float | None:
    """线性插值分位数（q ∈ [0,1]）；空序列返回 None。"""
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    pos = q * (len(s) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    return float(s[lo] + (s[hi] - s[lo]) * (pos - lo))


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """比例的 Wilson 置信区间（默认 95%）。n=0 时返回 (0, 1)。"""
    if n <= 0:
        return 0.0, 1.0
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)

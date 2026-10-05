"""统计工具的防御性测试（其余统计测试在 test_metrics.py）。"""

from __future__ import annotations

from verichalk.metrics.stats import wilson


def test_wilson_never_crashes_when_successes_exceed_n() -> None:
    lo, hi = wilson(139, 78)  # 防御：分子大于分母不应抛 math domain error
    assert 0.0 <= lo <= hi <= 1.0

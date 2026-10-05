"""图形：`FigureSpec`（结构化规格）→ SVG。

规格由写题阶段给出（或老师在编辑器里改），渲染是纯函数；导出与前端预览共用这一份 SVG。
新增图形类型 = 在 `draw.py` 加一个绘制函数并登记到 `KINDS`（architecture §10）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..domain.paper import FigureSpec
from .draw import FigureError, Rendered, bar_chart, grid, number_line, shape

KINDS: dict[str, Callable[[dict[str, Any]], Rendered]] = {
    "number_line": number_line,
    "bar_chart": bar_chart,
    "shape": shape,
    "grid": grid,
}


def render_figure(spec: FigureSpec) -> Rendered:
    """渲染一个图形规格；类型未知或规格不合法时抛 `FigureError`。"""
    fn = KINDS.get(spec.kind)
    if fn is None:
        raise FigureError(f"不支持的图形类型：{spec.kind}")
    return fn(spec.params)


__all__ = ["KINDS", "FigureError", "Rendered", "render_figure"]

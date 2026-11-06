"""图形：`FigureSpec`（结构化规格）→ SVG。

规格由写题阶段给出（或老师在编辑器里改），渲染是纯函数；导出与前端预览共用这一份 SVG。
新增图形类型 = 在 `draw.py` 加一个绘制函数并登记到 `KINDS`（architecture §10）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..domain.paper import FigureSpec
from .describe import describe_figure
from .draw import FigureError, Rendered, bar_chart, clock, grid, line_chart, number_line, pie_chart, shape

KINDS: dict[str, Callable[[dict[str, Any]], Rendered]] = {
    "number_line": number_line,
    "bar_chart": bar_chart,
    "line_chart": line_chart,
    "pie_chart": pie_chart,
    "shape": shape,
    "clock": clock,
    "grid": grid,
}


def render_figure(spec: FigureSpec) -> Rendered:
    """渲染一个图形规格；类型未知或规格不合法时抛 `FigureError`（不会抛其他异常）。"""
    fn = KINDS.get(spec.kind)
    if fn is None:
        raise FigureError(f"不支持的图形类型：{spec.kind}（只能是 {'、'.join(KINDS)}）")
    try:
        return fn(spec.params)
    except FigureError:
        raise
    except (ArithmeticError, ValueError, TypeError, KeyError, IndexError, AttributeError) as e:
        # 规格来自写题模型：数值写成 "?"、列表长度不对、缺字段……一律是"规格不合法"，不能让运行崩溃
        raise FigureError(
            f"{spec.kind} 的参数不合法（{type(e).__name__}）：数值要写成数字，字段与类型见说明"
        ) from e


__all__ = ["KINDS", "FigureError", "Rendered", "describe_figure", "render_figure"]

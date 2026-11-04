"""图形规格 → 文字描述（确定性）。核验用的模型看不到图片：盲解、题面评审、超纲抽取读的是这段文字。
图里的数据全部来自规格，所以文字与图一致；描述只陈述规格里有的内容，不推断。"""

from __future__ import annotations

from typing import Any

from ..domain.paper import FigureSpec
from .draw import SHAPE_LABELS

_SHAPE_CN = {
    "rectangle": "长方形",
    "square": "正方形",
    "triangle": "三角形",
    "parallelogram": "平行四边形",
    "trapezoid": "梯形",
    "circle": "圆",
}
_LABEL_CN = {
    "length": "长",
    "width": "宽",
    "side": "边",
    "base": "底",
    "left": "左边",
    "right": "右边",
    "height": "高",
    "top": "上底",
    "bottom": "下底",
    "radius": "半径",
    "diameter": "直径",
}


def _labels(d: dict[str, Any]) -> str:
    return "，".join(f"{_LABEL_CN.get(k, k)}标着“{v}”" for k, v in d.items() if v not in (None, ""))


def describe_figure(spec: FigureSpec) -> str:
    p = spec.params
    if spec.kind == "bar_chart":
        pairs = "，".join(
            f"{c} {v}" for c, v in zip(p.get("categories", []), p.get("values", []), strict=False)
        )
        head = f"条形统计图《{p['title']}》" if p.get("title") else "条形统计图"
        unit = f"（纵轴：{p['y_label']}）" if p.get("y_label") else ""
        return f"{head}{unit}：{pairs}"
    if spec.kind == "number_line":
        pts = "，".join(
            f"{pt.get('label') or '点'}在 {pt.get('value')}" + ("（空心）" if pt.get("hollow") else "")
            for pt in p.get("points") or []
        )
        hidden = (
            f"；{('、'.join(str(x) for x in p['hide_labels']))} 处的刻度数没有标出"
            if p.get("hide_labels")
            else ""
        )
        unit = f"（单位：{p['unit']}）" if p.get("unit") else ""
        return (
            f"数轴{unit}：从 {p.get('min')} 到 {p.get('max')}，每格 {p.get('step', 1)}"
            + (f"；{pts}" if pts else "")
            + hidden
        )
    if spec.kind == "shape":
        kind = str(p.get("shape"))
        name = _SHAPE_CN.get(kind, kind)
        extra = "（有一个直角）" if p.get("right_angle") else ""
        drawn = SHAPE_LABELS.get(kind, ())
        lab = _labels({k: v for k, v in (p.get("labels") or {}).items() if k in drawn})
        return f"一个{name}{extra}" + (f"：{lab}" if lab else "")
    if spec.kind == "grid":
        bits = [
            f"方格图，{p.get('rows')} 行 {p.get('cols')} 列"
            + ("，带坐标（原点在左下角）" if p.get("coordinate") else "")
        ]
        if p.get("shaded"):
            bits.append(
                f"涂色的格子 {len(p['shaded'])} 个："
                + "、".join(f"第{r + 1}行第{c + 1}列" for r, c in p["shaded"])
            )
        if p.get("points"):
            bits.append(
                "点："
                + "、".join(f"{pt.get('label') or ''}({pt.get('x')},{pt.get('y')})" for pt in p["points"])
            )
        if p.get("axis"):
            ax = p["axis"]
            bits.append(
                f"{'竖直' if ax.get('orientation', 'vertical') == 'vertical' else '水平'}的对称轴在第 {ax.get('at')} 条格线"
            )
        if p.get("segments"):
            bits.append(f"画了 {len(p['segments'])} 条线段")
        return "；".join(bits)
    return f"图（{spec.kind}）"

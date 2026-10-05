"""首批图形的 SVG 绘制：数轴、条形统计图、几何图形、方格图。

约定：每个绘制函数是纯函数 `params → Rendered`（同规格同输出，无随机、无外部依赖）；坐标单位为 px，
文字用 `FONT` 回退栈（浏览器与 Typst 都能找到中文字体）；画不出来的规格抛 `FigureError`，由调用方降级。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from xml.sax.saxutils import escape

FONT = "Noto Sans SC, Microsoft YaHei, SimHei, Noto Sans CJK SC, sans-serif"
INK = "#222"
GRAY = "#888"
FILL = "#cfe3ff"
BAR = "#6aa0e8"


class FigureError(ValueError):
    """规格不合法或无法绘制。"""


@dataclass(frozen=True)
class Rendered:
    svg: str
    width: float
    height: float


def _fmt(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".") if abs(v - round(v)) > 1e-9 else str(round(v))


def _num(v: Any) -> str:
    """刻度标签：整数不带小数点；其余按 Decimal 规范化，避免 0.30000000000000004。"""
    d = Decimal(str(v))
    s = format(d.normalize(), "f")
    return s


def _svg(w: float, h: float, body: str) -> Rendered:
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_fmt(w)}" height="{_fmt(h)}" '
        f'viewBox="0 0 {_fmt(w)} {_fmt(h)}" font-family="{FONT}" font-size="14" fill="{INK}">{body}</svg>'
    )
    return Rendered(svg, w, h)


def _text(x: float, y: float, s: str, *, anchor: str = "middle", size: int = 14, fill: str = INK) -> str:
    return (
        f'<text x="{_fmt(x)}" y="{_fmt(y)}" text-anchor="{anchor}" font-size="{size}" fill="{fill}">'
        f"{escape(str(s))}</text>"
    )


def _line(
    x1: float, y1: float, x2: float, y2: float, *, w: float = 1.2, color: str = INK, dash: str = ""
) -> str:
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (
        f'<line x1="{_fmt(x1)}" y1="{_fmt(y1)}" x2="{_fmt(x2)}" y2="{_fmt(y2)}" '
        f'stroke="{color}" stroke-width="{w}"{d}/>'
    )


def _need(params: dict[str, Any], key: str) -> Any:
    if key not in params:
        raise FigureError(f"缺少参数：{key}")
    return params[key]


# ---- 数轴 ----
def number_line(p: dict[str, Any]) -> Rendered:
    lo, hi = Decimal(str(_need(p, "min"))), Decimal(str(_need(p, "max")))
    step = Decimal(str(p.get("step", 1)))
    if hi <= lo or step <= 0 or (hi - lo) / step > 60:
        raise FigureError("数轴范围或刻度不合法")
    labels: dict[str, str] = {str(k): str(v) for k, v in (p.get("labels") or {}).items()}
    hidden = {_num(v) for v in p.get("hide_labels", [])}
    points = p.get("points") or []
    w, h, pad = float(p.get("width", 520)), 86.0, 30.0
    span = w - 2 * pad
    ax = h - 36

    def x_of(v: Decimal) -> float:
        return pad + float((v - lo) / (hi - lo)) * span

    parts = [_line(pad - 14, ax, w - pad + 14, ax, w=1.6)]
    parts.append(f'<path d="M{_fmt(w - pad + 14)} {_fmt(ax)} l-8 -4 v8 z" fill="{INK}"/>')
    n = int((hi - lo) / step)
    for i in range(n + 1):
        v = lo + step * i
        x = x_of(v)
        parts.append(_line(x, ax - 6, x, ax + 6))
        key = _num(v)
        if key in hidden:
            continue
        parts.append(_text(x, ax + 24, labels.get(key, key)))
    for pt in points:
        v = Decimal(str(_need(pt, "value")))
        if not lo <= v <= hi:
            raise FigureError("数轴上的点超出范围")
        x = x_of(v)
        fill = "#fff" if pt.get("hollow") else "#e0443e"
        parts.append(
            f'<circle cx="{_fmt(x)}" cy="{_fmt(ax)}" r="4.5" fill="{fill}" stroke="#e0443e" stroke-width="1.6"/>'
        )
        if pt.get("label"):
            parts.append(_text(x, ax - 14, str(pt["label"]), fill="#e0443e"))
    if p.get("unit"):
        parts.append(_text(w - pad + 14, ax - 10, str(p["unit"]), anchor="end", size=12, fill=GRAY))
    return _svg(w, h, "".join(parts))


# ---- 条形统计图 ----
def bar_chart(p: dict[str, Any]) -> Rendered:
    cats = [str(c) for c in _need(p, "categories")]
    vals = [Decimal(str(v)) for v in _need(p, "values")]
    if not cats or len(cats) != len(vals) or len(cats) > 12 or any(v < 0 for v in vals):
        raise FigureError("条形图的类别与数值不匹配")
    ymax = Decimal(str(p["y_max"])) if p.get("y_max") is not None else None
    if ymax is None:
        top = max(vals) or Decimal(1)
        mag = Decimal(10) ** (len(str(int(top))) - 1)
        ymax = (top / mag).to_integral_value(rounding="ROUND_CEILING") * mag
    ystep = Decimal(str(p["y_step"])) if p.get("y_step") is not None else ymax / 5
    if ystep <= 0 or ymax / ystep > 20:
        raise FigureError("纵轴刻度不合法")
    left, right, top_pad, bottom = 56.0, 16.0, 44.0 if p.get("title") else 28.0, 40.0
    plot_h = 170.0
    slot = 54.0
    w = left + right + slot * len(cats)
    h = top_pad + plot_h + bottom
    parts = []
    if p.get("title"):
        parts.append(_text(w / 2, 22, str(p["title"]), size=15))
    if p.get("y_label"):
        parts.append(_text(left - 8, top_pad - 10, str(p["y_label"]), anchor="end", size=12, fill=GRAY))

    def y_of(v: Decimal) -> float:
        return top_pad + plot_h - float(v / ymax) * plot_h

    n = int(ymax / ystep)
    for i in range(n + 1):
        v = ystep * i
        y = y_of(v)
        parts.append(_line(left, y, w - right, y, w=0.6 if i else 1.4, color="#ccc" if i else INK))
        parts.append(_text(left - 8, y + 5, _num(v), anchor="end", size=12))
    parts.append(_line(left, top_pad, left, top_pad + plot_h, w=1.4))
    for i, (c, v) in enumerate(zip(cats, vals, strict=True)):
        x0 = left + slot * i + slot * 0.2
        bw = slot * 0.6
        y = y_of(v)
        parts.append(
            f'<rect x="{_fmt(x0)}" y="{_fmt(y)}" width="{_fmt(bw)}" height="{_fmt(top_pad + plot_h - y)}" fill="{BAR}"/>'
        )
        if p.get("show_values", True):
            parts.append(_text(x0 + bw / 2, y - 5, _num(v), size=12))
        parts.append(_text(x0 + bw / 2, top_pad + plot_h + 20, c, size=13))
    if p.get("x_label"):
        parts.append(_text(w - right, h - 6, str(p["x_label"]), anchor="end", size=12, fill=GRAY))
    return _svg(w, h, "".join(parts))


# ---- 几何图形 ----
def _label_at(x: float, y: float, s: Any, *, anchor: str = "middle") -> str:
    return _text(x, y, str(s), anchor=anchor) if s not in (None, "") else ""


def _right_angle(x: float, y: float, dx1: float, dy1: float, dx2: float, dy2: float, r: float = 9) -> str:
    pts = [(x + dx1 * r, y + dy1 * r), (x + (dx1 + dx2) * r, y + (dy1 + dy2) * r), (x + dx2 * r, y + dy2 * r)]
    d = "M" + " L".join(f"{_fmt(a)} {_fmt(b)}" for a, b in pts)
    return f'<path d="{d}" fill="none" stroke="{INK}" stroke-width="1"/>'


def shape(p: dict[str, Any]) -> Rendered:
    kind = str(_need(p, "shape"))
    L = p.get("labels") or {}
    w, h = 300.0, 200.0
    poly = ""
    extra = ""
    if kind in ("rectangle", "square"):
        rw, rh = (150.0, 150.0) if kind == "square" else (190.0, 110.0)
        x0, y0 = (w - rw) / 2, (h - rh) / 2
        poly = f'<rect x="{_fmt(x0)}" y="{_fmt(y0)}" width="{_fmt(rw)}" height="{_fmt(rh)}"/>'
        extra += _right_angle(x0, y0, 1, 0, 0, 1) + _right_angle(x0 + rw, y0, -1, 0, 0, 1)
        extra += _right_angle(x0, y0 + rh, 1, 0, 0, -1) + _right_angle(x0 + rw, y0 + rh, -1, 0, 0, -1)
        extra += _label_at(w / 2, y0 + rh + 22, L.get("length") or L.get("side"))
        extra += _label_at(x0 - 8, h / 2 + 5, L.get("width") or L.get("side"), anchor="end")
    elif kind == "triangle":
        pts = (
            [(40.0, 160.0), (260.0, 160.0), (150.0, 30.0)]
            if not p.get("right_angle")
            else [(60.0, 160.0), (240.0, 160.0), (60.0, 40.0)]
        )
        poly = f'<polygon points="{" ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in pts)}"/>'
        (ax, ay), (bx, by), (cx, cy) = pts
        extra += _label_at((ax + bx) / 2, ay + 22, L.get("base"))
        extra += _label_at((ax + cx) / 2 - 12, (ay + cy) / 2, L.get("left"), anchor="end")
        extra += _label_at((bx + cx) / 2 + 12, (by + cy) / 2, L.get("right"), anchor="start")
        if p.get("right_angle"):
            extra += _right_angle(ax, ay, 1, 0, 0, -1)
        if L.get("height") and not p.get("right_angle"):
            extra += _line(cx, cy, cx, ay, dash="4 3", w=1) + _label_at(
                cx + 6, (cy + ay) / 2 + 5, L["height"], anchor="start"
            )
            extra += _right_angle(cx, ay, -1, 0, 0, -1, 8)
    elif kind == "parallelogram":
        x0, y0, bw, sk, ph = 50.0, 160.0, 170.0, 60.0, 110.0
        pts = [(x0, y0), (x0 + bw, y0), (x0 + bw + sk, y0 - ph), (x0 + sk, y0 - ph)]
        poly = f'<polygon points="{" ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in pts)}"/>'
        extra += _label_at(x0 + bw / 2, y0 + 22, L.get("base"))
        extra += _label_at(x0 + bw + sk / 2 + 12, y0 - ph / 2, L.get("side"), anchor="start")
        if L.get("height"):
            extra += _line(x0 + sk, y0 - ph, x0 + sk, y0, dash="4 3", w=1) + _label_at(
                x0 + sk + 6, y0 - ph / 2 + 5, L["height"], anchor="start"
            )
            extra += _right_angle(x0 + sk, y0, 1, 0, 0, -1, 8)
    elif kind == "trapezoid":
        x0, y0, bw, tw, ph = 40.0, 160.0, 220.0, 120.0, 110.0
        off = (bw - tw) / 2
        pts = [(x0, y0), (x0 + bw, y0), (x0 + off + tw, y0 - ph), (x0 + off, y0 - ph)]
        poly = f'<polygon points="{" ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in pts)}"/>'
        extra += _label_at(x0 + bw / 2, y0 + 22, L.get("bottom"))
        extra += _label_at(x0 + bw / 2, y0 - ph - 8, L.get("top"))
        if L.get("height"):
            extra += _line(x0 + off, y0 - ph, x0 + off, y0, dash="4 3", w=1) + _label_at(
                x0 + off + 6, y0 - ph / 2 + 5, L["height"], anchor="start"
            )
    elif kind == "circle":
        cx, cy, r = 150.0, 100.0, 78.0
        poly = f'<circle cx="{_fmt(cx)}" cy="{_fmt(cy)}" r="{_fmt(r)}"/>'
        extra += f'<circle cx="{_fmt(cx)}" cy="{_fmt(cy)}" r="2.5" fill="{INK}"/>'
        if L.get("radius"):
            extra += _line(cx, cy, cx + r, cy) + _label_at(cx + r / 2, cy - 8, L["radius"])
        if L.get("diameter"):
            extra += _line(cx - r, cy, cx + r, cy) + _label_at(cx, cy - 8, L["diameter"])
    else:
        raise FigureError(f"不支持的图形：{kind}")
    body = f'<g fill="{FILL}" stroke="{INK}" stroke-width="1.6" stroke-linejoin="round">{poly}</g>{extra}'
    return _svg(w, h, body)


# ---- 方格图 / 坐标 ----
def grid(p: dict[str, Any]) -> Rendered:
    rows, cols = int(_need(p, "rows")), int(_need(p, "cols"))
    if not (1 <= rows <= 20 and 1 <= cols <= 24):
        raise FigureError("方格图尺寸不合法")
    cell = float(p.get("cell", 26))
    coord = bool(p.get("coordinate"))
    left, top = (30.0, 16.0) if coord else (10.0, 10.0)
    w, h = left + cols * cell + 20, top + rows * cell + (30 if coord else 10)
    parts = []
    for r, c in p.get("shaded") or []:
        if not (0 <= r < rows and 0 <= c < cols):
            raise FigureError("涂色格超出范围")
        parts.append(
            f'<rect x="{_fmt(left + c * cell)}" y="{_fmt(top + r * cell)}" width="{_fmt(cell)}" height="{_fmt(cell)}" fill="{FILL}"/>'
        )
    for i in range(rows + 1):
        parts.append(_line(left, top + i * cell, left + cols * cell, top + i * cell, w=0.7, color="#999"))
    for j in range(cols + 1):
        parts.append(_line(left + j * cell, top, left + j * cell, top + rows * cell, w=0.7, color="#999"))
    axis = p.get("axis")  # 对称轴：{"orientation": "vertical"|"horizontal", "at": 格线序号}
    if axis:
        at = float(_need(axis, "at"))
        if axis.get("orientation", "vertical") == "vertical":
            parts.append(
                _line(
                    left + at * cell,
                    top - 6,
                    left + at * cell,
                    top + rows * cell + 6,
                    w=1.4,
                    color="#e0443e",
                    dash="6 4",
                )
            )
        else:
            parts.append(
                _line(
                    left - 6,
                    top + at * cell,
                    left + cols * cell + 6,
                    top + at * cell,
                    w=1.4,
                    color="#e0443e",
                    dash="6 4",
                )
            )
    for seg in p.get("segments") or []:
        (x1, y1), (x2, y2) = seg
        parts.append(_line(left + x1 * cell, top + y1 * cell, left + x2 * cell, top + y2 * cell, w=2))
    if coord:  # 坐标：x 向右、y 向上，原点在左下角格点
        parts.append(_line(left, top + rows * cell, left + cols * cell + 12, top + rows * cell, w=1.6))
        parts.append(_line(left, top + rows * cell, left, top - 10, w=1.6))
        for j in range(cols + 1):
            parts.append(_text(left + j * cell, top + rows * cell + 18, str(j), size=11))
        for i in range(rows + 1):
            parts.append(_text(left - 8, top + (rows - i) * cell + 4, str(i), anchor="end", size=11))
    for pt in p.get("points") or []:
        x, y = float(_need(pt, "x")), float(_need(pt, "y"))
        px = left + x * cell
        py = top + (rows - y) * cell if coord else top + y * cell
        parts.append(f'<circle cx="{_fmt(px)}" cy="{_fmt(py)}" r="3.8" fill="#e0443e"/>')
        if pt.get("label"):
            parts.append(_text(px + 7, py - 6, str(pt["label"]), anchor="start", size=13, fill="#e0443e"))
    return _svg(w, h, "".join(parts))

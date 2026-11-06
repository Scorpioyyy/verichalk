"""图形的 SVG 绘制：数轴、统计图（条形 / 折线 / 扇形，条形与折线可复式）、几何图形、钟面、方格图。

约定：每个绘制函数是纯函数 `params → Rendered`（同规格同输出，无随机、无外部依赖）；坐标单位为 px，
文字用 `FONT` 回退栈（浏览器与 Typst 都能找到中文字体）；画不出来的规格抛 `FigureError`，由调用方降级。
"""

from __future__ import annotations

import math
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
SERIES = ["#6aa0e8", "#f0a04b", "#7cc47c"]  # 复式统计图各组的颜色（最多 3 组）


def _series(p: dict[str, Any]) -> tuple[list[str], list[tuple[str, list[Decimal]]]]:
    """统计图的数据：`categories` + `values`（单式），或 `categories` + `series: [{name, values}]`（复式）。"""
    cats = [str(c) for c in _need(p, "categories")]
    if p.get("series"):
        ser = [(str(x.get("name", "")), [Decimal(str(v)) for v in _need(x, "values")]) for x in p["series"]]
    else:
        ser = [("", [Decimal(str(v)) for v in _need(p, "values")])]
    if (
        not cats
        or len(cats) > 12
        or not 1 <= len(ser) <= len(SERIES)
        or any(len(v) != len(cats) for _, v in ser)
        or any(x < 0 for _, v in ser for x in v)
    ):
        raise FigureError(
            "统计图的类别与数值不匹配：每组 values 的个数要与 categories 相同、数值非负；最多 12 类、3 组"
        )
    return cats, ser


def _value_axis(
    p: dict[str, Any], vals: list[Decimal], slot: float, n: int
) -> tuple[list[str], float, float, float, float, Any]:
    """统计图的公共部分：标题、纵轴刻度与网格线、图例位置。返回 (parts, w, h, left, base_y, y_of)。"""
    ymax = Decimal(str(p["y_max"])) if p.get("y_max") is not None else None
    if ymax is None:
        top = max(vals) or Decimal(1)
        mag = Decimal(10) ** (len(str(int(top))) - 1)
        ymax = (top / mag).to_integral_value(rounding="ROUND_CEILING") * mag
    ystep = Decimal(str(p["y_step"])) if p.get("y_step") is not None else ymax / 5
    if ystep <= 0 or ymax / ystep > 20:
        raise FigureError("纵轴刻度不合法")
    left, right, top_pad, bottom = 56.0, 16.0, 52.0 if p.get("title") else 28.0, 40.0
    plot_h = 170.0
    w = left + right + slot * n
    h = top_pad + plot_h + bottom
    parts = []
    if p.get("title"):
        parts.append(_text(w / 2, 22, str(p["title"]), size=15))
    if p.get("y_label"):
        # 从画布左边缘写起：右对齐到纵轴时，长一点的标签（"重量/千克"）会超出画布被截掉
        parts.append(_text(4, top_pad - 10, str(p["y_label"]), anchor="start", size=12, fill=GRAY))

    def y_of(v: Decimal) -> float:
        return top_pad + plot_h - float(v / ymax) * plot_h

    for i in range(int(ymax / ystep) + 1):
        v = ystep * i
        y = y_of(v)
        parts.append(_line(left, y, w - right, y, w=0.6 if i else 1.4, color="#ccc" if i else INK))
        parts.append(_text(left - 8, y + 5, _num(v), anchor="end", size=12))
    parts.append(_line(left, top_pad, left, top_pad + plot_h, w=1.4))
    if p.get("x_label"):
        parts.append(_text(w - right, h - 6, str(p["x_label"]), anchor="end", size=12, fill=GRAY))
    return parts, w, h, left, top_pad + plot_h, y_of


def _legend(names: list[str], w: float, *, line: bool = False) -> str:
    """复式统计图的图例：右上角，一组一行。"""
    out = []
    for i, name in enumerate(names):
        y = 36 + 16 * i
        mark = (
            _line(w - 92, y - 4, w - 76, y - 4, w=2.4, color=SERIES[i])
            if line
            else f'<rect x="{_fmt(w - 90)}" y="{_fmt(y - 10)}" width="12" height="10" fill="{SERIES[i]}"/>'
        )
        out.append(mark + _text(w - 72, y, name, anchor="start", size=12))
    return "".join(out)


def bar_chart(p: dict[str, Any]) -> Rendered:
    """条形统计图；给 `series` 时是复式条形统计图（每类并排几根直条，带图例）。"""
    cats, ser = _series(p)
    k = len(ser)
    slot = 54.0 if k == 1 else 26.0 * k + 24
    parts, w, h, left, base, y_of = _value_axis(p, [v for _, vs in ser for v in vs], slot, len(cats))
    if k > 1:
        w += 96  # 图例的位置
        parts.append(_legend([n for n, _ in ser], w))
    bw = slot * 0.6 if k == 1 else 26.0
    for i, c in enumerate(cats):
        x_cat = left + slot * i + (slot - bw * k) / 2
        for j, (_, vs) in enumerate(ser):
            x0, y = x_cat + bw * j, y_of(vs[i])
            parts.append(
                f'<rect x="{_fmt(x0)}" y="{_fmt(y)}" width="{_fmt(bw)}" height="{_fmt(base - y)}" fill="{SERIES[j]}"/>'
            )
            if p.get("show_values", True):
                parts.append(_text(x0 + bw / 2, y - 5, _num(vs[i]), size=12))
        parts.append(_text(left + slot * i + slot / 2, base + 20, c, size=13))
    return _svg(w, h, "".join(parts))


def line_chart(p: dict[str, Any]) -> Rendered:
    """折线统计图；给 `series` 时是复式折线统计图（几条折线，带图例）。"""
    cats, ser = _series(p)
    slot = 54.0
    parts, w, h, left, base, y_of = _value_axis(p, [v for _, vs in ser for v in vs], slot, len(cats))
    if len(ser) > 1:
        w += 96
        parts.append(_legend([n for n, _ in ser], w, line=True))
    xs = [left + slot * i + slot / 2 for i in range(len(cats))]
    for i, c in enumerate(cats):
        parts.append(_text(xs[i], base + 20, c, size=13))
    for j, (_, vs) in enumerate(ser):
        pts = [(xs[i], y_of(v)) for i, v in enumerate(vs)]
        parts.append(
            f'<polyline points="{" ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in pts)}" fill="none" '
            f'stroke="{SERIES[j]}" stroke-width="2.2"/>'
        )
        for (x, y), v in zip(pts, vs, strict=True):
            parts.append(f'<circle cx="{_fmt(x)}" cy="{_fmt(y)}" r="3.5" fill="{SERIES[j]}"/>')
            if p.get("show_values", True):  # 第二组的数标在点的下方，两条线靠近时不重叠
                parts.append(_text(x, y + 18 if j % 2 else y - 8, _num(v), size=12))
    return _svg(w, h, "".join(parts))


PIE = ["#6aa0e8", "#f0a04b", "#7cc47c", "#e57373", "#b39ddb", "#ffd54f"]


def pie_chart(p: dict[str, Any]) -> Rendered:
    """扇形统计图：`parts: [{label, percent}]`，百分比合计 100；`hide` 里的部分只写名称、不标百分比（题目要求的那一块）。"""
    raw = _need(p, "parts")
    items = [(str(_need(x, "label")), Decimal(str(_need(x, "percent")))) for x in raw]
    if not 2 <= len(items) <= len(PIE) or any(v <= 0 for _, v in items) or sum(v for _, v in items) != 100:
        raise FigureError("扇形统计图的各部分百分比必须为正数、合计 100，最多 6 部分")
    hide = {str(x) for x in p.get("hide") or []}
    w, cx, r = 320.0, 160.0, 92.0
    top = 52.0 if p.get("title") else 20.0
    cy = top + r
    h = cy + r + 20
    parts = [_text(w / 2, 22, str(p["title"]), size=15)] if p.get("title") else []
    start = -90.0
    for i, (label, pct) in enumerate(items):
        sweep = float(pct) * 3.6
        a0, a1 = math.radians(start), math.radians(start + sweep)
        x0, y0 = cx + r * math.cos(a0), cy + r * math.sin(a0)
        x1, y1 = cx + r * math.cos(a1), cy + r * math.sin(a1)
        large = 1 if sweep > 180 else 0
        parts.append(
            f'<path d="M{_fmt(cx)} {_fmt(cy)} L{_fmt(x0)} {_fmt(y0)} A{_fmt(r)} {_fmt(r)} 0 {large} 1 '
            f'{_fmt(x1)} {_fmt(y1)} Z" fill="{PIE[i]}" stroke="#fff" stroke-width="1.5"/>'
        )
        mid = math.radians(start + sweep / 2)
        lx, ly = cx + r * 0.62 * math.cos(mid), cy + r * 0.62 * math.sin(mid)
        parts.append(_text(lx, ly, label, size=12))
        parts.append(_text(lx, ly + 15, "?" if label in hide else f"{_num(pct)}%", size=12))
        start += sweep
    return _svg(w, h, "".join(parts))


# ---- 几何图形 ----
def _label_at(x: float, y: float, s: Any, *, anchor: str = "middle") -> str:
    return _text(x, y, str(s), anchor=anchor) if s not in (None, "") else ""


def _right_angle(x: float, y: float, dx1: float, dy1: float, dx2: float, dy2: float, r: float = 9) -> str:
    pts = [(x + dx1 * r, y + dy1 * r), (x + (dx1 + dx2) * r, y + (dy1 + dy2) * r), (x + dx2 * r, y + dy2 * r)]
    d = "M" + " L".join(f"{_fmt(a)} {_fmt(b)}" for a, b in pts)
    return f'<path d="{d}" fill="none" stroke="{INK}" stroke-width="1"/>'


# 每种平面图形会画出的标注（`labels` 的键）；文字描述只用这些，保证"描述里有的，图上就有"
SHAPE_LABELS: dict[str, tuple[str, ...]] = {
    "rectangle": ("length", "width", "side"),
    "square": ("side", "length", "width"),
    "triangle": ("base", "left", "right", "height"),
    "parallelogram": ("base", "side", "height"),
    "trapezoid": ("top", "bottom", "height"),
    "circle": ("radius", "diameter"),
}


def shape(p: dict[str, Any]) -> Rendered:
    kind = str(_need(p, "shape"))
    L = p.get("labels") or {}
    if not isinstance(L, dict):
        raise FigureError('labels 要写成 {边的名称: 标注}，如 {"length": "8 cm"}')
    for v in L.values():  # 标注是长度这类数值（"8 cm""?""a"），不是说明文字（"钟面示意图"）
        t = str(v) if v not in (None, "") else ""
        if len(t) > 2 and not any(ch.isdigit() or ch == "?" or (ch.isascii() and ch.isalpha()) for ch in t):
            raise FigureError(f"图上的标注只能写数值（如“8 cm”“?”），不能写说明文字：{v}")
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
        raise FigureError(f"不支持的平面图形：{kind}（shape 只能是 {'、'.join(SHAPE_LABELS)}）")
    body = f'<g fill="{FILL}" stroke="{INK}" stroke-width="1.6" stroke-linejoin="round">{poly}</g>{extra}'
    return _svg(w, h, body)


# ---- 钟面 ----
def clock(p: dict[str, Any]) -> Rendered:
    """钟面：`{"hour": 9, "minute": 30}`，12 个数字、60 个刻度、时针与分针（时针按分钟偏转）。"""
    hour, minute = int(_need(p, "hour")), int(p.get("minute", 0))
    if not (0 <= hour <= 23 and 0 <= minute < 60):
        raise FigureError("钟面的时刻不合法：hour 0～23，minute 0～59")
    w = h = 220.0
    cx = cy = 110.0
    r = 96.0
    parts = [
        f'<circle cx="{_fmt(cx)}" cy="{_fmt(cy)}" r="{_fmt(r)}" fill="#fff" stroke="{INK}" stroke-width="2.4"/>'
    ]
    for i in range(60):
        a = math.radians(i * 6 - 90)
        r0 = r - (10 if i % 5 == 0 else 5)
        parts.append(
            _line(
                cx + r0 * math.cos(a),
                cy + r0 * math.sin(a),
                cx + r * math.cos(a),
                cy + r * math.sin(a),
                w=1.8 if i % 5 == 0 else 0.8,
            )
        )
    for n in range(1, 13):
        a = math.radians(n * 30 - 90)
        parts.append(_text(cx + (r - 24) * math.cos(a), cy + (r - 24) * math.sin(a) + 5, str(n), size=15))
    ha = math.radians((hour % 12) * 30 + minute * 0.5 - 90)
    ma = math.radians(minute * 6 - 90)
    parts.append(_line(cx, cy, cx + r * 0.42 * math.cos(ha), cy + r * 0.42 * math.sin(ha), w=4.5))
    parts.append(_line(cx, cy, cx + r * 0.62 * math.cos(ma), cy + r * 0.62 * math.sin(ma), w=2.6))
    parts.append(f'<circle cx="{_fmt(cx)}" cy="{_fmt(cy)}" r="4" fill="{INK}"/>')
    return _svg(w, h, "".join(parts))


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

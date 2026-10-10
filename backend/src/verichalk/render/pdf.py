"""PDF 导出：自有 Typst 模板控制版面，pandoc 只转换文本片段（D8）。

流程：`PaperView` → 带占位符的 Typst 源码 → 一次 pandoc 转换全部片段 → 代入 → Typst 编译。
图形先渲染成 SVG 写进临时目录，由 Typst 直接嵌入。
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import typst

from ..core.errors import ExportError
from ..figures import FigureError, render_figure
from .convert import Pool, convert_fragments
from .fonts import MATH_FONT, TEXT_FONTS
from .view import SPACE_CM, ItemView, PaperView, SectionView, fmt_score, option_columns

_PH = re.compile(r"ZZF(\d+)ZZ")
_INLINE_MATH = re.compile(r"(?<![\\$])\$(?![\s$])((?:\\.|[^$\\])+?)(?<!\s)\$")
_FIG_PH = re.compile(r"ZZFIG(\d+)ZZ")


def _top_level_comma(expr: str) -> bool:
    depth = 0
    for i, ch in enumerate(expr):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0 and (i == 0 or expr[i - 1] != "\\"):
            return True
    return False


def display_fractions(typ: str) -> str:
    """行内公式里的分数默认是缩小的上标式，试卷里看不清；含分数的行内公式改用显示样式（`display(...)`）。"""

    def sub(m: re.Match[str]) -> str:
        inner = m.group(1)
        has_frac = "frac(" in inner or re.search(r"(?<!\\)/", inner)
        if not has_frac or _top_level_comma(inner) or inner.lstrip().startswith("display("):
            return m.group(0)
        return f"$display({inner})$"

    return _INLINE_MATH.sub(sub, typ)


def _font_list(names: tuple[str, ...]) -> str:
    return "(" + ", ".join(f'"{n}"' for n in names) + ")"


_PREAMBLE = f"""\
#set page(paper: "a4", margin: (x: 2.1cm, top: 2.2cm, bottom: 2.2cm),
  footer: context [#align(center)[#text(size: 9pt, fill: luma(120))[第 #counter(page).display() 页 · 共 #counter(page).final().first() 页]]])
#set text(font: {_font_list(TEXT_FONTS)}, size: 10.5pt, lang: "zh", region: "cn")
#set par(leading: 0.78em, justify: false)
#show math.equation: set text(font: ("{MATH_FONT}", {", ".join(f'"{n}"' for n in TEXT_FONTS[1:])}))
#show math.equation.where(block: true): set block(spacing: 0.9em)
#set table(stroke: 0.5pt + luma(90), inset: 5pt)
#let horizontalrule = line(length: 100%, stroke: 0.5pt + luma(150))
#let blockquote(body) = block(inset: (left: 1em), stroke: (left: 1.5pt + luma(180)), body)
#let blank(w: 3.2em) = box(width: w, height: 1em, stroke: (bottom: 0.6pt), outset: (bottom: 1.5pt))
#let tag(txt, color: luma(110)) = text(size: 9pt, fill: color)[#txt]
#let section(title, note) = block(sticky: true, above: 1.4em, below: 0.7em)[
  #text(size: 11.5pt, weight: "bold")[#title]#if note != "" [#h(0.8em)#tag[（#note）]]
]
#let qitem(n, body, score: none, review: false, keep: true, opts: (), cols: 1, rg: 0.6em, space: 0pt, ans: none, sol: none) = block(
  breakable: not keep, width: 100%, above: 1.7em, below: 1.7em,
)[
  #grid(columns: (2.1em, 1fr),
    [#strong[#n.]],
    [#if score != none [#tag[（#score）]#h(0.3em)]#body#if review [#h(0.5em)#tag(color: rgb("#c0392b"))[［需复核］]]])
  #if opts.len() > 0 [
    #v(0.35em)
    #pad(left: 2.1em, grid(columns: (1fr,) * cols, row-gutter: rg, column-gutter: 0.8em,
      ..opts.enumerate().map(((i, o)) => [#("ABCDEFGH".at(i)). #o])))
  ]
  #if ans != none [
    #v(0.5em)
    #pad(left: 2.1em, block(width: 100%, fill: luma(245), inset: 7pt, radius: 3pt)[
      #text(weight: "bold")[答案：]#ans
      #if sol != none [#v(0.3em)#text(weight: "bold")[解析：]#sol]
    ])
  ]
  #v(space)
]
#let answer-entry(n, ans, sol) = block(breakable: false, above: 1.2em, below: 1.2em)[
  #grid(columns: (2.1em, 1fr), [#strong[#n.]], [#ans#if sol != none [#linebreak()#text(fill: luma(70))[解析：]#sol]])
]
"""


def _tag(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _blank_field(label: str) -> str:
    return f"{label}：#blank(w: 4em)"


def _build(view: PaperView, pool: Pool) -> str:
    out = [_PREAMBLE]
    head = []
    if view.school:
        head.append(f"#align(center)[#text(size: 11pt)[{pool.add(view.school)}]]")
    head.append(f'#align(center)[#text(size: 18pt, weight: "bold")[{pool.add(view.title)}]]')
    info: list[str] = []
    teacher = view.version.value == "teacher"
    if view.class_name:
        info.append(f"班级：{pool.add(view.class_name)}")
    elif not teacher and view.name_line:
        info.append(_blank_field("班级"))
    if view.name_line:
        info.append(_blank_field("姓名"))
    if view.date:
        info.append(f"日期：{pool.add(view.date)}")
    if view.duration:
        info.append(f"时间：{view.duration}")
    if view.total_score is not None:
        info.append(f"满分：{fmt_score(view.total_score)} 分")
    if teacher:
        info.append("教师用卷")
    elif view.name_line:
        info.append(_blank_field("得分"))
    head.append("#v(0.2em)#align(center)[#text(size: 10pt)[" + "#h(1.6em)".join(info) + "]]")
    head.append("#v(0.2em)#line(length: 100%, stroke: 0.7pt)")
    out.append("\n".join(head))
    for sec in view.sections:
        out.append(_section(sec, pool))
    if view.appendix:
        out.append(
            '#pagebreak()\n#align(center)[#text(size: 14pt, weight: "bold")[参考答案与解析]]\n#v(0.4em)'
        )
        for e in view.appendix:
            sol = f"[{pool.add(e.solution)}]" if e.solution else "none"
            out.append(f"#answer-entry({e.number}, [{pool.add(e.answer)}], {sol})")
    return "\n".join(out)


def _section(sec: SectionView, pool: Pool) -> str:
    parts = []
    if sec.heading:
        parts.append(f'#section([{pool.add(sec.heading)}], "{_tag(sec.note)}")')
    for it in sec.items:
        parts.append(_item(it, pool))
    return "\n".join(parts)


def _item(it: ItemView, pool: Pool) -> str:
    args = [str(it.number), f"[{pool.add(it.stem, it.figures)}]"]
    if it.score is not None:
        args.append(f'score: "{fmt_score(it.score)}分"')
    if it.review:
        args.append("review: true")
    if it.options:
        opts = ", ".join(f"[{pool.add(o)}]" for o in it.options)
        args.append(f"opts: ({opts},)")
        args.append(f"cols: {option_columns(it.options)}")
        if any("frac" in o for o in it.options):
            args.append("rg: 1.5em")  # 含分数的选项更高，行距放大
    if it.space != "none":
        args.append(f"space: {SPACE_CM[it.space]}cm")
    size = len(it.stem) + sum(len(o) for o in it.options) + len(it.solution or "") + len(it.answer or "")
    args.append(f"keep: {'true' if size < 700 else 'false'}")
    if it.answer is not None:
        args.append(f"ans: [{pool.add(it.answer)}]")
        if it.solution:
            args.append(f"sol: [{pool.add(it.solution)}]")
    return f"#qitem({', '.join(args)})"


def render_pdf(view: PaperView, font_dirs: tuple[str, ...] = ()) -> tuple[bytes, list[str]]:
    """返回 (PDF 字节, 警告)。图形渲染失败不阻断导出，改为题内的占位框并给出警告。"""
    pool = Pool()
    source = _build(view, pool)
    converted = convert_fragments(pool.texts, "typst")
    warnings: list[str] = []
    with tempfile.TemporaryDirectory(prefix="vc_pdf_") as td:
        root = Path(td)

        def fig_call(m: re.Match[str]) -> str:
            idx = int(m.group(1))
            spec = pool.fig_ids[idx]
            if spec is None:
                warnings.append("题干引用了不存在的图")
                return "#box(stroke: 0.5pt + luma(150), inset: 6pt)[（图缺失）]"
            try:
                r = render_figure(spec)
            except FigureError as e:
                warnings.append(f"图 {spec.id} 无法绘制：{e}")
                return f"#box(stroke: 0.5pt + luma(150), inset: 6pt)[（{spec.alt or '图'}）]"
            name = f"fig{idx}.svg"
            (root / name).write_text(r.svg, encoding="utf-8")
            return f'#box(image("{name}", width: {r.width * 0.75:.1f}pt))'

        def sub(m: re.Match[str]) -> str:
            text = display_fractions(converted[int(m.group(1))]).replace("ZZBLANKZZ", "#blank()")
            return _FIG_PH.sub(fig_call, text)

        # 片段里的 `#blank()` 等函数调用在内容块里继续有效；公式内的占位符不会出现（prepare 跳过公式）
        full = _PH.sub(sub, source)
        (root / "main.typ").write_text(full, encoding="utf-8")
        try:
            main = str(root / "main.typ")
            if font_dirs:
                data = typst.compile(main, root=str(root), font_paths=list(font_dirs))
            else:
                data = typst.compile(main, root=str(root))
        except Exception as e:
            raise ExportError(f"Typst 编译失败：{str(e)[:400]}") from e
    return bytes(data), warnings

"""文本类导出：Markdown（原样保留 Pandoc Markdown + TeX）与 LaTeX 源码（只生成源码，不编译，D8）。

Markdown 单文件自包含：图形以 SVG data URI 内嵌。LaTeX 有图形时打成 zip（`paper.tex` + `fig*.png`），否则就是一个 `.tex`。
"""

from __future__ import annotations

import base64
import io
import re
import zipfile

from ..domain.paper import FigureSpec
from ..figures import FigureError, render_figure
from .convert import Pool, convert_fragments
from .raster import svg_to_png
from .view import LETTERS, SPACE_CM, ItemView, PaperView, fmt_score, option_columns

_FIG = re.compile(r"!\[([^\]]*)\]\(fig:([A-Za-z0-9_\-]+)\)")
_FIG_PH = re.compile(r"ZZFIG(\d+)ZZ")
_PH = re.compile(r"ZZF(\d+)ZZ")


# ---- Markdown ----
def _md_info(view: PaperView) -> str:
    bits = []
    if view.school:
        bits.append(f"学校：{view.school}")
    if view.class_name:
        bits.append(f"班级：{view.class_name}")
    elif view.name_line:
        bits.append("班级：______")
    if view.name_line:
        bits.append("姓名：______")
    if view.date:
        bits.append(f"日期：{view.date}")
    if view.duration:
        bits.append(f"时间：{view.duration}")
    if view.total_score is not None:
        bits.append(f"满分：{fmt_score(view.total_score)} 分")
    if view.version.value == "teacher":
        bits.append("教师用卷")
    elif view.name_line:
        bits.append("得分：______")
    return "　".join(bits)


def render_markdown(view: PaperView) -> tuple[bytes, list[str]]:
    warnings: list[str] = []

    def inline_figs(text: str, specs: list[FigureSpec]) -> str:
        by_id = {f.id: f for f in specs}

        def sub(m: re.Match[str]) -> str:
            spec = by_id.get(m.group(2))
            if spec is None:
                warnings.append("题干引用了不存在的图")
                return "（图缺失）"
            try:
                svg = render_figure(spec).svg
            except FigureError as e:
                warnings.append(f"图 {spec.id} 无法绘制：{e}")
                return f"（{spec.alt or '图'}）"
            return f"![{spec.alt}](data:image/svg+xml;base64,{base64.b64encode(svg.encode()).decode()})"

        return _FIG.sub(sub, text)

    lines = [f"# {view.title}", "", _md_info(view), ""]
    for sec in view.sections:
        if sec.heading:
            lines += [f"## {sec.heading}" + (f"（{sec.note}）" if sec.note else ""), ""]
        for it in sec.items:
            head = f"**{it.number}.**" + (f"（{fmt_score(it.score)}分）" if it.score is not None else "")
            lines += [f"{head} {inline_figs(it.stem, it.figures)}" + ("［需复核］" if it.review else ""), ""]
            lines += [f"- {LETTERS[i]}. {o}" for i, o in enumerate(it.options)]
            if it.options:
                lines.append("")
            if it.answer is not None:
                lines.append(f"> **答案：** {it.answer}")
                if it.solution:
                    lines += [">", f"> **解析：** {it.solution}"]
                lines.append("")
    if view.appendix:
        lines += ["---", "", "## 参考答案与解析", ""]
        for e in view.appendix:
            lines.append(f"**{e.number}.** {e.answer}" + (f"  \n解析：{e.solution}" if e.solution else ""))
            lines.append("")
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8"), warnings


# ---- LaTeX ----
_TEX_PREAMBLE = r"""\documentclass[11pt,a4paper]{ctexart}
\usepackage[margin=2.2cm]{geometry}
\usepackage{amsmath,amssymb,graphicx,longtable,booktabs,array}
\providecommand{\tightlist}{\setlength{\itemsep}{0pt}\setlength{\parskip}{0pt}}
\newcounter{none} % pandoc 的表格代码用 \LTcaptype{none}，需要这个计数器
\pagestyle{plain}
\setlength{\parindent}{0pt}
\setlength{\parskip}{0.4em}
\newcommand{\blank}{\underline{\hspace{3em}}}
"""


def _tex_item(it: ItemView, pool: Pool) -> str:
    head = f"\\textbf{{{it.number}.}}" + (f"（{fmt_score(it.score)}分）" if it.score is not None else "")
    out = [
        f"\\medskip\\noindent {head}\\ {pool.add(it.stem, it.figures)}" + ("［需复核］" if it.review else "")
    ]
    if it.options:
        cols = option_columns(it.options)
        cells = [f"{LETTERS[i]}.\\ {pool.add(o)}" for i, o in enumerate(it.options)]
        if cols == 1:
            out.append("\\par\\noindent " + "\\par\\noindent ".join(cells))
        else:
            w = f"{0.96 / cols:.2f}\\textwidth"
            spec = "@{}" + f"p{{{w}}}" * cols + "@{}"
            rows = [cells[i : i + cols] for i in range(0, len(cells), cols)]
            body = " \\\\\n".join(" & ".join(r + [""] * (cols - len(r))) for r in rows)
            out.append(f"\\par\\noindent\\begin{{tabular}}{{{spec}}}\n{body}\n\\end{{tabular}}")
    if it.answer is not None:
        ans = f"\\par\\noindent\\textbf{{答案：}}{pool.add(it.answer)}"
        if it.solution:
            ans += f"\\par\\noindent\\textbf{{解析：}}{pool.add(it.solution)}"
        out.append(ans)
    if it.space != "none":
        out.append(f"\\vspace{{{SPACE_CM[it.space]}cm}}")
    return "\n".join(out)


def render_latex(view: PaperView) -> tuple[bytes, str, list[str]]:
    """返回 (内容, 扩展名 `tex` 或 `zip`, 警告)。"""
    pool = Pool()
    body = [_TEX_PREAMBLE, "\\begin{document}"]
    body.append(f"\\begin{{center}}{{\\Large\\bfseries {pool.add(view.title)}}}\\end{{center}}")
    body.append(f"\\begin{{center}}{pool.add(_md_info(view))}\\end{{center}}")
    for sec in view.sections:
        if sec.heading:
            note = f"（{sec.note}）" if sec.note else ""
            body.append(f"\\bigskip\\noindent\\textbf{{{pool.add(sec.heading)}}}{note}")
        body += [_tex_item(it, pool) for it in sec.items]
    if view.appendix:
        body.append("\\clearpage\\begin{center}{\\large\\bfseries 参考答案与解析}\\end{center}")
        for e in view.appendix:
            sol = f"\\par\\noindent 解析：{pool.add(e.solution)}" if e.solution else ""
            body.append(f"\\noindent\\textbf{{{e.number}.}}\\ {pool.add(e.answer)}{sol}")
    body.append("\\end{document}\n")
    source = "\n".join(body)
    converted = convert_fragments(pool.texts, "latex")
    warnings: list[str] = []
    pngs: dict[str, bytes] = {}

    def fig_tex(m: re.Match[str]) -> str:
        idx = int(m.group(1))
        spec = pool.fig_ids[idx]
        if spec is None:
            warnings.append("题干引用了不存在的图")
            return "（图缺失）"
        try:
            r = render_figure(spec)
            name = f"fig{idx}.png"
            pngs[name] = svg_to_png(r.svg)
        except Exception as e:
            warnings.append(f"图 {spec.id} 无法绘制：{e}")
            return f"（{spec.alt or '图'}）"
        return f"\\includegraphics[width={min(r.width / 96 * 2.54, 12):.1f}cm]{{{name}}}"

    def sub(m: re.Match[str]) -> str:
        return _FIG_PH.sub(fig_tex, converted[int(m.group(1))].replace("ZZBLANKZZ", "\\blank{}"))

    tex = _PH.sub(sub, source)
    if not pngs:
        return tex.encode("utf-8"), "tex", warnings
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("paper.tex", tex)
        for name, data in pngs.items():
            z.writestr(name, data)
    return buf.getvalue(), "zip", warnings

"""Word 导出：整卷拼成一份 Pandoc Markdown，由 pandoc 转成 docx（公式是原生 OMML 对象，可在 Word 里继续编辑）。

样式来自运行时修补的参考文档（`reference.docx`）：宋体 + Times New Roman、紧凑段距、标题居中。
选项用无边框表格对齐；填空线用下划线字符；图形先栅格化为 PNG。
"""

from __future__ import annotations

import io
import re
import subprocess
import tempfile
import zipfile
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

import pypandoc

from ..core.errors import ExportError
from ..domain.paper import FigureSpec
from ..figures import FigureError, render_figure
from .convert import BLANK, MD_FORMAT, convert_document, prepare
from .raster import svg_to_png
from .view import SPACE_CM, ItemView, PaperView, SectionView, fmt_score, option_columns

DOCX_FORMAT = MD_FORMAT.replace("-link_attributes", "+link_attributes").replace(
    "-yaml_metadata_block", "+yaml_metadata_block"
)
AddFn = Callable[[str, list[FigureSpec]], str]
_FIG_PH = re.compile(r"ZZFIG(\d+)ZZ")
_BLANK_TXT = "\\_" * 8


# 数学区默认用 Cambria Math（数字直立）、行内分数不缩小
_MATH_PR = (
    '<m:mathPr><m:mathFont m:val="Cambria Math" /><m:smallFrac m:val="0" /><m:dispDef />'
    '<m:defJc m:val="centerGroup" /></m:mathPr>'
)


def _patch_style_rpr(styles: str, style_id: str, size: int, bold: bool) -> str:
    """把某个样式的字号改成 size（半磅），并按需加粗（加在 rFonts 之后，符合 schema 顺序）。"""
    m = re.search(rf'<w:style [^>]*w:styleId="{style_id}".*?</w:style>', styles, re.S)
    if not m:
        return styles
    block = m.group(0)
    block = re.sub(r'<w:sz w:val="\d+" />', f'<w:sz w:val="{size}" />', block, count=1)
    if bold and "<w:b />" not in block:
        block = re.sub(r"(<w:rFonts [^>]*/>)", lambda m: m.group(1) + "<w:b />", block, count=1)
    return styles.replace(m.group(0), block)


_MATH_RUN = re.compile(r"<m:r><m:t(?: [^>]*)?>([^<]*)</m:t></m:r>")


def _upright_numbers(xml: str) -> str:
    """pandoc 生成的公式里数字没有"正体"标记，Word 会按斜体排；纯数字与符号的 run 补上 `m:sty=p`。"""

    def sub(m: re.Match[str]) -> str:
        text = m.group(1)
        if re.fullmatch(r"[0-9.,:%\s]+", text):
            return m.group(0).replace("<m:r>", '<m:r><m:rPr><m:sty m:val="p" /></m:rPr>', 1)
        return m.group(0)

    return _MATH_RUN.sub(sub, xml)


def _post_process(data: bytes) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            blob = zin.read(info.filename)
            if info.filename == "word/document.xml":
                blob = _upright_numbers(blob.decode("utf-8")).encode("utf-8")
            zout.writestr(info, blob)
    return out.getvalue()


@lru_cache(maxsize=1)
def _reference_docx() -> bytes:
    """pandoc 默认参考文档 + 试卷样式修补（字体、字号、段距、标题颜色）。"""
    exe = pypandoc.get_pandoc_path()
    raw = subprocess.run(
        [exe, "--print-default-data-file", "reference.docx"], capture_output=True, check=True
    ).stdout
    zin = zipfile.ZipFile(io.BytesIO(raw))
    styles = zin.read("word/styles.xml").decode("utf-8")
    fonts = '<w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:eastAsia="SimSun" w:cs="Times New Roman" />'
    styles = re.sub(r"<w:rFonts [^>]*?(?:asciiTheme|eastAsiaTheme)[^>]*?/>", fonts, styles, flags=re.S)
    styles = styles.replace(
        '<w:sz w:val="24" />\n        <w:szCs w:val="24" />',
        '<w:sz w:val="21" />\n        <w:szCs w:val="21" />',
        1,
    )
    styles = styles.replace(
        '<w:spacing w:before="180" w:after="180" />',
        '<w:spacing w:before="80" w:after="160" w:line="320" w:lineRule="auto" />',
    )
    styles = re.sub(r'<w:color w:val="[0-9A-Fa-f]{6}"[^>]*/>', "", styles)
    for style_id, size, bold in (("Title", 36, True), ("Heading2", 24, True), ("Subtitle", 21, False)):
        styles = _patch_style_rpr(styles, style_id, size, bold)
    styles = re.sub(r'<w:tblStylePr w:type="firstRow">.*?</w:tblStylePr>', "", styles, flags=re.S)
    settings = zin.read("word/settings.xml").decode("utf-8")
    settings = settings.replace("<w:themeFontLang", _MATH_PR + "<w:themeFontLang", 1)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "word/styles.xml":
                data = styles.encode("utf-8")
            elif info.filename == "word/settings.xml":
                data = settings.encode("utf-8")
            zout.writestr(info, data)
    return out.getvalue()


def _options_table(opts: list[str], cols: int) -> str:
    """选项排版：一列时用换行；多列时用无边框表格（首行当表头，表头的边框样式在参考文档里去掉）。
    标签写成 `A\\.` 避免被识别成有序列表。"""
    cells = [f"{'ABCDEFGH'[i]}\\. " + o.replace("\n", " ").replace("|", "\\|") for i, o in enumerate(opts)]
    if cols == 1:
        return "\n".join(cells)
    rows = [cells[i : i + cols] for i in range(0, len(cells), cols)]
    lines = []
    for k, r in enumerate(rows):
        r = r + [""] * (cols - len(r))
        lines.append("| " + " | ".join(r) + " |")
        if k == 0:
            # 分隔线总长超过 pandoc 的列宽（72），它才按破折号个数分配列宽 → 各列等宽，行间对齐
            lines.append("|" + "|".join(":" + "-" * (80 // cols) for _ in range(cols)) + "|")
    return "\n".join(lines)


def _item_md(it: ItemView, add: AddFn) -> str:
    head = f"**{it.number}.**"
    if it.score is not None:
        head += f"（{fmt_score(it.score)}分）"
    review = "［需复核］" if it.review else ""
    parts = [f"{head} {add(it.stem, it.figures)}{review}"]
    if it.options:
        cols = option_columns(it.options)
        parts.append(_options_table([add(o, []) for o in it.options], cols))
    if it.answer is not None:
        ans = f"> **答案：** {add(it.answer, [])}"
        if it.solution:
            ans += f"\n>\n> **解析：** {add(it.solution, [])}"
        parts.append(ans)
    if it.space != "none":
        parts.append("\\\n" * max(1, round(SPACE_CM[it.space] / 0.65)))  # 作答空白
    return "\n\n".join(parts)


def _section_md(sec: SectionView, add: AddFn) -> str:
    parts = []
    if sec.heading:
        parts.append(f"## {sec.heading}" + (f"（{sec.note}）" if sec.note else ""))
    parts += [_item_md(it, add) for it in sec.items]
    return "\n\n".join(parts)


def render_docx(view: PaperView) -> tuple[bytes, list[str]]:
    warnings: list[str] = []
    with tempfile.TemporaryDirectory(prefix="vc_docx_") as td:
        root = Path(td)
        figs: list[FigureSpec | None] = []
        cur: dict[str, FigureSpec] = {}

        def on_figure(fid: str) -> str:
            figs.append(cur.get(fid))
            return f"ZZFIG{len(figs) - 1}ZZ"

        def add(text: str, figures: list[FigureSpec]) -> str:
            cur.clear()
            cur.update({f.id: f for f in figures})
            return prepare(text, on_figure)

        body = "\n\n".join(_section_md(s, add) for s in view.sections)
        if view.appendix:
            app = ['```{=openxml}\n<w:p><w:r><w:br w:type="page"/></w:r></w:p>\n```', "## 参考答案与解析"]
            for e in view.appendix:
                line = f"**{e.number}.** {add(e.answer, [])}"
                if e.solution:
                    line += f"\\\n解析：{add(e.solution, [])}"
                app.append(line)
            body += "\n\n" + "\n\n".join(app)

        def fig_md(m: re.Match[str]) -> str:
            idx = int(m.group(1))
            spec = figs[idx]
            if spec is None:
                warnings.append("题干引用了不存在的图")
                return "（图缺失）"
            try:
                r = render_figure(spec)
                png = svg_to_png(r.svg)
            except (FigureError, ExportError) as e:
                warnings.append(f"图 {spec.id} 无法绘制：{e}")
                return f"（{spec.alt or '图'}）"
            path = root / f"fig{idx}.png"
            path.write_bytes(png)
            return f"![{spec.alt}]({path.as_posix()}){{width={r.width / 96 * 2.54:.1f}cm}}"

        body = _FIG_PH.sub(fig_md, body.replace(BLANK, _BLANK_TXT))
        info = []
        if view.school:
            info.append(f"学校：{view.school}")
        teacher = view.version.value == "teacher"
        info.append(
            f"班级：{view.class_name}"
            if view.class_name
            else ("" if teacher or not view.name_line else "班级：\\_\\_\\_\\_\\_\\_")
        )
        if view.name_line:
            info.append("姓名：\\_\\_\\_\\_\\_\\_")
        if view.date:
            info.append(f"日期：{view.date}")
        if view.duration:
            info.append(f"时间：{view.duration}")
        if view.total_score is not None:
            info.append(f"满分：{fmt_score(view.total_score)} 分")
        info.append("教师用卷" if teacher else ("得分：\\_\\_\\_\\_\\_\\_" if view.name_line else ""))
        ref = root / "reference.docx"
        ref.write_bytes(_reference_docx())
        out = root / "paper.docx"
        # 标题与信息行走元数据（Title / Subtitle 样式居中）；两者按 Markdown 解析
        doc = f"---\ntitle: {_yaml(view.title)}\nsubtitle: {_yaml('　'.join(x for x in info if x))}\n---\n\n{body}\n"
        convert_document(
            doc,
            "docx",
            out,
            ["--reference-doc", str(ref), "--resource-path", str(root)],
            fmt=DOCX_FORMAT,
        )
        return _post_process(out.read_bytes()), warnings


def _yaml(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'

"""导出：`Paper + ExportOptions → 文件`（PDF / Word / Markdown / LaTeX 源码）。

流程：`view.build_view`（题号、分值、学生版 / 教师版，纯函数）→ 各格式写出器。入口 `export_paper` 是同步函数（pandoc 与
Typst 都是阻塞调用），异步调用方用 `asyncio.to_thread` 包装。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..core.errors import ExportError
from ..domain.export import ExportFormat, ExportOptions, ExportVersion
from ..domain.paper import Paper
from .docx import render_docx
from .fonts import has_cjk_font, missing_glyphs
from .pdf import render_pdf
from .textual import render_latex, render_markdown
from .view import build_view

MEDIA_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "md": "text/markdown; charset=utf-8",
    "tex": "application/x-tex; charset=utf-8",
    "zip": "application/zip",
}


@dataclass
class ExportResult:
    data: bytes
    filename: str
    media_type: str
    warnings: list[str] = field(default_factory=list)


def _filename(title: str, opts: ExportOptions, ext: str) -> str:
    base = re.sub(r'[\\/:*?"<>|\s]+', "_", title.strip()) or "试卷"
    suffix = "教师版" if opts.version == ExportVersion.teacher else "学生版"
    return f"{base}_{suffix}.{ext}"


def export_paper(paper: Paper, opts: ExportOptions, *, font_dirs: tuple[str, ...] = ()) -> ExportResult:
    """导出一份试卷。空试卷抛 `ExportError`（带教师能懂的说明）；图形无法绘制与缺字只给警告，不阻断。"""
    view = build_view(paper, opts)
    if not view.items:
        raise ExportError("试卷里没有题目", user_message="这份试卷还没有题目，先出几道题再导出吧。")
    warnings = list(view.warnings)
    fmt = opts.format
    if fmt in (ExportFormat.pdf, ExportFormat.docx):
        if fmt == ExportFormat.pdf and not has_cjk_font(font_dirs):
            warnings.append("服务器上没有找到中文字体，PDF 里的汉字可能显示为方框")
        elif (miss := missing_glyphs(view.text(), font_dirs)) and fmt == ExportFormat.pdf:
            warnings.append("这些字符在字体里没有字形，PDF 里可能显示为方框：" + "".join(miss[:12]))
    if fmt == ExportFormat.pdf:
        data, w = render_pdf(view, font_dirs)
        ext = "pdf"
    elif fmt == ExportFormat.docx:
        data, w = render_docx(view)
        ext = "docx"
    elif fmt == ExportFormat.md:
        data, w = render_markdown(view)
        ext = "md"
    else:
        data, ext, w = render_latex(view)
    return ExportResult(data, _filename(view.title, opts, ext), MEDIA_TYPES[ext], [*warnings, *w])


__all__ = ["ExportResult", "build_view", "export_paper"]

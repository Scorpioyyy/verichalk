"""内容转换：Pandoc Markdown + TeX → 目标格式的片段（D8）。

两个关键点：
1. **一次调用转换全卷**：把所有片段用分隔标记拼成一份文档，转换一次再拆开，每次导出只启动一次 pandoc。
2. **占位符**：填空横线与插图引用在转换前换成不会被 pandoc 改写的占位词，转换后换成目标格式的结构。
   公式内部不做替换（公式里的下划线是下标）。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pypandoc

from ..core.errors import ExportError
from ..domain.paper import FigureSpec

# 关掉会误伤试卷文本的扩展：智能标点、`~`/`^` 上下标、`@` 引用、裸 TeX / HTML、YAML 元数据块
MD_FORMAT = (
    "markdown+hard_line_breaks+pipe_tables"
    "-smart-subscript-superscript-strikeout-citations-raw_html-raw_tex-yaml_metadata_block"
    "-auto_identifiers-implicit_figures-pandoc_title_block-fenced_divs-bracketed_spans-inline_notes"
    "-link_attributes-definition_lists-example_lists"
)
SEP = "ZZSEPZZ"
BLANK = "ZZBLANKZZ"
_FIG = re.compile(r"!\[([^\]]*)\]\(fig:([A-Za-z0-9_\-]+)\)")
_FIG_PH = re.compile(r"ZZFIG(\d+)ZZ")
_MATH = re.compile(r"(?<!\\)\$\$.+?(?<!\\)\$\$|(?<!\\)\$(?!\$).+?(?<!\\)\$", re.S)
_BLANK_RUN = re.compile(r"(?:\\_|_|＿){3,}")


def prepare(text: str, on_figure: Callable[[str], str]) -> str:
    """把一个片段里的填空线与图引用换成占位符（公式内不动）；`on_figure(图形 id)` 返回该图的占位符。"""

    def fig(m: re.Match[str]) -> str:
        return on_figure(m.group(2))

    out: list[str] = []
    last = 0
    for m in _MATH.finditer(text):
        out.append(_BLANK_RUN.sub(BLANK, _FIG.sub(fig, text[last : m.start()])))
        out.append(m.group(0))
        last = m.end()
    out.append(_BLANK_RUN.sub(BLANK, _FIG.sub(fig, text[last:])))
    return "".join(out)


class Pool:
    """片段池：文本先登记、拿到占位符，统一转换后代入。图形占位符按登记顺序编号。"""

    def __init__(self) -> None:
        self.texts: list[str] = []
        self.fig_ids: list[FigureSpec | None] = []
        self._cur: dict[str, FigureSpec] = {}

    def add(self, text: str, figures: list[FigureSpec] | None = None) -> str:
        self._cur = {f.id: f for f in (figures or [])}
        self.texts.append(prepare(text, self._on_figure))
        return f"ZZF{len(self.texts) - 1}ZZ"

    def _on_figure(self, fig_id: str) -> str:
        self.fig_ids.append(self._cur.get(fig_id))
        return f"ZZFIG{len(self.fig_ids) - 1}ZZ"


def convert_fragments(fragments: list[str], to: str, extra_args: list[str] | None = None) -> list[str]:
    """把若干 Markdown 片段转成目标格式（`typst` / `latex` 等文本格式），返回与输入等长的片段列表。"""
    if not fragments:
        return []
    doc = f"\n\n{SEP}\n\n".join(f if f.strip() else "ZZEMPTYZZ" for f in fragments)
    try:
        out = pypandoc.convert_text(doc, to, format=MD_FORMAT, extra_args=extra_args or [])
    except Exception as e:  # pypandoc 抛 RuntimeError / OSError
        raise ExportError(f"pandoc 转换失败：{str(e)[:300]}") from e
    out = out.replace("\r\n", "\n").replace("\r", "\n")  # Windows 上 pandoc 输出 CRLF
    parts = re.split(rf"\n*{SEP}\n*", out.strip("\n"))
    if len(parts) != len(fragments):
        raise ExportError(f"pandoc 片段拆分数量不符：{len(parts)} != {len(fragments)}")
    return ["" if p.strip() == "ZZEMPTYZZ" else p.strip("\n") for p in parts]


def convert_document(
    source: str, to: str, outfile: Path, extra_args: list[str] | None = None, *, fmt: str = MD_FORMAT
) -> None:
    """整份文档转成二进制格式（docx）。"""
    try:
        pypandoc.convert_text(source, to, format=fmt, outputfile=str(outfile), extra_args=extra_args or [])
    except Exception as e:
        raise ExportError(f"pandoc 转换失败：{str(e)[:300]}") from e


def pandoc_version() -> str:
    try:
        v: Any = pypandoc.get_pandoc_version()
        return str(v)
    except Exception:
        return ""

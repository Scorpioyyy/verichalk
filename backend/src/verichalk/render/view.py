"""试卷的"排法"：`Paper + ExportOptions → PaperView`，纯函数、与具体格式无关。

题号连续、分区编号、分值与总分、学生版 / 教师版哪些字段出现、答案放哪里，都只在这里决定并只在这里测试；
PDF / Word / Markdown / LaTeX 导出器拿同一份视图各自写出目标语法。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from ..domain.export import AnswerPlacement, ExportOptions, ExportVersion
from ..domain.paper import FigureSpec, Item, ItemKind, Paper, Section, VerifyStatus

_NUMERALS = "一二三四五六七八九十"
_HAS_NUMERAL = re.compile(r"^\s*[一二三四五六七八九十]+\s*[、.．]")
LETTERS = "ABCDEFGH"


def cn_numeral(n: int) -> str:
    """1~99 的中文数字（分区编号用）。"""
    if n <= 10:
        return _NUMERALS[n - 1]
    if n < 20:
        return "十" + _NUMERALS[n - 11]
    tens, ones = divmod(n, 10)
    return _NUMERALS[tens - 1] + "十" + (_NUMERALS[ones - 1] if ones else "")


def fmt_score(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:g}"


@dataclass
class ItemView:
    number: int
    kind: ItemKind
    stem: str
    options: list[str]
    score: float | None
    answer: str | None  # 题后答案（教师版 inline）；None = 不在题后出现
    solution: str | None
    space: str  # 学生版作答空间："none" | "short" | "medium" | "long"（高度见 SPACE_CM）
    review: bool  # 教师版："需复核"标注
    figures: list[FigureSpec] = field(default_factory=list)


@dataclass
class SectionView:
    heading: str  # 空 = 不显示分区标题
    note: str  # 括号里的分值说明，如"每题 2 分，共 20 分"
    items: list[ItemView]


@dataclass
class AnswerEntry:
    number: int
    answer: str
    solution: str | None


@dataclass
class PaperView:
    title: str
    version: ExportVersion
    school: str
    class_name: str
    date: str
    duration: str  # 如"45 分钟"
    total_score: float | None
    name_line: bool
    sections: list[SectionView]
    appendix: list[AnswerEntry] | None
    warnings: list[str] = field(default_factory=list)

    @property
    def items(self) -> list[ItemView]:
        return [it for s in self.sections for it in s.items]

    def text(self) -> str:
        """视图里会出现在文件里的全部文本（字形检查、公式计数用）。"""
        parts = [self.title, self.school, self.class_name, self.date]
        for s in self.sections:
            parts.append(s.heading)
            for it in s.items:
                parts += [it.stem, *it.options, it.answer or "", it.solution or ""]
        for e in self.appendix or []:
            parts += [e.answer, e.solution or ""]
        return "\n".join(parts)


SPACE_CM = {"short": 1.6, "medium": 3.0, "long": 5.0}


def _space(item: Item) -> str:
    """学生版的作答空白：主观题按解析的长短估计需要几步。"""
    n = len(item.solution)
    if item.kind in (ItemKind.application, ItemKind.open):
        return "long" if n > 120 else "medium"
    if item.kind == ItemKind.calc:
        return "long" if n > 160 else "medium" if n > 70 else "short"
    return "none"


def _stem_with_figures(item: Item) -> str:
    """题干没有引用的插图补在题干后面：规格里有的图不能因为引用漏写而在导出里消失。"""
    stem = item.stem
    for f in item.figures:
        if f"fig:{f.id})" not in stem:
            stem += f"\n\n![{f.alt}](fig:{f.id})"
    return stem


def _answer_text(item: Item) -> str:
    return item.answer.strip() or "（未提供）"


def _note(section: Section) -> str:
    scores = [it.score for it in section.items]
    if not scores or any(s is None for s in scores):
        return ""
    vals = [float(s) for s in scores if s is not None]
    total = sum(vals)
    if len(set(vals)) == 1:
        return f"每题 {fmt_score(vals[0])} 分，共 {fmt_score(total)} 分"
    return f"共 {fmt_score(total)} 分"


def build_view(paper: Paper, opts: ExportOptions) -> PaperView:
    teacher = opts.version == ExportVersion.teacher
    inline = teacher and opts.answers == AnswerPlacement.inline
    appendix_on = teacher and opts.answers == AnswerPlacement.appendix
    show_solution = teacher and opts.include_solution
    warnings: list[str] = []

    sections: list[SectionView] = []
    entries: list[AnswerEntry] = []
    number = 0
    titled = [s for s in paper.sections if s.items]
    for si, sec in enumerate(titled, start=1):
        views: list[ItemView] = []
        for it in sec.items:
            number += 1
            sol = it.solution.strip() if show_solution and it.solution.strip() else None
            views.append(
                ItemView(
                    number=number,
                    kind=it.kind,
                    stem=_stem_with_figures(it),
                    options=list(it.options),
                    score=it.score,
                    answer=_answer_text(it) if inline else None,
                    solution=sol if inline else None,
                    space="none" if teacher else _space(it),
                    review=teacher
                    and opts.mark_review
                    and it.verification.status == VerifyStatus.needs_review,
                    figures=list(it.figures),
                )
            )
            if appendix_on:
                entries.append(AnswerEntry(number, _answer_text(it), sol))
        title = sec.title.strip()
        if not title:
            heading = ""
        elif _HAS_NUMERAL.match(title) or len(titled) == 1:
            heading = title
        else:
            heading = f"{cn_numeral(si)}、{title}"
        sections.append(SectionView(heading=heading, note=_note(sec), items=views))

    scored = [v.score for s in sections for v in s.items if v.score is not None]
    total: float | None = None
    if scored and len(scored) == number:
        total = float(sum(scored))
    meta_total = paper.meta.get("total_score")
    if total is None and isinstance(meta_total, (int, float)):
        total = float(meta_total)
    elif total is not None and isinstance(meta_total, (int, float)) and not math.isclose(total, meta_total):
        warnings.append(
            f"各题分值合计 {fmt_score(total)} 与试卷设定的总分 {fmt_score(float(meta_total))} 不一致"
        )

    h = opts.header
    meta = paper.meta
    duration = h.duration_minutes or meta.get("duration_minutes")
    return PaperView(
        title=paper.title.strip() or "数学练习",
        version=opts.version,
        school=h.school or str(meta.get("school", "") or ""),
        class_name=h.class_name or str(meta.get("class_name", "") or ""),
        date=h.date or str(meta.get("date", "") or ""),
        duration=f"{duration} 分钟" if duration else "",
        total_score=total,
        name_line=h.name_line and not teacher,
        sections=sections,
        appendix=entries if appendix_on and entries else None,
        warnings=warnings,
    )


def option_columns(options: list[str]) -> int:
    """选项排成几列：短的一行四个，中等两列，长的一列（按最长选项的可见字符数，公式按源码长度估计）。"""
    if not options:
        return 1
    longest = max(len(re.sub(r"[$\\{}\s]", "", o)) for o in options)
    if len(options) == 4 and longest <= 9:
        return 4
    if longest <= 22:
        return 2
    return 1

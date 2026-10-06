"""M6 导出：版面视图、内容转换、图形、各格式的冒烟，以及评测检测器本身（构造坏输出确认抓得到）。"""

from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree as ET

import pytest

from verichalk.core.errors import ExportError
from verichalk.domain.export import AnswerPlacement, ExportFormat, ExportOptions, ExportVersion
from verichalk.domain.paper import FigureSpec, Item, ItemKind, Paper, Section, Verification, VerifyStatus
from verichalk.eval.export_eval import ExportCase, check_result, synthetic_paper
from verichalk.figures import FigureError, render_figure
from verichalk.render import ExportResult, export_paper
from verichalk.render.convert import BLANK, convert_fragments, prepare
from verichalk.render.fonts import missing_glyphs
from verichalk.render.pdf import display_fractions
from verichalk.render.view import build_view, cn_numeral, option_columns


def item(i: int, kind: ItemKind = ItemKind.calc, **kw) -> Item:
    kw.setdefault("stem", f"计算第{i}题：$1+{i}=$____")
    kw.setdefault("answer", str(1 + i))
    return Item(id=f"i{i}", kind=kind, **kw)


def paper(*sections: Section, **kw) -> Paper:
    return Paper(id="p", title="测试卷", rev=1, sections=list(sections), **kw)


# ---- 视图 ----
def test_numbering_is_continuous_across_sections() -> None:
    p = paper(
        Section(id="a", title="选择", items=[item(1), item(2)]),
        Section(id="b", title="计算", items=[item(3)]),
    )
    v = build_view(p, ExportOptions())
    assert [it.number for it in v.items] == [1, 2, 3]
    assert [s.heading for s in v.sections] == ["一、选择", "二、计算"]


def test_single_section_title_not_numbered_and_existing_numeral_kept() -> None:
    one = build_view(paper(Section(id="a", title="练习", items=[item(1)])), ExportOptions())
    assert one.sections[0].heading == "练习"
    two = build_view(
        paper(
            Section(id="a", title="一、选择题", items=[item(1)]),
            Section(id="b", title="填空", items=[item(2)]),
        ),
        ExportOptions(),
    )
    assert [s.heading for s in two.sections] == ["一、选择题", "二、填空"]


def test_scores_and_total() -> None:
    p = paper(
        Section(id="a", title="A", items=[item(1, score=3), item(2, score=3)]),
        Section(id="b", title="B", items=[item(3, score=4)]),
    )
    v = build_view(p, ExportOptions())
    assert v.sections[0].note == "每题 3 分，共 6 分"
    assert v.total_score == 10
    mismatch = build_view(p.model_copy(update={"meta": {"total_score": 100}}), ExportOptions())
    assert any("不一致" in w for w in mismatch.warnings)


def test_student_version_hides_answers_everywhere() -> None:
    p = paper(Section(id="a", title="A", items=[item(1, solution="先算再算，得到答案。")]))
    for ans in AnswerPlacement:
        v = build_view(p, ExportOptions(version=ExportVersion.student, answers=ans))
        assert v.appendix is None
        assert all(it.answer is None and it.solution is None for it in v.items)


def test_teacher_inline_vs_appendix_and_solution_switch() -> None:
    p = paper(Section(id="a", title="A", items=[item(1, solution="解析内容")]))
    inline = build_view(p, ExportOptions(version=ExportVersion.teacher, answers=AnswerPlacement.inline))
    assert (
        inline.items[0].answer == "2" and inline.items[0].solution == "解析内容" and inline.appendix is None
    )
    app = build_view(
        p,
        ExportOptions(
            version=ExportVersion.teacher, answers=AnswerPlacement.appendix, include_solution=False
        ),
    )
    assert app.items[0].answer is None and app.appendix and app.appendix[0].solution is None


def test_needs_review_marked_for_teacher_only() -> None:
    it = item(1, verification=Verification(status=VerifyStatus.needs_review))
    p = paper(Section(id="a", title="A", items=[it]))
    assert build_view(p, ExportOptions(version=ExportVersion.teacher)).items[0].review
    assert not build_view(p, ExportOptions(version=ExportVersion.student)).items[0].review


def test_orphan_figure_is_appended_to_stem() -> None:
    fig = FigureSpec(id="f1", kind="number_line", params={"min": 0, "max": 1}, alt="数轴")
    v = build_view(paper(Section(id="a", title="A", items=[item(1, figures=[fig])])), ExportOptions())
    assert "fig:f1" in v.items[0].stem


def test_empty_sections_are_skipped_and_empty_paper_has_no_items() -> None:
    v = build_view(paper(Section(id="a", title="空区")), ExportOptions())
    assert v.items == [] and v.sections == []


@pytest.mark.parametrize(("n", "expect"), [(1, "一"), (10, "十"), (11, "十一"), (20, "二十"), (23, "二十三")])
def test_cn_numeral(n: int, expect: str) -> None:
    assert cn_numeral(n) == expect


def test_option_columns() -> None:
    assert option_columns(["1", "2", "3", "4"]) == 4
    assert option_columns(["锐角三角形", "直角三角形", "钝角三角形"]) == 2
    assert option_columns(["先算 $100\\times25$，再算 $2\\times25$，最后把两个乘积相加得到结果"] * 4) == 1


# ---- 转换 ----
def test_prepare_replaces_blanks_outside_math_only() -> None:
    figs: list[str] = []
    out = prepare(
        "填 ____ 或 \\_\\_\\_ ，式子 $a_{1}+____$ 与 ![](fig:f1)", lambda fid: figs.append(fid) or "ZZFIG0ZZ"
    )
    assert out.count(BLANK) == 2
    assert "$a_{1}+____$" in out and figs == ["f1"] and "ZZFIG0ZZ" in out


def test_convert_fragments_roundtrip_count_and_math() -> None:
    frags = ["第一段 $\\frac{1}{2}$", "", "第二段\n第二行", "$x\\times y$"]
    out = convert_fragments(frags, "typst")
    assert len(out) == 4 and out[1] == ""
    assert "frac" in out[0] or "/" in out[0]
    assert "\r" not in "".join(out)
    assert "times" in out[3]


def test_convert_does_not_apply_smart_typography_or_subscript() -> None:
    (out,) = convert_fragments(['范围 3~5 与 a^b 与 "引号" 与 3--5'], "latex")
    assert "~" not in out.replace("\\textasciitilde", "") or "\\textasciitilde" in out
    assert "\\textsuperscript" not in out


def test_display_fractions_only_for_inline_with_fraction() -> None:
    assert display_fractions("$3 / 4 + 1 / 8$") == "$display(3 / 4 + 1 / 8)$"
    assert display_fractions("$1 + 2$") == "$1 + 2$"
    assert display_fractions("$ a / b $") == "$ a / b $"  # 块公式已是显示样式
    assert display_fractions("$x = 1, y / 2$") == "$x = 1, y / 2$"  # 顶层逗号不能放进 display()
    assert display_fractions("$3 \\/ 4$") == "$3 \\/ 4$"  # 字面斜杠不是分数


# ---- 图形 ----
@pytest.mark.parametrize(
    ("kind", "params"),
    [
        ("number_line", {"min": 0, "max": 2, "step": 0.5, "points": [{"value": 1.5, "label": "A"}]}),
        ("bar_chart", {"categories": ["a", "b"], "values": [3, 5]}),
        ("shape", {"shape": "triangle", "labels": {"base": "4"}}),
        ("grid", {"rows": 3, "cols": 4, "shaded": [[0, 0]]}),
    ],
)
def test_figures_render_valid_deterministic_svg(kind: str, params: dict) -> None:
    spec = FigureSpec(id="f", kind=kind, params=params)
    a, b = render_figure(spec), render_figure(spec)
    assert a.svg == b.svg and a.width > 0 and a.height > 0
    ET.fromstring(a.svg)  # 合法 XML


@pytest.mark.parametrize(
    ("kind", "params"),
    [
        ("hologram", {}),
        ("number_line", {"min": 0}),
        ("number_line", {"min": 1, "max": 0}),
        ("bar_chart", {"categories": ["a"], "values": [1, 2]}),
        ("shape", {"shape": "blob"}),
        ("grid", {"rows": 0, "cols": 3}),
    ],
)
def test_bad_figure_specs_raise_figure_error(kind: str, params: dict) -> None:
    with pytest.raises(FigureError):
        render_figure(FigureSpec(id="f", kind=kind, params=params))


def test_figure_text_is_escaped() -> None:
    svg = render_figure(
        FigureSpec(id="f", kind="shape", params={"shape": "rectangle", "labels": {"length": "<b>&"}})
    ).svg
    ET.fromstring(svg)
    assert "&lt;b&gt;&amp;" in svg


# ---- 字体 ----
def test_missing_glyphs_ignores_ascii_and_reports_unknown() -> None:
    assert missing_glyphs("abc 123 + 数学 ×") == []
    assert "\U0010fffd" in missing_glyphs("a\U0010fffdb")  # 私有区码位，任何字体都没有


# ---- 各格式冒烟 ----
SMALL = paper(
    Section(
        id="a",
        title="练习",
        items=[
            item(
                1,
                ItemKind.choice,
                stem="哪个更大？",
                options=["$\\frac{1}{2}$", "0.3", "0.25", "0.1"],
                answer="A",
                solution="比较大小：$\\frac12>0.3$。",
                score=2,
            ),
            item(2, score=3, solution="直接算。"),
        ],
    )
)


def export(fmt: ExportFormat, ver: ExportVersion = ExportVersion.teacher, **kw) -> ExportResult:
    return export_paper(SMALL, ExportOptions(format=fmt, version=ver, **kw))


def test_pdf_export() -> None:
    r = export(ExportFormat.pdf)
    assert (
        r.data.startswith(b"%PDF") and r.filename.endswith("教师版.pdf") and r.media_type == "application/pdf"
    )


def test_docx_has_native_formulas() -> None:
    r = export(ExportFormat.docx)
    with zipfile.ZipFile(io.BytesIO(r.data)) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    assert xml.count("<m:oMath>") + xml.count("<m:oMath ") >= 3 and "<w:drawing" not in xml


def test_markdown_export_keeps_source_text() -> None:
    text = export(ExportFormat.md, answers=AnswerPlacement.inline).data.decode("utf-8")
    assert "$\\frac{1}{2}$" in text and "**答案：**" in text and text.count("A. ") == 1


def test_markdown_student_has_no_answers() -> None:
    text = export(ExportFormat.md, ExportVersion.student).data.decode("utf-8")
    assert "答案" not in text and "解析" not in text


def test_student_header_lines_follow_the_option() -> None:
    """不勾选"显示姓名 / 班级 / 得分填写线"：三条线一起去掉（原先只去掉了姓名）；勾选时三条都在。"""
    from verichalk.domain.export import ExportHeader

    def texts(name_line: bool) -> dict[str, str]:
        out = {}
        for fmt in (ExportFormat.md, ExportFormat.docx, ExportFormat.pdf):
            r = export(fmt, ExportVersion.student, header=ExportHeader(name_line=name_line))
            if fmt == ExportFormat.docx:
                with zipfile.ZipFile(io.BytesIO(r.data)) as z:
                    out["docx"] = z.read("docProps/core.xml").decode("utf-8") + z.read(
                        "word/document.xml"
                    ).decode("utf-8")
            elif fmt == ExportFormat.pdf:
                import pymupdf

                out["pdf"] = "".join(str(pg.get_text()) for pg in pymupdf.open(stream=r.data))
            else:
                out["md"] = r.data.decode("utf-8")
        return out

    for name, text in texts(True).items():
        assert all(k in text for k in ("姓名", "班级", "得分")), name
    for name, text in texts(False).items():
        assert not any(k in text for k in ("姓名", "班级", "得分")), name


def test_latex_export_is_source_only_and_balanced() -> None:
    r = export(ExportFormat.tex)
    tex = r.data.decode("utf-8")
    assert (
        r.filename.endswith(".tex")
        and "\\documentclass" in tex
        and tex.count("\\begin{") == tex.count("\\end{")
    )
    assert "ZZ" not in tex


def test_latex_with_figure_is_zip() -> None:
    fig = FigureSpec(id="f1", kind="number_line", params={"min": 0, "max": 2})
    p = paper(Section(id="a", title="图", items=[item(1, stem="看图：![](fig:f1)", figures=[fig])]))
    r = export_paper(p, ExportOptions(format=ExportFormat.tex))
    assert r.filename.endswith(".zip")
    with zipfile.ZipFile(io.BytesIO(r.data)) as z:
        assert "paper.tex" in z.namelist() and any(n.endswith(".png") for n in z.namelist())


def test_empty_paper_gives_friendly_error() -> None:
    with pytest.raises(ExportError) as ei:
        export_paper(paper(), ExportOptions())
    assert "还没有题目" in ei.value.user_message


def test_bad_figure_degrades_with_warning_not_failure() -> None:
    fig = FigureSpec(id="f1", kind="hologram", alt="全息图")
    p = paper(Section(id="a", title="图", items=[item(1, stem="看图：![](fig:f1)", figures=[fig])]))
    for fmt in ExportFormat:
        r = export_paper(p, ExportOptions(format=fmt))
        assert any("无法绘制" in w for w in r.warnings), fmt


def test_long_paper_exports_all_formats() -> None:
    p = synthetic_paper(40)
    for fmt in ExportFormat:
        r = export_paper(p, ExportOptions(format=fmt, version=ExportVersion.teacher))
        assert len(r.data) > 500


# ---- 评测检测器本身：构造坏输出，确认抓得到 ----
def _case(p: Paper = SMALL) -> ExportCase:
    return ExportCase("t", [], p)


def test_detector_catches_student_leak_and_placeholders() -> None:
    opts = ExportOptions(format=ExportFormat.md, version=ExportVersion.student)
    leaked = ExportResult("# 测试卷\n**答案：** 3 ZZBLANKZZ 哪个更大？".encode(), "x.md", "text/markdown")
    codes = {f.code for f in check_result(_case(), opts, leaked)}
    assert "X5" in codes and "X0" in codes


def test_detector_catches_missing_native_formulas_in_docx() -> None:
    good = export(ExportFormat.docx, ExportVersion.student)
    opts = ExportOptions(format=ExportFormat.docx, version=ExportVersion.student)
    assert not check_result(_case(), opts, good)
    # 把所有公式对象去掉 → 应报 X1
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(good.data)) as zin, zipfile.ZipFile(buf, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "word/document.xml":
                data = data.replace(b"<m:oMath>", b"<m:oMathX>").replace(b"</m:oMath>", b"</m:oMathX>")
            zout.writestr(info, data)
    bad = ExportResult(buf.getvalue(), good.filename, good.media_type)
    assert any(f.code == "X1" for f in check_result(_case(), opts, bad))


def test_detector_catches_tex_leak_and_missing_stem_in_pdf() -> None:
    opts = ExportOptions(format=ExportFormat.pdf, version=ExportVersion.student)
    ok = export(ExportFormat.pdf, ExportVersion.student)
    assert not check_result(_case(), opts, ok)
    other = paper(Section(id="a", title="练习", items=[item(1, stem="完全不同的题干文字内容。")]))
    codes = {f.code for f in check_result(_case(other), opts, ok)}
    assert "X5" in codes

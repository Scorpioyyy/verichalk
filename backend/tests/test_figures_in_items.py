"""题目带图（D60）：写题给出的规格变成 `FigureSpec`、题面引用正确的 ID、结构检查渲染校验、核验模型读到图的文字描述。"""

from __future__ import annotations

from verichalk.domain.blueprint import AnswerPart, FigureDraft, WriteOut
from verichalk.domain.paper import FigureSpec
from verichalk.figures import describe_figure
from verichalk.stages.produce import attach_figure, plain_labels
from verichalk.verify.checks import VerifyInput, check_structure, model_stem
from verichalk.verify.content import structure_issues

BAR = FigureDraft(
    kind="bar_chart",
    params={
        "title": "各班借书数量",
        "categories": ["一班", "二班"],
        "values": [24, 30],
        "y_label": "本数/本",
    },
    alt="各班借书数量统计图",
)


def _w(stem: str, fig: FigureDraft | None = BAR) -> WriteOut:
    return WriteOut(stem=stem, figure=fig, answers=[AnswerPart(value="6")])


def test_attach_figure_rewrites_reference_or_appends_one() -> None:
    w, spec = attach_figure(_w("如图 ![图](fig:abc)，二班比一班多借几本？"))
    assert spec is not None and spec.kind == "bar_chart" and spec.alt == "各班借书数量统计图"
    assert f"(fig:{spec.id})" in w.stem and "fig:abc" not in w.stem
    w2, spec2 = attach_figure(_w("如图，二班比一班多借几本？"))
    assert spec2 is not None and w2.stem.endswith(f"![各班借书数量统计图](fig:{spec2.id})")
    w3, none = attach_figure(_w("二班比一班多借几本？", None))
    assert none is None and w3.stem == "二班比一班多借几本？"


def test_figure_dependence_is_allowed_only_with_a_figure() -> None:
    args = {
        "kind": "fill",
        "stem": "如图，二班比一班多借____本。",
        "options": [],
        "answer_values": ["6"],
        "solution": "30-24=6",
    }
    assert any("如图" in i for i in structure_issues(**args))
    assert structure_issues(**args, has_figure=True) == []


async def test_structure_check_renders_the_figure() -> None:
    good = FigureSpec(id="f1", kind="bar_chart", params=BAR.params)
    bad = FigureSpec(id="f2", kind="bar_chart", params={"categories": ["一班"], "values": [1, 2]})
    base = {"kind": "fill", "stem": "如图，二班比一班多借____本。", "answers": [AnswerPart(value="6")]}
    assert (await check_structure(VerifyInput(**base, figure=good))).status.value == "pass"
    r = await check_structure(VerifyInput(**base, figure=bad))
    assert r.status.value == "fail" and "图形规格不合法" in r.detail


def test_models_read_the_figure_as_text() -> None:
    spec = FigureSpec(id="f1", kind="bar_chart", params=BAR.params)
    stem = model_stem(
        VerifyInput(kind="fill", stem="如图，二班比一班多借____本。\n\n![图](fig:f1)", figure=spec)
    )
    assert "fig:" not in stem and "一班 24" in stem and "二班 30" in stem


def test_describe_each_kind() -> None:
    def d(kind: str, **params: object) -> str:
        return describe_figure(FigureSpec(id="x", kind=kind, params=dict(params)))

    assert (
        d("shape", shape="rectangle", labels={"length": "8 cm", "width": "5 cm"})
        == "一个长方形：长标着“8 cm”，宽标着“5 cm”"
    )
    assert "从 0 到 2，每格 0.2" in d(
        "number_line", min=0, max=2, step=0.2, points=[{"value": 1.4, "label": "A"}]
    )
    assert "A在 1.4" in d("number_line", min=0, max=2, step=0.2, points=[{"value": 1.4, "label": "A"}])
    g = d("grid", rows=3, cols=4, shaded=[[0, 1]], coordinate=True)
    assert "3 行 4 列" in g and "第1行第2列" in g


def test_description_lists_only_labels_the_figure_draws() -> None:
    spec = FigureSpec(
        id="x", kind="shape", params={"shape": "circle", "labels": {"radius": "?", "area": "314 m²"}}
    )
    assert describe_figure(spec) == "一个圆：半径标着“?”"  # 图上不画 area，描述里也不能有


def test_figure_labels_become_plain_text() -> None:
    _, spec = attach_figure(
        WriteOut(
            stem="如图",
            figure=FigureDraft(
                kind="shape", params={"shape": "triangle", "labels": {"base": "$80\\text{ cm}$"}}
            ),
        )
    )
    assert spec is not None and spec.params["labels"]["base"] == "80 cm"
    assert plain_labels("$2400 \\text{ cm}^2$") == "2400 cm²"


async def test_malformed_figure_params_fail_the_check_instead_of_crashing() -> None:
    # 写题模型用 "?" 表示被遮住的一栏：渲染不了，应当退回修改，而不是让运行崩溃
    for params in (
        {"categories": ["一班", "二班"], "values": [24, "?"]},
        {"categories": ["一班"], "values": None},
        {"min": "a", "max": 2},
        {"shape": "parallelogram", "labels": ["底 6 cm", "高 4 cm"]},  # 标注写成了列表
    ):
        kind = "number_line" if "min" in params else "shape" if "shape" in params else "bar_chart"
        r = await check_structure(
            VerifyInput(
                kind="fill",
                stem="如图，____",
                answers=[AnswerPart(value="1")],
                figure=FigureSpec(id="x", kind=kind, params=params),
            )
        )
        assert r.status.value == "fail" and "图形规格不合法" in r.detail


def test_statistics_charts_render_and_describe() -> None:
    from verichalk.figures import render_figure

    two = {
        "title": "两班借书",
        "categories": ["一月", "二月", "三月"],
        "series": [{"name": "一班", "values": [12, 15, 9]}, {"name": "二班", "values": [10, 14, 11]}],
    }
    for kind in ("bar_chart", "line_chart"):
        spec = FigureSpec(id="x", kind=kind, params=two)
        assert render_figure(spec).svg.startswith("<svg") and "二班" in render_figure(spec).svg
        d = describe_figure(spec)
        assert d.startswith("复式") and "一月 一班 12、二班 10" in d
    pie = FigureSpec(
        id="p",
        kind="pie_chart",
        params={
            "parts": [{"label": "科普", "percent": 30}, {"label": "其他", "percent": 70}],
            "hide": ["其他"],
        },
    )
    assert "<path" in render_figure(pie).svg
    assert describe_figure(pie) == "扇形统计图：科普 30%，其他 （百分比没有标出）"


def test_invalid_figures_name_what_is_allowed() -> None:
    # 写题模型自创类型名（quadrilateral、line_segment）时，报错要列出可用的，修复才能一次改对
    from verichalk.figures import FigureError, render_figure

    for kind, params, hint in [
        ("line_segment", {}, "shape"),
        ("shape", {"shape": "quadrilateral"}, "trapezoid"),
        ("pie_chart", {"parts": [{"label": "a", "percent": 30}, {"label": "b", "percent": 60}]}, "合计 100"),
        ("bar_chart", {"categories": ["a"], "series": [{"name": "x", "values": [1, 2]}]}, "个数"),
    ]:
        try:
            render_figure(FigureSpec(id="x", kind=kind, params=params))
        except FigureError as e:
            assert hint in str(e)
        else:
            raise AssertionError(kind)


def test_clock_face_renders_and_describes_hand_positions() -> None:
    from verichalk.figures import render_figure

    half = FigureSpec(id="c", kind="clock", params={"hour": 9, "minute": 30})
    assert render_figure(half).svg.count("<text") == 12
    assert (
        describe_figure(half) == "钟面：时针在 9 和 10 中间，分针指向 6"
    )  # 不写出"9 时半"：读钟面就是要考的
    assert (
        describe_figure(FigureSpec(id="c", kind="clock", params={"hour": 15}))
        == "钟面：时针指向 3，分针指向 12"
    )


def test_shape_labels_must_be_values_not_captions() -> None:
    from verichalk.figures import FigureError, render_figure

    try:
        render_figure(
            FigureSpec(id="x", kind="shape", params={"shape": "circle", "labels": {"diameter": "钟面示意图"}})
        )
    except FigureError as e:
        assert "数值" in str(e)
    else:
        raise AssertionError("caption accepted as a label")

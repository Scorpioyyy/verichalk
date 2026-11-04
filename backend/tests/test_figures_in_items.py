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

"""答案规范化与精确比较（CLAUDE.md §2.8：不用浮点比较答案）。"""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

import pytest

from verichalk.domain.blueprint import AnswerPart
from verichalk.verify.answers import answers_equal, format_answer, parse_value, program_values


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (["11.10"], ["11.1"]),
        (["1/2"], ["0.5"]),
        (["50%"], ["0.5"]),
        (["1又1/2"], ["3/2"]),
        (["1 1/2"], ["1.5"]),
        (["12元"], ["12"]),
        (["1,200"], ["1200"]),
        (["６"], ["6"]),
        (["B"], ["b"]),
        (["（C）"], ["C"]),
        (["对"], ["√"]),
        (["错误"], ["×"]),
        (["6……2"], ["6", "2"]),
        (["6余2"], ["6", "2"]),
        (["锐角 三角形"], ["锐角三角形"]),
    ],
)
def test_equal(a: list[str], b: list[str]) -> None:
    assert answers_equal(a, b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (["11.1"], ["11.2"]),
        (["1/3"], ["0.333"]),  # 近似值不等于精确值
        (["6", "2"], ["2", "6"]),  # 多问按顺序
        (["6"], ["6", "2"]),
        (["A"], ["B"]),
        (["对"], ["错"]),
        (["12"], ["一二"]),
    ],
)
def test_not_equal(a: list[str], b: list[str]) -> None:
    assert not answers_equal(a, b)


def test_parse_is_exact() -> None:
    a, b = parse_value("0.1"), parse_value("0.2")
    assert isinstance(a, Fraction) and isinstance(b, Fraction)
    assert a + b == Fraction(3, 10)  # 浮点会得到 0.30000000000000004


def test_program_values() -> None:
    assert program_values([Decimal("11.10"), Fraction(3, 4), 5, True]) == ["11.10", "3/4", "5", "对"]
    assert program_values(Fraction(6, 3)) == ["2"]


def test_format_answer() -> None:
    assert format_answer([AnswerPart(value="11.10", unit="元")]) == "11.10元"
    parts = [AnswerPart(label="商", value="6"), AnswerPart(label="余数", value="2")]
    assert format_answer(parts) == "商：6；余数：2"
    assert format_answer([]) == ""


def test_tex_and_yes_no_forms() -> None:
    bs = chr(92)
    assert answers_equal(["$" + bs + "frac{3}{8}$"], ["3/8"])
    assert answers_equal([f"{bs}dfrac{{1}}{{2}}"], ["0.5"])
    assert answers_equal(["不够"], ["错"])
    assert answers_equal(["够"], ["对"])
    assert not answers_equal(["够"], ["错"])


def test_percent_conventions() -> None:
    assert answers_equal(["22.5%"], ["22.5"])
    assert answers_equal(["22.5%"], ["0.225"])
    assert not answers_equal(["22.5%"], ["2.25"])


def test_expression_and_equation_answers() -> None:
    """教材里"列式""写出方程"类题的答案：算式按数值、方程按解比较。"""
    assert answers_equal(["5×4"], ["4×5"])
    assert answers_equal(["(3+2)×4"], ["20"])
    assert answers_equal(["4x=20"], ["x=20÷4"])
    assert answers_equal(["x+25=60"], ["x=35"])
    assert answers_equal(["2(x+1)=10"], ["x=4"])
    assert not answers_equal(["4x=20"], ["x=4"])
    assert not answers_equal(["5×4"], ["5+4"])
    assert answers_equal(["5×4=20"], ["20"])


def test_chinese_numerals_in_text_answers() -> None:
    assert answers_equal(["三位数"], ["3位数"])
    assert answers_equal(["二十五"], ["25"])
    assert not answers_equal(["三位数"], ["4位数"])

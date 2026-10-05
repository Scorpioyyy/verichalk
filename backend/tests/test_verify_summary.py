"""核验状态汇总规则（D9）：L1 不变量。"""

from __future__ import annotations

import pytest

from verichalk.domain.paper import CheckResult, CheckStatus, VerifyStatus
from verichalk.verify.summary import failing, summarize

P, W, F, S = CheckStatus.passed, CheckStatus.warn, CheckStatus.fail, CheckStatus.skip


def checks(**kw: CheckStatus) -> list[CheckResult]:
    base = {"structure": P, "program": P, "blind": P, "boundary": P, "quality": P}
    return [CheckResult(name=k, status=v) for k, v in (base | kw).items()]


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({}, VerifyStatus.verified),
        ({"blind": S}, VerifyStatus.checked),
        ({"program": S}, VerifyStatus.checked),
        ({"program": S, "blind": S}, VerifyStatus.needs_review),
        ({"quality": W}, VerifyStatus.needs_review),
        ({"blind": W}, VerifyStatus.needs_review),
        ({"boundary": F}, VerifyStatus.rejected),
        ({"blind": F}, VerifyStatus.rejected),
        ({"structure": F, "blind": S}, VerifyStatus.rejected),
    ],
)
def test_status(override: dict[str, CheckStatus], expected: VerifyStatus) -> None:
    assert summarize(checks(**override)).status == expected


def test_fail_always_wins() -> None:
    assert summarize(checks(program=F, blind=P)).status == VerifyStatus.rejected


def test_failing_lists_names() -> None:
    assert [c.name for c in failing(checks(blind=F, quality=F))] == ["blind", "quality"]


async def test_program_check_maps_choice_letter_to_option() -> None:
    """选择题：求解程序返回正确选项的内容，与答案字母对应的选项比较。"""
    from verichalk.domain.blueprint import AnswerPart
    from verichalk.verify.checks import VerifyInput, check_program

    code = "from decimal import Decimal\ndef solve():\n    return [Decimal('3.6') + Decimal('1.4')]\n"
    inp = VerifyInput(
        kind="choice",
        stem="3.6+1.4=？",
        options=["4", "5", "5.5", "6"],
        answers=[AnswerPart(value="B")],
        solver_code=code,
    )
    assert (await check_program(inp)).status == CheckStatus.passed
    inp.answers = [AnswerPart(value="C")]
    assert (await check_program(inp)).status == CheckStatus.fail

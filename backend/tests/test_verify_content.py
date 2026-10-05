"""内容规范化（D27）与结构检查。"""

from __future__ import annotations

from verichalk.verify.content import check_math, normalize_text, structure_issues


def test_normalize_trims_and_moves_blank() -> None:
    assert normalize_text("计算：$3.6 - 1.25 = $____") == "计算：$3.6 - 1.25 =$____"
    assert normalize_text("$ 7×8 = ( ) $") == r"$7\times 8 =$____"
    assert normalize_text(r"$a=\_\_\_$") == "$a=$____"


def test_normalize_idempotent() -> None:
    once = normalize_text("求 $ 12÷3 = $ ____ 的值")
    assert normalize_text(once) == once


def test_check_math() -> None:
    assert check_math(r"$\frac{1}{2}+\frac{1}{3}$") == []
    assert check_math("$1+2") != []
    assert check_math(r"$\frac{1}{2$") != []
    assert check_math(r"$\frac12$") != []  # 本项目要求显式花括号
    assert any("未知命令" in x for x in check_math(r"$\foo{1}$"))


def _issues(**kw: object) -> list[str]:
    base: dict[str, object] = {
        "kind": "fill",
        "stem": "$1+1=$____",
        "options": [],
        "answer_values": ["2"],
        "solution": "1+1=2",
    }
    return structure_issues(**(base | kw))  # type: ignore[arg-type]


def test_structure_ok() -> None:
    assert _issues() == []


def test_structure_choice_rules() -> None:
    assert _issues(kind="choice", stem="哪个最大？", options=["1", "2"], answer_values=["A"]) != []
    assert _issues(kind="choice", stem="哪个最大？", options=["1", "2", "3", "4"], answer_values=["E"]) != []
    assert _issues(kind="choice", stem="哪个最大？", options=["1", "2", "3", "4"], answer_values=["C"]) == []
    assert _issues(kind="calc", options=["1", "2", "3", "4"]) != []


def test_structure_detects_known_failures() -> None:
    assert any("自我修正" in x for x in _issues(solution="先算 3+4=8……等等，重新算：3+4=7"))
    assert any("依赖图形" in x for x in _issues(stem="如图，求阴影部分面积 ____"))
    assert any("填空位置" in x for x in _issues(stem="1+1 等于几？"))
    assert any("答案为空" in x for x in _issues(answer_values=[]))
    assert any("判断题" in x for x in _issues(kind="judge", stem="3>2", answer_values=["也许"]))


def test_fix_tex_escapes_after_json_decoding() -> None:
    """模型没有双写反斜杠时，JSON 解码会把 times 命令变成制表符 + imes，frac 命令变成换页符 + rac。"""
    import json

    from verichalk.verify.content import fix_tex_escapes

    bs = chr(92)  # 反斜杠
    good = f"$2.5{bs}times 3={bs}frac{{1}}{{2}}$"
    bad = json.loads('"' + good.replace(bs, bs) + '"')  # JSON 里的单个反斜杠被当成转义
    assert "\t" in bad
    assert fix_tex_escapes(bad) == good
    assert normalize_text(bad) == good


def test_literal_newline_restored_but_tex_neq_kept() -> None:
    bs = chr(92)
    assert normalize_text("第一行" + bs + "n第二行") == "第一行\n第二行"
    assert normalize_text(f"$a{bs}neq b$") == f"$a{bs}neq b$"


# ---- 选择题答案字母由程序结果确定（produce.fix_choice_letter）----
async def test_fix_choice_letter_follows_program_value() -> None:
    from verichalk.domain.blueprint import AnswerPart, WriteOut
    from verichalk.domain.paper import ItemKind
    from verichalk.stages.produce import fix_choice_letter

    code = "from decimal import Decimal\ndef solve():\n    return [Decimal('3.6')]"
    base = WriteOut(
        stem="s", options=["3.06", "3.60", "3.7", "3.16"], answers=[AnswerPart(value="C")], solver_code=code
    )
    fixed = await fix_choice_letter(base, ItemKind.choice)
    assert fixed.answers[0].value == "B"  # 程序算出 3.6 = 选项 B（3.60），模型写的 C 被纠正
    ok = base.model_copy(update={"answers": [AnswerPart(value="B")]})
    assert await fix_choice_letter(ok, ItemKind.choice) is ok
    nomatch = base.model_copy(update={"options": ["1", "2", "3", "4"]})
    assert await fix_choice_letter(nomatch, ItemKind.choice) is nomatch  # 对不上任何选项：不改，交给核验
    dup = base.model_copy(update={"options": ["3.6", "3.60", "3.7", "3.16"]})
    assert await fix_choice_letter(dup, ItemKind.choice) is dup  # 多个选项相等：不猜
    assert await fix_choice_letter(base, ItemKind.fill) is base

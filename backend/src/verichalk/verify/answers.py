"""答案的规范化与精确比较（CLAUDE.md §2.8：禁止浮点参与答案比较）。

所有数值答案解析成 `Fraction` 后再比较，因此 `11.1` 与 `11.10`、`0.5` 与 `1/2`、`50%` 与 `0.5` 相等；
选择题比较选项字母，判断题归一为"对 / 错"，其余文字答案去空白与标点后比较。
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Any

from ..domain.blueprint import AnswerPart
from .expr import evaluate

Canon = Fraction | str

_TRUE = {"对", "正确", "√", "✓", "✔", "是", "true", "yes", "t", "够", "能", "可以", "会", "有"}
_FALSE = {
    "错",
    "错误",
    "×",
    "✗",
    "✘",
    "否",
    "不对",
    "false",
    "no",
    "f",
    "不够",
    "不能",
    "不可以",
    "不会",
    "没有",
    "不是",
}
_NUM = r"[-+]?\d+(?:\.\d+)?"
_FRAC = re.compile(rf"^({_NUM})\s*/\s*(\d+)$")
_MIXED = re.compile(r"^([-+]?\d+)\s*(?:又|\s)\s*(\d+)\s*/\s*(\d+)$")
_PERCENT = re.compile(rf"^({_NUM})\s*%$")
_LEAD_NUM = re.compile(rf"^({_NUM}(?:\s*/\s*\d+)?|\d+\s*(?:又|\s)\s*\d+\s*/\s*\d+)")
_REMAINDER = re.compile(r"\s*(?:……|\.{3,}|…+|余)\s*")


_TEX_FRAC = re.compile(r"\\d?frac\s*\{\s*(-?\d+(?:\.\d+)?)\s*\}\s*\{\s*(\d+)\s*\}")


def _clean(s: str) -> str:
    s = unicodedata.normalize("NFKC", str(s)).strip()
    s = _TEX_FRAC.sub(lambda m: f"{m.group(1)}/{m.group(2)}", s)  # 选项 / 答案里的 TeX 分数
    s = (
        s.replace("$", "")
        .replace(chr(92) + "%", "%")
        .replace(chr(92) + "times", "×")
        .replace(chr(92) + "div", "÷")
    )
    s = s.replace(",", "").replace("，", "").replace("−", "-").replace("—", "-")
    return s.strip(" 。.；;")


def _to_fraction(s: str) -> Fraction | None:
    s = s.strip()
    try:
        if m := _PERCENT.match(s):
            return Fraction(Decimal(m.group(1))) / 100
        if m := _MIXED.match(s):
            whole = int(m.group(1))
            frac = Fraction(int(m.group(2)), int(m.group(3)))
            return whole - frac if m.group(1).startswith("-") else whole + frac
        if m := _FRAC.match(s):
            return Fraction(Decimal(m.group(1))) / int(m.group(2))
        if re.fullmatch(_NUM, s):
            return Fraction(Decimal(s))
    except (InvalidOperation, ZeroDivisionError, ValueError):
        return None
    return None


_MONEY = re.compile(r"(?:(\d+)元)?(?:(\d+)角)?(?:(\d+)分)?")
_PI = re.compile(r"(\d+(?:\.\d+)?(?:/\d+)?)?π")


def _written_number(t: str) -> Fraction | None:
    """读法里的数："八十五""十二点五""85"。"""
    if (f := _to_fraction(t)) is not None:
        return f
    whole, _, dec = t.partition("点")
    n = _cn_to_int(whole) if whole else None
    if n is None or any(ch not in _CN_DIGIT for ch in dec):
        return None
    return Fraction(Decimal(f"{n}.{''.join(str(_CN_DIGIT[ch]) for ch in dec) or 0}"))


def _special_value(s: str) -> Fraction | None:
    """几种写法不同、数值相同的答案：百分数的读法（"百分之八十五" = 85%）、复名数（"4元7角" = 4.7 元）、
    含 π 的结果（"9π" = 9 × 3.14，小学取 π = 3.14）。"""
    if s.startswith("百分之") and (v := _written_number(s[3:])) is not None:
        return v / 100
    t = _cn_numerals(s)
    if (m := _MONEY.fullmatch(t)) and sum(g is not None for g in m.groups()) >= 2:
        yuan, jiao, fen = (int(g or 0) for g in m.groups())
        return yuan + Fraction(jiao, 10) + Fraction(fen, 100)
    if m := _PI.fullmatch(t):
        k = _to_fraction(m.group(1) or "1")
        return k * Fraction(314, 100) if k is not None else None
    return None


def parse_value(raw: str) -> Canon:
    """答案字符串 → 规范值：数值为 `Fraction`，其余为规范化后的字符串。"""
    if (v := _special_value(_clean(raw))) is not None:
        return v
    s = _cn_numerals(_clean(raw))
    f = _to_fraction(s)
    if f is not None:
        return f
    low = s.lower()
    if low in _TRUE:
        return "对"
    if low in _FALSE:
        return "错"
    if re.fullmatch(r"[A-Da-d]", s):
        return s.upper()
    if m := re.fullmatch(r"[（(]?([A-Da-d])[)）]?[.、．]?", s):
        return m.group(1).upper()
    # 带单位的数值："11.10元"、"3/4 千克"：取开头的数值，其余视为单位
    m = _LEAD_NUM.match(s)
    if m:
        f = _to_fraction(m.group(1))
        if f is not None and not re.search(r"[0-9]", s[m.end() :]):
            return f
    v = evaluate(s)  # 算式按数值、方程按解比较（见 expr.py）
    if v is not None:
        return v
    return re.sub(r"[\s，。,.；;：:、]+", "", s)


_CN_DIGIT = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CN_NUM = re.compile(r"[零一二两三四五六七八九十百]+")


def _cn_to_int(t: str) -> int | None:
    """ "三""十二""二十五""一百零五" → 整数（够用的简化版）。"""
    if not t:
        return None
    total, cur = 0, 0
    for ch in t:
        if ch in _CN_DIGIT:
            cur = _CN_DIGIT[ch]
        elif ch == "十":
            total += (cur or 1) * 10
            cur = 0
        elif ch == "百":
            total += (cur or 1) * 100
            cur = 0
        else:
            return None
    return total + cur


def _cn_numerals(s: str) -> str:
    """答案文字里的中文数字写成阿拉伯数字（"三位数" 与 "3位数" 相同；只在数量词前或整串都是中文数字时转换）。"""
    if _CN_NUM.fullmatch(s):
        n = _cn_to_int(s)
        return str(n) if n is not None else s

    def sub(m: re.Match[str]) -> str:
        n = _cn_to_int(m.group(0))
        return str(n) if n is not None else m.group(0)

    return re.sub(
        r"[零一二两三四五六七八九十百]+(?=位|个|只|条|份|人|次|层|行|列|组|道|本|元|角|分|米|克|吨|升)",
        sub,
        s,
    )


def _split(values: list[str]) -> list[str]:
    """展开"商……余数"这类写在一个值里的多个答案（清洗后的原文）。"""
    out: list[str] = []
    for v in values:
        s = _clean(v)
        out += [p for p in (_REMAINDER.split(s) if _REMAINDER.search(s) else [s]) if p != ""]
    return out


def answer_parts(values: list[str]) -> list[str]:
    """答案按问展开后的各部分（"9……4"是商与余数两部分）。"""
    return _split(values)


def flatten(values: list[str]) -> list[Canon]:
    return [parse_value(p) for p in _split(values)]


_TEXT_PREFIX = re.compile(r"^(?:星期|周|礼拜|一[个只条本盒辆张件块瓶袋支把根])(?=.)")
_TEXT_SUFFIX = re.compile(r"(?<=.)(?:颜色|色)$")


def _text_core(s: str) -> str:
    """文字答案的写法差异（"红"与"红色"、"三"与"星期三"、"积木"与"一盒积木"）：去掉这几种前后缀再比较。"""
    return _TEXT_SUFFIX.sub("", _TEXT_PREFIX.sub("", s))


def _pair_equal(ra: str, rb: str) -> bool:
    ca, cb = parse_value(ra), parse_value(rb)
    if ca == cb:
        return True
    if (isinstance(ca, str) or isinstance(cb, str)) and not re.search(r"[不没非]", ra + rb):
        if parse_value(_text_core(_clean(ra))) == parse_value(_text_core(_clean(rb))):
            return True
    # 百分数的写法约定不统一：`22.5%` 与程序算出的 `22.5`（百分数的数值）视为相等
    for pct, other in ((ra, cb), (rb, ca)):
        if pct.endswith("%") and isinstance(other, Fraction):
            base = parse_value(pct[:-1])
            if isinstance(base, Fraction) and base == other:
                return True
    return False


def answers_equal(a: list[str], b: list[str]) -> bool:
    """两组答案是否一致（按问的顺序逐项比较）。"""
    ra, rb = _split(a), _split(b)
    return len(ra) == len(rb) and all(_pair_equal(x, y) for x, y in zip(ra, rb, strict=True))


def program_value_text(v: Any) -> str:
    """沙箱返回的精确值 → 答案字符串。"""
    if isinstance(v, bool):
        return "对" if v else "错"
    if isinstance(v, Fraction):
        return str(v.numerator) if v.denominator == 1 else f"{v.numerator}/{v.denominator}"
    if isinstance(v, Decimal):
        return format(v, "f")
    return str(v)


def program_values(result: Any) -> list[str]:
    """求解程序的返回值（单值或列表）→ 与 `answers` 同序的字符串列表。"""
    items = result if isinstance(result, list | tuple) else [result]
    return [program_value_text(x) for x in items]


def format_answer(parts: list[AnswerPart]) -> str:
    """显示用的答案串：由结构化答案拼装，教师看到的与核验比较的是同一份数据。"""
    if not parts:
        return ""
    if len(parts) == 1:
        p = parts[0]
        return f"{p.value}{p.unit}" if p.unit else p.value
    segs = []
    for p in parts:
        body = f"{p.value}{p.unit}" if p.unit else p.value
        segs.append(f"{p.label}：{body}" if p.label else body)
    return "；".join(segs)


def answer_values(parts: list[AnswerPart]) -> list[str]:
    return [p.value for p in parts]

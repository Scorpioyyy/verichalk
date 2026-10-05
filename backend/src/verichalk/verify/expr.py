"""算式与方程答案的等价判断（教材里"列式""写出方程"类题的答案不是单个数值）。

- **算式**（如 `5×4`、`(3+2)×4`）：安全地按精确算术求值，比较**数值**，因此 `5×4` 与 `4×5` 等价；
- **方程**（如 `4x=20`、`x+25=60`）：含一个未知数（单个字母）的一次方程，比较**解**，因此 `4x=20` 与 `x=20÷4` 等价。

不使用 `eval`：自带的递归下降解析器只认数字、`+ - × ÷ ( )` 和一个未知数，系数用 `Fraction` 精确计算。
这只判断"数学上等价"，不判断"写法是否符合教学要求"（后者属于题面质量，由判官评审）。
"""

from __future__ import annotations

import re
import unicodedata
from fractions import Fraction

_TOKEN = re.compile(r"\s*(\d+(?:\.\d+)?|[A-Za-z]|[+\-*/×÷()=])")


class _Poly:
    """关于未知数的一次式 a·x + b（系数为 Fraction）。"""

    __slots__ = ("a", "b")

    def __init__(self, a: Fraction = Fraction(0), b: Fraction = Fraction(0)) -> None:
        self.a, self.b = a, b

    def __add__(self, o: _Poly) -> _Poly:
        return _Poly(self.a + o.a, self.b + o.b)

    def __sub__(self, o: _Poly) -> _Poly:
        return _Poly(self.a - o.a, self.b - o.b)

    def __mul__(self, o: _Poly) -> _Poly:
        if self.a and o.a:
            raise ValueError("非一次式")
        return _Poly(self.a * o.b + self.b * o.a, self.b * o.b)

    def __truediv__(self, o: _Poly) -> _Poly:
        if o.a or o.b == 0:
            raise ValueError("除数含未知数或为 0")
        return _Poly(self.a / o.b, self.b / o.b)


def _normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).replace("−", "-").replace("＝", "=")
    s = s.replace("\\times", "×").replace("\\div", "÷").replace("\\cdot", "×").replace("$", "")
    s = re.sub(r"(?<=\d)\s*([A-Za-z(])", r"*\1", s)  # 4x → 4*x；2(3+1) → 2*(3+1)
    s = re.sub(r"\)\s*(?=[\dA-Za-z(])", ")*", s)
    return s


def _parse(s: str) -> tuple[list[str], str | None]:
    pos, toks, var = 0, [], None
    while pos < len(s):
        m = _TOKEN.match(s, pos)
        if not m:
            raise ValueError("无法解析的字符")
        t = m.group(1)
        if t.isalpha():
            if var not in (None, t):
                raise ValueError("含多个未知数")
            var = t
        toks.append(t)
        pos = m.end()
    return toks, var


class _Parser:
    def __init__(self, toks: list[str]) -> None:
        self.t, self.i = toks, 0

    def peek(self) -> str | None:
        return self.t[self.i] if self.i < len(self.t) else None

    def eat(self) -> str:
        v = self.t[self.i]
        self.i += 1
        return v

    def expr(self) -> _Poly:
        v = self.term()
        while self.peek() in ("+", "-"):
            op = self.eat()
            r = self.term()
            v = v + r if op == "+" else v - r
        return v

    def term(self) -> _Poly:
        v = self.factor()
        while self.peek() in ("*", "×", "/", "÷"):
            op = self.eat()
            r = self.factor()
            v = v * r if op in ("*", "×") else v / r
        return v

    def factor(self) -> _Poly:
        t = self.peek()
        if t is None:
            raise ValueError("表达式不完整")
        if t == "-":
            self.eat()
            return _Poly() - self.factor()
        if t == "(":
            self.eat()
            v = self.expr()
            if self.peek() != ")":
                raise ValueError("括号不配对")
            self.eat()
            return v
        self.eat()
        if t.isalpha():
            return _Poly(Fraction(1), Fraction(0))
        return _Poly(Fraction(0), Fraction(t))


def _eval_side(toks: list[str]) -> _Poly:
    p = _Parser(toks)
    v = p.expr()
    if p.peek() is not None:
        raise ValueError("多余的符号")
    return v


def evaluate(text: str) -> Fraction | None:
    """算式或方程 → 精确值（算式的值 / 方程中未知数的解）；不是算式或方程返回 None。

    `a=b` 形式：含未知数则解方程；不含未知数（如 `5×4=20`）取左边的值（两边不相等也取左边，等号是否成立由别处核对）。"""
    s = _normalize(text)
    if not re.search(r"\d", s) or not re.search(r"[+\-*/×÷()=]|[A-Za-z]", s):
        return None
    try:
        toks, var = _parse(s)
        if toks.count("=") > 1:
            return None
        if "=" in toks:
            k = toks.index("=")
            left, right = _eval_side(toks[:k]), _eval_side(toks[k + 1 :])
            diff = left - right
            if var is not None:
                if diff.a == 0:
                    return None
                return -diff.b / diff.a
            return left.b
        v = _eval_side(toks)
        if v.a != 0:
            return None  # 含未知数的式子（不是方程）没有数值
        return v.b
    except (ValueError, ZeroDivisionError):
        return None

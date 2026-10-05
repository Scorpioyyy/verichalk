"""题目内容的规范化与结构检查（D27）。

内容统一为 Pandoc Markdown + TeX 数学。模型常见的写法会让后续排版出错（S1 探针发现：`$3.6-1.25 = $` 的内侧空格会让 pandoc
把整段当成普通文本；填空线写进公式里会被渲染成下标）。这里在内容进入 IR 的入口一次性解决：
①修剪公式内侧空白；②把填空位置移出公式；③公式里的 × ÷ 等符号转成 TeX 命令；④做轻量的语法检查。
"""

from __future__ import annotations

import re

_MATH = re.compile(r"(?<!\\)\$(?!\$)(.+?)(?<!\\)\$", re.S)
_BLANK_IN_MATH = re.compile(r"(?:\s*(?:\\_|_){2,}\s*|\s*[（(]\s*[)）]\s*)+$")
_SYMBOLS = {
    "×": r"\times ",
    "÷": r"\div ",
    "≠": r"\neq ",
    "≤": r"\leq ",
    "≥": r"\geq ",
    "≈": r"\approx ",
    "°": r"^\circ ",
    "·": r"\cdot ",
    "π": r"\pi ",
    "−": "-",
}
_ALLOWED_CMDS = {
    "frac", "dfrac", "times", "div", "cdot", "pm", "leq", "geq", "neq", "le", "ge", "ne", "approx", "circ",
    "angle", "triangle", "parallel", "perp", "sqrt", "text", "mathrm", "overline", "underline", "left",
    "right", "quad", "qquad", "ldots", "cdots", "dots", "pi", "%", "mathbf", "square", "because",
    "therefore", "rightarrow", "Rightarrow", "to", "sim", "cong", "mid", "equiv", "infty", "bigcirc", "sum",
    "lt", "gt", "lbrace", "rbrace", "cdotp", "bullet", "prime", "degree", "space", "dot", "hat", "bar",
}  # fmt: skip
_SELF_CORRECTION = re.compile(
    r"等等|重新(?:计算|算|检查|审题)|让我(?:再|重新)|不对[，,。]|更正|再检查|算错了|应该是.{0,6}才对|\[?草稿|scratch"
)
_FIGURE_DEP = re.compile(
    r"如图|下图|上图|右图|左图|图中|看图|观察图|见图|如下图|统计图中|示意图|以下(?:图|表格)中的"
)
_PLACEHOLDER = re.compile(r"\{\{|\}\}|TODO|待补充|××|□□□|…{3,}")
_ANSWER_HINT_IN_STEM = re.compile(r"(?:答案|解析|解答)[:：]")


def _fix_math(body: str) -> tuple[str, str]:
    """返回（修正后的公式内容, 被移出公式的填空位置）。"""
    m = _BLANK_IN_MATH.search(body)
    tail = ""
    if m:
        body, tail = body[: m.start()], "____"
    body = body.strip()
    for k, v in _SYMBOLS.items():
        body = body.replace(k, v)
    body = re.sub(r"\s{2,}", " ", body).strip()
    return body, tail


_CTRL = {"\t": "\\t", "\f": "\\f", "\b": "\\b", "\a": "\\a", "\v": "\\v", "\r": "\\r"}


def fix_tex_escapes(text: str) -> str:
    """模型在 JSON 字符串里写 `\times` 却没有双写反斜杠时，解析会得到制表符 + `imes`（`\frac` → 换页符 + `rac`）。
    题目内容里不会有这些控制字符，把它们还原成反斜杠命令的首字母即可。"""
    return "".join(_CTRL.get(ch, ch) for ch in text)


_LITERAL_NEWLINE = re.compile(r"\\n(?![A-Za-z])")


def fix_literal_newlines(text: str) -> str:
    """模型把换行写成了字面的反斜杠 + n（JSON 里双写了反斜杠）：还原成真换行。
    后面紧跟 ASCII 字母的（`\neq`、`\notin` 等 TeX 命令）不动。"""
    return _LITERAL_NEWLINE.sub("\n", text)


def normalize_text(text: str) -> str:
    """修剪公式内侧空白、把填空位置移到公式外、符号转 TeX。幂等。"""
    text = fix_literal_newlines(fix_tex_escapes(text.replace("\r\n", "\n"))).strip()

    def sub(m: re.Match[str]) -> str:
        body, tail = _fix_math(m.group(1))
        if not body:
            return tail or ""
        return f"${body}${tail}"

    out = _MATH.sub(sub, text)
    out = re.sub(r"[ \t]+\n", "\n", out)
    return out.strip()


def math_segments(text: str) -> list[str]:
    return [m.group(1) for m in _MATH.finditer(text)]


def check_math(text: str) -> list[str]:
    """轻量的 TeX 语法检查：美元符配对、花括号配对、`\\frac` 参数、未知命令。"""
    issues: list[str] = []
    stripped = text.replace(r"\$", "")
    if stripped.count("$") % 2 != 0:
        issues.append("公式的美元符号没有配对")
    for seg in math_segments(text):
        depth = 0
        for ch in seg:
            depth += (ch == "{") - (ch == "}")
            if depth < 0:
                break
        if depth != 0:
            issues.append(f"公式花括号不配对：${seg[:30]}$")
            continue
        for cmd in re.findall(r"\\([A-Za-z]+|%)", seg):
            if cmd not in _ALLOWED_CMDS:
                issues.append(f"公式含未知命令 \\{cmd}")
        for m in re.finditer(r"\\d?frac", seg):
            rest = seg[m.end() :]
            if not re.match(r"\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}\s*\{", rest):
                issues.append("\\frac 需要两个花括号参数")
                break
    return issues


def structure_issues(
    *, kind: str, stem: str, options: list[str], answer_values: list[str], solution: str
) -> list[str]:
    """题目结构检查（确定性）。返回问题列表，空表示通过。"""
    issues: list[str] = []
    if not stem.strip():
        issues.append("题面为空")
    if len(stem) > 600:
        issues.append("题面过长")
    if not answer_values or any(not str(v).strip() for v in answer_values):
        issues.append("答案为空")
    if kind == "choice":
        if len(options) < 3:
            issues.append("选择题需要至少 3 个选项")
        elif answer_values and re.fullmatch(r"[A-Da-d]", answer_values[0].strip()):
            idx = ord(answer_values[0].strip().upper()) - 65
            if idx >= len(options):
                issues.append("选择题的答案字母不在选项范围内")
        elif answer_values:
            issues.append("选择题的答案必须是选项字母")
    elif options:
        issues.append("非选择题不应有选项")
    if kind == "fill" and "____" not in stem:
        issues.append("填空题的题面里没有填空位置（____）")
    if kind == "judge" and answer_values and parse_judge(answer_values[0]) is None:
        issues.append("判断题的答案必须是“对”或“错”")
    blob = "\n".join([stem, *options])
    if _FIGURE_DEP.search(blob):
        issues.append("题面依赖图形（本版本不出依赖图形的题）")
    if _PLACEHOLDER.search(blob + solution):
        issues.append("含占位符或未完成的内容")
    if _ANSWER_HINT_IN_STEM.search(stem):
        issues.append("题面里泄漏了答案或解析")
    if solution and _SELF_CORRECTION.search(solution):
        issues.append("解析里含自我修正的痕迹（应为定稿）")
    blob_all = "\n".join([stem, *options, solution])
    if "$$" in blob_all:
        issues.append("不要使用 $$ 公式块，算式写成行内 $…$")
    if re.search(r"\\text\{[^}]*_{2,}|\\underline", blob_all):
        issues.append("填空线必须写在公式外面（____），不能放进公式或 \\text")
    if re.search(r"[（(]\s*[3-9一二三四五]\s*[)）]", stem):
        issues.append("一道题只能问一个或两个问题，不要用（1）（2）（3）编号")
    if len(answer_values) > 3:
        issues.append("答案超过 3 项，请拆成更小的题")
    issues += check_math(blob_all)
    return issues


def parse_judge(v: str) -> str | None:
    s = v.strip().lower()
    if s in {"对", "正确", "√", "✓", "是", "true"}:
        return "对"
    if s in {"错", "错误", "×", "✗", "否", "false"}:
        return "错"
    return None

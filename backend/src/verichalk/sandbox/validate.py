"""求解程序的静态检查（AST 白名单）。

模型生成的求解程序只允许做精确数值计算：`Fraction` / `Decimal` / 整数与 `math` 的少量函数。
任何 import（白名单外）、文件 / 网络 / 反射、dunder 属性、浮点字面量、`float()` 都被拒绝——
浮点会让答案比较不可靠（CLAUDE.md §2.8），因此在源码层面直接禁止。
"""

from __future__ import annotations

import ast

from ..core.errors import SandboxViolation

ALLOWED_IMPORTS = {
    "fractions": {"Fraction"},
    "decimal": {"Decimal", "ROUND_HALF_UP", "ROUND_DOWN", "ROUND_UP", "getcontext"},
    "math": {"gcd", "lcm", "isqrt", "floor", "ceil", "factorial", "comb", "perm"},
}
ALLOWED_NAMES = {
    # 构造与内建
    "Fraction",
    "Decimal",
    "ROUND_HALF_UP",
    "ROUND_DOWN",
    "ROUND_UP",
    "getcontext",
    "math",
    "int",
    "str",
    "bool",
    "len",
    "range",
    "sum",
    "min",
    "max",
    "abs",
    "round",
    "divmod",
    "pow",
    "sorted",
    "list",
    "tuple",
    "dict",
    "set",
    "enumerate",
    "zip",
    "reversed",
    "all",
    "any",
    "map",
    "filter",
    "True",
    "False",
    "None",
    "isinstance",
    "print",
}
ALLOWED_ATTRS = {
    # Fraction / Decimal / int / list / str / dict 的安全方法与属性
    "numerator",
    "denominator",
    "quantize",
    "limit_denominator",
    "is_integer",
    "as_integer_ratio",
    "append",
    "extend",
    "sort",
    "index",
    "count",
    "join",
    "split",
    "strip",
    "format",
    "keys",
    "values",
    "items",
    "get",
    "gcd",
    "lcm",
    "isqrt",
    "floor",
    "ceil",
    "factorial",
    "comb",
    "perm",
    "copy",
    "reverse",
    "insert",
    "pop",
    "upper",
    "lower",
    "replace",
    "startswith",
    "endswith",
    "normalize",
    "to_integral_value",
}
ALLOWED_NODES = (
    ast.Module,
    ast.FunctionDef,
    ast.arguments,
    ast.arg,
    ast.Return,
    ast.Assign,
    ast.AugAssign,
    ast.AnnAssign,
    ast.For,
    ast.While,
    ast.If,
    ast.Expr,
    ast.Pass,
    ast.Break,
    ast.Continue,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.Call,
    ast.Attribute,
    ast.Subscript,
    ast.Slice,
    ast.Tuple,
    ast.List,
    ast.Dict,
    ast.Set,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
    ast.comprehension,
    ast.IfExp,
    ast.Name,
    ast.Constant,
    ast.Load,
    ast.Store,
    ast.Del,
    ast.keyword,
    ast.JoinedStr,
    ast.FormattedValue,
    ast.Starred,
    ast.Import,
    ast.ImportFrom,
    ast.alias,
    ast.Assert,
    ast.operator,
    ast.unaryop,
    ast.boolop,
    ast.cmpop,
)


def validate_source(source: str) -> ast.Module:
    """解析并检查源码；违反白名单抛 `SandboxViolation`（消息指出位置与原因，便于反馈给模型修复）。"""
    if len(source) > 20_000:
        raise SandboxViolation("求解程序过长")
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise SandboxViolation(f"语法错误：{e.msg}（第 {e.lineno} 行）") from e

    defined: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            defined.add(node.name)
            for a in node.args.args + node.args.kwonlyargs:
                defined.add(a.arg)
            if node.args.vararg:
                defined.add(node.args.vararg.arg)
            if node.args.kwarg:
                defined.add(node.args.kwarg.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            defined.add(node.id)
        elif isinstance(node, ast.arg):
            defined.add(node.arg)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            for a in node.names:  # import 绑定的名称（白名单校验在下面的循环里）
                defined.add((a.asname or a.name).split(".")[0])

    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            raise SandboxViolation(
                f"不允许的语法：{type(node).__name__}（第 {getattr(node, 'lineno', '?')} 行）"
            )
        line = getattr(node, "lineno", "?")
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name not in ALLOWED_IMPORTS:
                    raise SandboxViolation(f"不允许 import {a.name}（第 {line} 行）")
        elif isinstance(node, ast.ImportFrom):
            allowed = ALLOWED_IMPORTS.get(node.module or "")
            if allowed is None or node.level:
                raise SandboxViolation(f"不允许 from {node.module} import（第 {line} 行）")
            for a in node.names:
                if a.name not in allowed:
                    raise SandboxViolation(f"不允许 from {node.module} import {a.name}（第 {line} 行）")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_") or node.attr not in ALLOWED_ATTRS:
                raise SandboxViolation(f"不允许的属性：.{node.attr}（第 {line} 行）")
        elif isinstance(node, ast.Name):
            if node.id.startswith("__"):
                raise SandboxViolation(f"不允许的名称：{node.id}（第 {line} 行）")
            if isinstance(node.ctx, ast.Load) and node.id not in ALLOWED_NAMES and node.id not in defined:
                raise SandboxViolation(f"未定义或不允许的名称：{node.id}（第 {line} 行）")
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, float):
                raise SandboxViolation(
                    f"不允许浮点字面量 {node.value!r}，请用 Decimal('…') 或 Fraction（第 {line} 行）"
                )
            if isinstance(node.value, bytes | complex):
                raise SandboxViolation(f"不允许的常量类型（第 {line} 行）")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in (
                "float",
                "complex",
                "eval",
                "exec",
                "open",
                "compile",
                "getattr",
                "setattr",
                "delattr",
                "globals",
                "locals",
                "vars",
                "input",
                "__import__",
            ):
                raise SandboxViolation(f"不允许调用 {node.func.id}（第 {line} 行）")
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            # 指数过大会拖垮解释器；静态可判的常量指数限制在 0..64
            if (
                isinstance(node.right, ast.Constant)
                and isinstance(node.right.value, int)
                and not (0 <= node.right.value <= 64)
            ):
                raise SandboxViolation(f"指数过大（第 {line} 行）")
    return tree

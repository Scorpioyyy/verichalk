"""子进程里运行的执行器（由 `runner.py` 以 `python -I -S`-风格启动，stdin 传入 JSON，stdout 回传 JSON）。

本文件不得导入 verichalk 的其他模块：它在隔离的子进程里独立运行，且只暴露受限的内建函数。
"""

from __future__ import annotations

import builtins
import json
import sys
from decimal import Decimal
from fractions import Fraction

_SAFE_BUILTINS = {
    n: getattr(builtins, n)
    for n in (
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
        "isinstance",
        "print",
        "True",
        "False",
        "None",
        "Exception",
        "ValueError",
        "ZeroDivisionError",
        "ArithmeticError",
    )
}
_ALLOWED_MODULES = {"fractions", "decimal", "math"}


def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name not in _ALLOWED_MODULES or level:
        raise ImportError(f"import {name} 被禁止")
    return __import__(name, globals, locals, fromlist, level)


def encode(v):
    """把结果编码为带类型标签的 JSON，保证 Fraction / Decimal 精确往返。"""
    if isinstance(v, bool):
        return {"t": "bool", "v": v}
    if isinstance(v, int):
        return {"t": "int", "v": str(v)}
    if isinstance(v, Fraction):
        return {"t": "frac", "n": str(v.numerator), "d": str(v.denominator)}
    if isinstance(v, Decimal):
        return {"t": "dec", "v": format(v, "f")}
    if isinstance(v, str):
        return {"t": "str", "v": v}
    if v is None:
        return {"t": "none"}
    if isinstance(v, (list, tuple)):
        return {"t": "list", "v": [encode(x) for x in v]}
    if isinstance(v, dict):
        return {"t": "dict", "v": [[encode(k), encode(x)] for k, x in v.items()]}
    if isinstance(v, (set, frozenset)):
        return {"t": "list", "v": [encode(x) for x in sorted(v, key=repr)]}
    raise TypeError(
        f"不支持的结果类型：{type(v).__name__}（求解程序必须返回精确数值、字符串、布尔或它们的容器）"
    )


def main() -> None:
    req = json.loads(sys.stdin.read())
    code, params, entry = req["code"], req.get("params") or {}, req.get("entry", "solve")
    import math

    env = {
        "__builtins__": {**_SAFE_BUILTINS, "__import__": _safe_import},
        "Fraction": Fraction,
        "Decimal": Decimal,
        "math": math,
    }
    out: dict
    try:
        exec(compile(code, "<solver>", "exec"), env)
        fn = env.get(entry)
        if not callable(fn):
            raise NameError(f"求解程序必须定义函数 {entry}()")
        # 参数以字符串传入，按需转换为 int / Fraction / Decimal 由调用方在 params 里用 {"t":…} 标注
        kwargs = {k: _decode(v) for k, v in params.items()}
        value = fn(**kwargs)
        out = {"ok": True, "value": encode(value)}
    except BaseException as e:
        out = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
    sys.stdout.write("\n@@RESULT@@" + json.dumps(out, ensure_ascii=False))
    sys.stdout.flush()


def _decode(v):
    if isinstance(v, dict) and "t" in v:
        t = v["t"]
        if t == "int":
            return int(v["v"])
        if t == "frac":
            return Fraction(int(v["n"]), int(v["d"]))
        if t == "dec":
            return Decimal(v["v"])
        if t == "str":
            return v["v"]
        if t == "bool":
            return bool(v["v"])
        if t == "list":
            return [_decode(x) for x in v["v"]]
    return v


if __name__ == "__main__":
    main()

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

import pytest

from verichalk.core.errors import SandboxError, SandboxTimeout, SandboxViolation
from verichalk.sandbox import run_solver, validate_source

GOOD = [
    ("def solve():\n    return Fraction(1, 3) + Fraction(1, 6)\n", Fraction(1, 2)),
    ("def solve():\n    return Decimal('3.6') - Decimal('1.25')\n", Decimal("2.35")),
    ("from fractions import Fraction\nfrom math import gcd\ndef solve():\n    return gcd(12, 18)\n", 6),
    ("def solve():\n    return [x * x for x in range(4)]\n", [0, 1, 4, 9]),
    ("def solve():\n    a, b = 7, 2\n    return divmod(a, b)\n", [3, 1]),
    ("def solve():\n    return True\n", True),
    ("def solve():\n    print('步骤')\n    return 'ok'\n", "ok"),
]


@pytest.mark.parametrize("code,expected", GOOD)
async def test_good_programs_exact(code, expected):
    r = await run_solver(code)
    assert r.value == expected and type(r.value) is type(expected)


async def test_params_roundtrip_exact():
    code = "def solve(a, b):\n    return a + b\n"
    r = await run_solver(code, {"a": {"t": "dec", "v": "0.1"}, "b": {"t": "dec", "v": "0.2"}})
    assert r.value == Decimal("0.3")  # 浮点会得到 0.30000000000000004
    r = await run_solver(code, {"a": {"t": "frac", "n": "1", "d": "3"}, "b": {"t": "int", "v": "1"}})
    assert r.value == Fraction(4, 3)


BAD = [
    "import os\ndef solve():\n    return 1\n",
    "from os import path\ndef solve():\n    return 1\n",
    "import subprocess\n",
    "def solve():\n    return open('x').read()\n",
    "def solve():\n    return eval('1+1')\n",
    "def solve():\n    return __import__('os')\n",
    "def solve():\n    return ().__class__.__bases__\n",
    "def solve():\n    return (1).__class__\n",
    "def solve():\n    return 0.5 + 1\n",  # 浮点字面量
    "def solve():\n    return float(1) / 3\n",  # float()
    "def solve():\n    return getattr(1, 'real')\n",
    "def solve():\n    x = lambda: 1\n    return x()\n",  # lambda 不在白名单
    "class A:\n    pass\ndef solve():\n    return 1\n",
    "def solve():\n    return globals()\n",
    "def solve():\n    return 2 ** 100000\n",  # 常量大指数
    "def solve():\n    return undefined_name\n",
    "async def solve():\n    return 1\n",
    "def solve():\n    with open('f') as f:\n        return 1\n",
    "def solve():\n    return sys.exit(0)\n",
    "def solve():\n    return math.sqrt(2)\n",  # math.sqrt 不在白名单（返回浮点）
    "def solve(:\n",
    "x = " + "1 + " * 5000 + "1\ndef solve():\n    return x\n",
]


@pytest.mark.parametrize("code", BAD)
async def test_adversarial_programs_rejected(code):
    with pytest.raises(SandboxViolation):
        validate_source(code)
    with pytest.raises(SandboxViolation):
        await run_solver(code)


async def test_infinite_loop_times_out():
    with pytest.raises(SandboxTimeout):
        await run_solver("def solve():\n    while True:\n        pass\n", timeout_s=1.0)


async def test_runtime_errors_are_typed():
    with pytest.raises(SandboxError, match="ZeroDivisionError"):
        await run_solver("def solve():\n    return 1 // 0\n")
    with pytest.raises(SandboxError, match="solve"):
        await run_solver("def other():\n    return 1\n")
    with pytest.raises(SandboxError, match="不支持的结果类型"):
        await run_solver("def solve():\n    return object\n") if False else await run_solver(
            "def solve():\n    return range(3)\n"
        )


async def test_child_does_not_inherit_secrets(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "abcd1234efgh5678")
    # 沙箱里没有 os / 环境可访问；这里验证即使源码尝试也过不了校验
    with pytest.raises(SandboxViolation):
        await run_solver("import os\ndef solve():\n    return os.environ.get('DASHSCOPE_API_KEY')\n")


async def test_decimal_context_precision_is_allowed() -> None:
    code = "from decimal import Decimal, getcontext\ndef solve():\n    getcontext().prec = 30\n    return [Decimal('1') / Decimal('3')]\n"
    r = await run_solver(code)
    assert len(str(r.value[0])) > 20

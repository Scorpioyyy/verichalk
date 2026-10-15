from __future__ import annotations

import subprocess
from decimal import Decimal
from fractions import Fraction
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from verichalk.core.errors import SandboxError, SandboxTimeout, SandboxViolation
from verichalk.sandbox import run_solver, validate_source


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_resource_limits_match_platform(monkeypatch: pytest.MonkeyPatch, platform: str):
    """Mac 跳过不兼容的地址空间限制，Linux 仍保留内存限制，两者均限制 CPU。"""
    import sys

    from verichalk.sandbox import runner

    resource = SimpleNamespace(RLIMIT_AS=1, RLIMIT_CPU=2, setrlimit=Mock())
    monkeypatch.setitem(sys.modules, "resource", resource)
    monkeypatch.setattr(runner, "sys", SimpleNamespace(platform=platform))
    runner._preexec()
    calls = [(args[0], args[1]) for args, _ in resource.setrlimit.call_args_list]
    expected = [(2, (10, 10))]
    if platform == "linux":
        expected.insert(0, (1, (512 * 2**20, 512 * 2**20)))
    assert calls == expected


@pytest.mark.parametrize("error", [OSError("cannot start"), subprocess.SubprocessError("preexec failed")])
async def test_process_start_failure_is_typed(monkeypatch: pytest.MonkeyPatch, error: Exception):
    """初始化失败归入沙箱错误，由核验流程处理，不泄漏原始内部异常。"""
    from verichalk.sandbox import runner

    monkeypatch.setattr(runner.subprocess, "run", Mock(side_effect=error))
    with pytest.raises(SandboxError, match="求解子进程启动失败"):
        await run_solver("def solve():\n    return 6 * 7\n")

    from verichalk.domain.paper import CheckStatus
    from verichalk.verify.checks import VerifyInput, check_program

    result = await check_program(
        VerifyInput(kind="calc", stem="6 × 7", solver_code="def solve():\n    return 6 * 7\n")
    )
    assert result.status == CheckStatus.fail
    assert "求解子进程启动失败" in result.detail


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


def test_solver_runs_on_a_selector_event_loop():
    """Windows 上 `uvicorn --reload` 用的是不支持 asyncio 子进程的选择器事件循环；沙箱不能依赖事件循环的类型。"""
    import asyncio

    from verichalk.sandbox import run_solver

    loop = asyncio.SelectorEventLoop()
    try:
        res = loop.run_until_complete(run_solver("def solve():\n    return 6*7\n"))
    finally:
        loop.close()
    assert res.value == 42

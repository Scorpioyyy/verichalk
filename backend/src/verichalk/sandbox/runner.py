"""受限执行（D21）：AST 白名单 + 独立子进程 + 墙钟超时 + 内存上限（POSIX）。

这是进程级隔离加源码过滤，不是操作系统级沙箱；容器本身是第二道隔离。
只有模型输出的求解程序会被执行——用户文本与图片永远不会被当作代码。
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

from .. import trace
from ..core.errors import SandboxError, SandboxTimeout
from .validate import validate_source

_HARNESS = Path(__file__).with_name("harness.py")
_MARK = "@@RESULT@@"
MAX_OUTPUT = 100_000


@dataclass
class SandboxResult:
    value: Any
    stdout: str
    duration_ms: float


def decode(v: dict) -> Any:
    t = v["t"]
    if t == "int":
        return int(v["v"])
    if t == "frac":
        f = Fraction(int(v["n"]), int(v["d"]))
        return f
    if t == "dec":
        return Decimal(v["v"])
    if t in ("str", "bool"):
        return v["v"]
    if t == "none":
        return None
    if t == "list":
        return [decode(x) for x in v["v"]]
    if t == "dict":
        return {_hashable(decode(k)): decode(x) for k, x in v["v"]}
    raise SandboxError(f"未知结果类型标签：{t}")


def _hashable(x: Any) -> Any:
    return tuple(x) if isinstance(x, list) else x


def _preexec() -> None:  # pragma: no cover  仅 POSIX
    if sys.platform == "win32":
        return
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (512 * 2**20, 512 * 2**20))
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))


async def run_solver(
    code: str, params: dict[str, Any] | None = None, *, entry: str = "solve", timeout_s: float = 5.0
) -> SandboxResult:
    """校验并执行 `entry(**params)`，返回精确结果。

    `params` 的值需已编码为 `{"t": "int|frac|dec|str|bool|list", …}` 或普通 JSON 值。
    违反白名单抛 `SandboxViolation`；超时抛 `SandboxTimeout`；运行出错抛 `SandboxError`。
    """
    validate_source(code)
    payload = json.dumps({"code": code, "params": params or {}, "entry": entry}, ensure_ascii=False)
    kwargs: dict[str, Any] = {}
    if os.name == "posix":
        kwargs["preexec_fn"] = _preexec
    elif sys.platform == "win32":
        # 父进程没有控制台时（服务、被 Playwright 拉起的服务），每个子进程都会申请新控制台，累积后启动失败
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    env = {
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
    }  # 不继承任何密钥类环境变量
    cmd = [sys.executable, "-X", "utf8", "-I", str(_HARNESS)]

    def _run() -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            cmd,
            input=payload.encode("utf-8"),
            capture_output=True,
            timeout=timeout_s,  # 超时由 subprocess 杀掉子进程
            env=env,
            **kwargs,
        )

    async with trace.tool_span("sandbox.run_solver", entry=entry):
        t0 = time.perf_counter()
        # 在线程里同步运行子进程：不依赖事件循环的类型（Windows 上 uvicorn --reload 用的事件循环不支持 asyncio 子进程）
        try:
            proc = await asyncio.to_thread(_run)
        except subprocess.TimeoutExpired:
            raise SandboxTimeout(f"求解程序超过 {timeout_s}s") from None
        out, err = proc.stdout, proc.stderr
        dur = (time.perf_counter() - t0) * 1000
    text = out.decode("utf-8", "replace")[:MAX_OUTPUT]
    if _MARK not in text:
        raise SandboxError(
            f"求解程序异常退出（退出码 {proc.returncode}）：{err.decode('utf-8', 'replace')[:200]}"
        )
    stdout, _, tail = text.partition(_MARK)
    res = json.loads(tail)
    if not res["ok"]:
        raise SandboxError(res["error"])
    return SandboxResult(value=decode(res["value"]), stdout=stdout.strip(), duration_ms=dur)

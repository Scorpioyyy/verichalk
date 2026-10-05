"""开发辅助命令（跨平台）：

python scripts/dev.py check     # ruff 检查 + 格式检查 + pyright + pytest（L1）
python scripts/dev.py fmt       # 自动格式化与修复
python scripts/dev.py run       # 本地启动后端（http://127.0.0.1:8000），带热重载
python scripts/dev.py eval      # 回放评测：smoke 集（val）
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
PY = sys.executable


def sh(*args: str, cwd: Path = BACKEND) -> int:
    print("$", " ".join(args), flush=True)
    return subprocess.call(list(args), cwd=cwd)


def check() -> int:
    steps = [
        [PY, "-m", "ruff", "check", "."],
        [PY, "-m", "ruff", "format", "--check", "."],
        [PY, "-m", "pyright", "--pythonpath", PY],
        [PY, "-m", "pytest", "-q"],
    ]
    failed = [" ".join(s[2:4]) for s in steps if sh(*s) != 0]
    print("\n全部通过" if not failed else f"\n失败：{failed}")
    return 1 if failed else 0


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "check":
        return check()
    if cmd == "fmt":
        return sh(PY, "-m", "ruff", "check", ".", "--fix") or sh(
            PY, "-m", "ruff", "format", "."
        )
    if cmd == "run":
        return sh(PY, "-m", "verichalk", "serve", "--reload")
    if cmd == "eval":
        return sh(
            PY,
            "-m",
            "verichalk.eval",
            "run",
            "--suite",
            "smoke",
            "--split",
            "val",
            "--mode",
            "replay",
        )
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())

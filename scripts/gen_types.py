"""由后端 OpenAPI 生成前端的 TypeScript 类型（D13：契约优先，类型不手写）。

python scripts/gen_types.py           # 生成 frontend/src/shared/api/schema.gen.ts
python scripts/gen_types.py --check   # 只检查已提交的文件是否与后端一致（有漂移则退出码 1）

事件 schema 与所有请求 / 响应模型都在 OpenAPI 里（事件经调试接口的响应类型导出）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
TARGET = FRONTEND / "src" / "shared" / "api" / "schema.gen.ts"


def build_openapi() -> dict:
    from verichalk.api import create_app
    from verichalk.core.config import Settings

    return create_app(Settings()).openapi()


def generate(out: Path) -> None:
    with tempfile.TemporaryDirectory() as td:
        spec = Path(td) / "openapi.json"
        spec.write_text(json.dumps(build_openapi(), ensure_ascii=False), encoding="utf-8")
        npx = shutil.which("npx.cmd") or shutil.which("npx") or "npx"
        subprocess.run(
            [npx, "openapi-typescript", str(spec), "-o", str(out)],
            cwd=FRONTEND,
            check=True,
            stdout=subprocess.DEVNULL,
        )
    # 统一换行，避免不同平台产生无意义的差异
    out.write_text(out.read_text(encoding="utf-8").replace("\r\n", "\n"), encoding="utf-8", newline="\n")


def main() -> int:
    if "--check" in sys.argv:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / "schema.gen.ts"
            generate(tmp)
            same = TARGET.exists() and TARGET.read_text(encoding="utf-8") == tmp.read_text(encoding="utf-8")
        print("前端类型与后端一致" if same else "前端类型已漂移：运行 python scripts/gen_types.py 并提交")
        return 0 if same else 1
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    generate(TARGET)
    print(f"已生成 {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

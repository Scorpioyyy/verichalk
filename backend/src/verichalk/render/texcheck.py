"""LaTeX 源码的实际编译检查（X9）：只用于评测与人工验收，服务端导出只生成源码、不编译（D8）。"""

from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path


def compile_latex(data: bytes, *, is_zip: bool) -> str:
    """本机有 xelatex 时编译一遍；返回错误摘要，空串表示成功或无法检测。"""
    exe = shutil.which("xelatex")
    if not exe:
        return ""
    with tempfile.TemporaryDirectory(prefix="vc_tex_") as td:
        d = Path(td)
        if is_zip:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                z.extractall(d)
        else:
            (d / "paper.tex").write_bytes(data)
        try:
            p = subprocess.run(
                [exe, "-interaction=nonstopmode", "-halt-on-error", "paper.tex"],
                cwd=d,
                capture_output=True,
                timeout=180,
            )
        except subprocess.TimeoutExpired:
            return "xelatex 编译超时"
        if not (d / "paper.pdf").exists():  # MiKTeX 的"未检查更新"提示会让返回码非零，以是否产出 PDF 为准
            tail = (p.stdout or b"").decode("utf-8", "replace").strip().splitlines()[-6:]
            return "xelatex 编译失败：" + " | ".join(tail)[:300]
    return ""

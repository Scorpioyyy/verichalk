"""SVG → PNG（Word 与 LaTeX 不能直接嵌 SVG）。用 Typst 渲染，不再引入栅格化依赖。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import typst

from ..core.errors import ExportError


def svg_to_png(svg: str, *, ppi: float = 200.0) -> bytes:
    with tempfile.TemporaryDirectory(prefix="vc_png_") as td:
        root = Path(td)
        (root / "f.svg").write_text(svg, encoding="utf-8")
        (root / "main.typ").write_text(
            '#set page(width: auto, height: auto, margin: 2pt)\n#image("f.svg")\n', encoding="utf-8"
        )
        try:
            out = typst.compile(str(root / "main.typ"), root=str(root), format="png", ppi=ppi)
        except Exception as e:
            raise ExportError(f"图形栅格化失败：{str(e)[:200]}") from e
    return bytes(out) if not isinstance(out, list) else bytes(out[0])

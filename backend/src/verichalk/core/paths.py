"""仓库根目录的定位。"""

from __future__ import annotations

import os
from pathlib import Path


def find_root() -> Path:
    """优先 `VERICHALK_ROOT`；否则自本文件向上找含 `config/models.yaml` 的目录（开发态）。"""
    env = os.environ.get("VERICHALK_ROOT")
    if env:
        return Path(env).resolve()
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "config" / "models.yaml").exists():
            return p
    return Path.cwd().resolve()

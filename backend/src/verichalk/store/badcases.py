"""Badcase 簿：一条记录一个 YAML 文件（可读、可 diff、可直接提交），目录由设置决定（开发态是 `eval/badcases/`）。"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

import yaml

from ..core.errors import NotFound
from ..core.ids import new_id
from ..domain.badcase import Badcase, BadcaseIn

_SAFE_ID = re.compile(r"^bc_[0-9A-Z]+$")


class BadcaseBook:
    def __init__(self, directory: Path) -> None:
        self.dir = directory

    def _path(self, badcase_id: str) -> Path:
        if not _SAFE_ID.match(badcase_id):
            raise NotFound(f"Badcase 不存在：{badcase_id}")
        return self.dir / f"{badcase_id}.yaml"

    async def add(self, body: BadcaseIn) -> Badcase:
        bc = Badcase(id=new_id("bc"), created_at=time.time(), **body.model_dump())

        def _do() -> None:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._path(bc.id).write_text(
                yaml.safe_dump(bc.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )

        await asyncio.to_thread(_do)
        return bc

    async def list(self) -> list[Badcase]:
        def _do() -> list[Badcase]:
            if not self.dir.exists():
                return []
            out = [
                Badcase.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))
                for p in self.dir.glob("bc_*.yaml")
            ]
            return sorted(out, key=lambda b: b.created_at, reverse=True)

        return await asyncio.to_thread(_do)

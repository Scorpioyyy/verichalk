"""评测用例：YAML 文件 → `Case`。格式见 docs/evaluation.md §3.1。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from ..core.errors import ConfigError


class TurnSpec(BaseModel):
    user: str = ""
    images: list[str] = Field(default_factory=list)  # 相对 eval/datasets/photos/ 的路径（拍照集不入库）
    pipeline: str | None = None
    has_paper: bool = False  # 本轮开始前，会话里已有试卷（运行器会注入夹具试卷）
    clarify_answer: str | None = None  # 若本轮触发澄清检查点，用它作答；缺省回答"你来定"
    expect: dict[str, Any] = Field(
        default_factory=dict
    )  # 本轮的期望（意图理解：route / clarify / brief / user_fields / absent）


class Case(BaseModel):
    id: str
    split: str = "val"
    tags: list[str] = Field(default_factory=list)
    turns: list[TurnSpec]
    expect: dict[str, Any] = Field(default_factory=dict)


def load_cases(datasets_dir: Path, suite: str, split: str | None = None) -> list[Case]:
    """读取 `<datasets_dir>/cases/<suite>.yaml`（或目录 `<suite>/*.yaml`）；可按 split 过滤。"""
    base = datasets_dir / "cases"
    files = (
        [base / f"{suite}.yaml"]
        if (base / f"{suite}.yaml").exists()
        else sorted((base / suite).glob("*.yaml"))
    )
    if not files:
        raise ConfigError(f"找不到评测集：{suite}（{base}）")
    cases: list[Case] = []
    for f in files:
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or []
        cases += [Case.model_validate(d) for d in data]
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise ConfigError(f"评测集 {suite} 含重复的用例 id")
    return [c for c in cases if split in (None, "all", c.split)]

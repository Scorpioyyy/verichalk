"""仓库目录整洁（CLAUDE.md §6）：顶层只含声明的条目；提交时 tmp/ 为空。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

EXPECTED_TOP_LEVEL = {
    "README.md",
    "CLAUDE.md",
    "LICENSE",
    "Dockerfile",
    ".env.example",
    ".gitignore",
    ".gitattributes",
    ".github",
    ".dockerignore",
    ".editorconfig",
    "backend",
    "frontend",
    "config",
    "eval",
    "docs",
    "deploy",
    "scripts",
    "data",
    "tmp",
    ".git",
}
IGNORED = {".venv", "node_modules", ".pytest_cache", ".ruff_cache", ".claude", "__pycache__"}


def test_top_level_is_clean():
    actual = {p.name for p in ROOT.iterdir()} - IGNORED
    unexpected = actual - EXPECTED_TOP_LEVEL
    assert not unexpected, f"顶层出现未声明的条目，需归类或删除：{sorted(unexpected)}"


def test_tmp_is_empty():
    leftovers = (
        [p.name for p in (ROOT / "tmp").iterdir() if p.name != ".gitkeep"] if (ROOT / "tmp").exists() else []
    )
    assert not leftovers, f"tmp/ 必须为空（里程碑验收后清理）：{leftovers}"

"""架构不变量（architecture §2）：分层依赖、禁用导入、无环。违反即失败。"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "verichalk"

# 层号越大越靠上；只允许依赖同层或更低层（同层之间不得成环，见 test_no_cycles）
LAYERS = {
    "core": 0,
    "domain": 1,
    "llm": 2,
    "trace": 2,
    "store": 2,
    "sandbox": 2,
    "knowledge": 3,
    "verify": 3,
    "figures": 3,
    "render": 3,
    "perception": 3,
    "metrics": 3,
    "tools": 4,
    "stages": 5,
    "orchestrator": 6,
    "api": 7,
    "eval": 8,
}

# 外部库只能出现在指定的模块里（业务代码不得绕过网关 / 沙箱 / 知识层 / 存储）
RESTRICTED = {
    "httpx": {"llm/transport.py", "knowledge/embedding.py"},
    "requests": set(),
    "chalkbase": {"knowledge/service.py"},
    "sqlite3": {"store/db.py", "store/repos.py"},
    "subprocess": {
        "render/",
        "sandbox/runner.py",
        "eval/report.py",
    },  # 渲染引擎（pandoc / typst）在 render/ 内调用；求解程序在沙箱的子进程里执行；报告读取 git 版本
    "openai": set(),
}
# 特例：只有这些位置可以创建子进程
SUBPROCESS_CALLS = {"sandbox/runner.py", "render/", "eval/report.py"}


def modules():
    for p in sorted(SRC.rglob("*.py")):
        rel = p.relative_to(SRC).as_posix()
        yield p, rel


def imports_of(path: Path, rel: str) -> list[str]:
    """返回该文件导入的模块的绝对名（相对导入已解析）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    pkg_parts = ["verichalk", *rel.removesuffix(".py").split("/")]
    if pkg_parts[-1] != "__init__":
        pkg_parts = pkg_parts[:-1]
    else:
        pkg_parts = pkg_parts[:-1]
    out: list[str] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            if n.level:
                base = pkg_parts[: len(pkg_parts) - (n.level - 1)]
                mod = ".".join(base + ([n.module] if n.module else []))
            else:
                mod = n.module or ""
            if mod == "verichalk":  # `from .. import trace` 导入的是子包 verichalk.trace
                out += [f"verichalk.{a.name}" for a in n.names]
            elif mod:
                out.append(mod)
    return out


def layer_of(rel: str) -> int | None:
    top = rel.split("/")[0]
    return LAYERS.get(top)


def test_every_subpackage_has_a_layer():
    tops = {
        p.name for p in SRC.iterdir() if p.is_dir() and not p.name.startswith("__") and any(p.rglob("*.py"))
    }
    assert tops <= set(LAYERS), f"未登记层号的子包：{tops - set(LAYERS)}"


@pytest.mark.parametrize("path,rel", list(modules()), ids=lambda x: x if isinstance(x, str) else "")
def test_imports_only_same_or_lower_layers(path, rel):
    mine = layer_of(rel)
    if mine is None:
        return  # 顶层模块（__init__、__main__）
    for imp in imports_of(path, rel):
        parts = imp.split(".")
        if parts[0] != "verichalk" or len(parts) < 2:
            continue
        theirs = LAYERS.get(parts[1])
        assert theirs is not None or parts[1] in ("__version__",), f"{rel} 导入了未登记的子包 {imp}"
        if theirs is not None:
            assert theirs <= mine, f"{rel}（层 {mine}）不得导入更高层 {imp}（层 {theirs}）"


def test_no_cycles_between_subpackages():
    graph: dict[str, set[str]] = {k: set() for k in LAYERS}
    for path, rel in modules():
        a = rel.split("/")[0]
        if a not in LAYERS:
            continue
        for imp in imports_of(path, rel):
            parts = imp.split(".")
            if parts[0] == "verichalk" and len(parts) > 1 and parts[1] in LAYERS and parts[1] != a:
                graph[a].add(parts[1])
    visiting: list[str] = []
    done: set[str] = set()

    def dfs(n: str) -> None:
        if n in done:
            return
        assert n not in visiting, f"子包导入成环：{' → '.join([*visiting, n])}"
        visiting.append(n)
        for m in graph[n]:
            dfs(m)
        visiting.pop()
        done.add(n)

    for k in graph:
        dfs(k)


@pytest.mark.parametrize("path,rel", list(modules()), ids=lambda x: x if isinstance(x, str) else "")
def test_restricted_libraries_only_in_their_modules(path, rel):
    for imp in imports_of(path, rel):
        root = imp.split(".")[0]
        if root in RESTRICTED:
            allowed = RESTRICTED[root]
            assert any(rel == a or (a.endswith("/") and rel.startswith(a)) for a in allowed), (
                f"{rel} 不得直接导入 {root}（只允许：{sorted(allowed) or '无'}）"
            )


def test_stages_do_not_import_upward_layers_explicitly():
    """规则 2：stages 不得导入 api / orchestrator；api 只经 orchestrator 触达业务。"""
    for path, rel in modules():
        top = rel.split("/")[0]
        imps = {i.split(".")[1] for i in imports_of(path, rel) if i.startswith("verichalk.") and "." in i}
        if top == "stages":
            assert not imps & {"api", "orchestrator"}, rel
        if top == "api":
            assert not imps & {"stages"}, f"{rel} 应通过 orchestrator 而不是直接使用 stages"


def test_subprocess_creation_confined_to_sandbox_and_render():
    for path, rel in modules():
        text = path.read_text(encoding="utf-8")
        if "create_subprocess" in text or "Popen(" in text or "subprocess.run" in text:
            assert any(rel == a or (a.endswith("/") and rel.startswith(a)) for a in SUBPROCESS_CALLS), rel

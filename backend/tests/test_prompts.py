from __future__ import annotations

import pytest

from verichalk.core.errors import ConfigError
from verichalk.domain.llm import ChatMessage
from verichalk.llm import all_prompt_ids, get_prompt, parse_prompt

DOC = """---
id: demo.x
version: 3
role: fast
---
<!-- segment:static -->
你是助手。只输出 JSON。
<!-- segment:stable -->
年级：{{ grade }}
<!-- segment:dynamic -->
问题：{{ q }}
"""


def test_render_layout_and_prefix_hash():
    t = parse_prompt(DOC)
    a = t.render(stable={"grade": 4}, dynamic={"q": "A"})
    b = t.render(stable={"grade": 4}, dynamic={"q": "B"})
    assert [m.role for m in a.messages] == ["system", "user"]
    assert a.messages[0].content == "你是助手。只输出 JSON。\n\n年级：4"
    assert a.messages[1].content == "问题：A"
    # 动态内容变化不影响前缀哈希，但影响整体哈希
    assert a.ref.prefix_hash == b.ref.prefix_hash and a.ref.hash != b.ref.hash
    assert [s.name for s in a.ref.segments] == ["static", "stable", "dynamic"]


def test_history_goes_between_system_and_dynamic():
    t = parse_prompt(DOC)
    h = [ChatMessage(role="user", content="u1"), ChatMessage(role="assistant", content="a1")]
    out = t.render(stable={"grade": 4}, dynamic={"q": "Z"}, history=h)
    assert [m.role for m in out.messages] == ["system", "user", "assistant", "user"]
    assert out.messages[-1].content == "问题：Z"


def test_static_must_not_have_variables():
    bad = DOC.replace("你是助手。", "你是{{ name }}。")
    with pytest.raises(ConfigError):
        parse_prompt(bad)


def test_missing_variable_fails_loudly():
    import jinja2

    with pytest.raises(jinja2.UndefinedError):
        parse_prompt(DOC).render(stable={}, dynamic={"q": 1})


@pytest.mark.parametrize("bad", ["没有前置元数据", "---\nid: a\n---\n<!-- segment:static -->\nx"])
def test_malformed_prompts_rejected(bad):
    with pytest.raises(ConfigError):
        parse_prompt(bad)


def test_every_shipped_prompt_is_valid():
    """所有随包提示词都能加载，id 与路径一致，角色是已知角色。"""
    from verichalk.domain.llm import Role

    ids = all_prompt_ids()
    assert ids, "至少应有一份提示词"
    for pid in ids:
        t = get_prompt(pid)
        assert t.id == pid and t.role in {r.value for r in Role}

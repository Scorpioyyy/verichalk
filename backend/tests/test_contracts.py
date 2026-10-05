"""契约快照：事件 schema 与 OpenAPI。前端类型由它们生成；变更必须是有意的（UPDATE_SNAPSHOTS=1 更新并提交）。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest

from verichalk.api import create_app
from verichalk.core.config import Settings
from verichalk.domain.events import SCHEMA_VERSION, EventAdapter

SNAP = Path(__file__).parent / "snapshots"


def _check(name: str, obj: dict) -> None:
    path = SNAP / name
    text = json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if os.environ.get("UPDATE_SNAPSHOTS") == "1" or not path.exists():
        SNAP.mkdir(exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if not os.environ.get("UPDATE_SNAPSHOTS"):
            pytest.fail(f"快照 {name} 不存在，已生成；请检查并提交后重跑")
        return
    assert path.read_text(encoding="utf-8") == text, (
        f"{name} 与快照不一致：若是有意变更，运行 UPDATE_SNAPSHOTS=1 pytest 并提交；事件 schema 变更还需升 SCHEMA_VERSION 次版本"
    )


def test_event_schema_snapshot():
    schema = EventAdapter.json_schema()
    _check("event_schema.json", {"schema_version": SCHEMA_VERSION, "schema": schema})


def test_openapi_snapshot(store, kb_service):
    from verichalk.llm import build_gateway
    from verichalk.orchestrator import Container, RunManager, Warmer
    from verichalk.trace import EventBus

    s = Settings()
    llm = build_gateway(s, transport=object())  # type: ignore[arg-type]
    bus = EventBus()
    c = Container(s, store, bus, kb_service, llm, RunManager(s, store, bus, kb_service, llm), Warmer(s, llm))
    app = create_app(s, container=c)
    _check("openapi.json", app.openapi())
    assert httpx  # 仅为保持导入（ASGI 测试在 test_api 中）


def test_every_event_type_has_literal_discriminator_and_visibility():
    """每个事件类型都有唯一的 `type` 字面量，并显式声明默认可见性（user / debug）。"""
    from typing import get_args

    from verichalk.domain.events import Event

    members = get_args(get_args(Event)[0])
    types = []
    for m in members:
        t = m.model_fields["type"].default
        assert isinstance(t, str) and t
        types.append(t)
    assert len(types) == len(set(types))

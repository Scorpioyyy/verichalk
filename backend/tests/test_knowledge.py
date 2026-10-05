from __future__ import annotations

import warnings

import pytest

from verichalk.core.errors import NotFound
from verichalk.domain.events import RetrievalResult, SpanFinished
from verichalk.knowledge import KnowledgeService
from verichalk.trace import MemorySink, Tracer, use_tracer

DECIMAL_KP = "kp.na.小数加减法.different_places"


@pytest.fixture(scope="module")
def kb():
    import os

    saved = {k: os.environ.pop(k, None) for k in ("DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL")}
    from chalkbase import Curriculum

    try:
        yield KnowledgeService(Curriculum())  # pyright: ignore[reportCallIssue]
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


async def test_search_lexical_fallback_emits_retrieval_event(kb):
    sink = MemorySink()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # 无密钥时 chalkbase 会警告并降级为词法检索
        async with use_tracer(Tracer("r", sink)):
            hits = await kb.search("小数加减法", k=5)
    assert hits and all(h.name and h.id.startswith("kp.") for h in hits)
    ev = next(e for e in sink.events if isinstance(e, RetrievalResult))
    assert ev.payload.step == "search" and len(ev.payload.nodes) == len(hits)
    fin = next(e for e in sink.events if isinstance(e, SpanFinished) and e.name == "kb.search")
    assert fin.attrs["n_hits"] == len(hits)


async def test_kp_chain_and_relations(kb):
    kp = await kb.kp(DECIMAL_KP)
    assert (
        kp.name and kp.description and kp.grade == 4 and kp.semester == "b" and kp.lesson_id == "g4b.u1.l06"
    )
    chain = await kb.chain(DECIMAL_KP, depth=2)
    assert chain and all(c.name and c.depth in (1, 2) for c in chain)
    rel = await kb.relations(DECIMAL_KP)
    assert all(r.name for r in rel)


async def test_unknown_id_is_not_found(kb):
    with pytest.raises(NotFound):
        await kb.kp("kp.nope.nope")


async def test_learned_boundary_and_check(kb):
    learned = await kb.learned_before("g4b.u1.l06")
    assert DECIMAL_KP not in learned and len(learned) > 100
    b = await kb.boundary("g4b.u1.l06")
    assert b.decimal_max_places == 3 and b.n_concepts > 0 and len(b.concepts) <= 60
    ok = await kb.check_item({"decimal_places": 2, "operation_forms": {"加法": ["小数"]}}, "g4b.u1.l06")
    assert ok.verdict == "in"
    bad = await kb.check_item({"decimal_places": 2, "operation_forms": {"加法": ["小数"]}}, "g3a.u1.l01")
    assert bad.verdict == "out" and bad.violations


async def test_archetypes_contexts_instantiate(kb):
    ats = await kb.archetypes_for(DECIMAL_KP, limit=3)
    assert 0 < len(ats) <= 3 and all(len(a.template) <= 201 for a in ats)
    ctx = await kb.contexts_for(grade=4, text="购物")
    assert ctx and ctx[0].theme
    p = await kb.instantiate(ats[0].id, seed=1, lesson_id="g4b.u1.l06")
    assert p["problem"] and p["archetype_id"] == ats[0].id


async def test_review_candidates_returns_refs(kb):
    refs = await kb.review_candidates("g5a.u2.l01", target_kp_ids=[DECIMAL_KP], limit=5)
    assert all(r.name for r in refs)


async def test_compact_payloads_stay_small(kb):
    """面向 LLM 的返回必须紧凑：知识点详情与边界摘要的 JSON 不超过 4KB。"""
    kp = await kb.kp(DECIMAL_KP)
    b = await kb.boundary("g4b.u1.l06")
    assert len(kp.model_dump_json()) < 4000 and len(b.model_dump_json()) < 4000

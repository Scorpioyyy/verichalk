"""回放评测的回归测试：smoke 集在无密钥、无网络的环境下必须逐位复现并全部通过。

它同时守住三件事：评测框架本身可用、录制回放确定、知识检索在回放时不依赖网络（查询向量缓存在录制目录里）。
"""

from __future__ import annotations

import pytest

from verichalk.core.config import LLMMode, Settings
from verichalk.eval import load_cases, render_markdown, run_suite


@pytest.fixture
def offline(monkeypatch):
    for k in ("DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL", "DASHSCOPE_INTL_API_KEY", "DASHSCOPE_INTL_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    s = Settings(llm_mode=LLMMode.replay, cassette_namespace="smoke")
    monkeypatch.setenv("CHALKBASE_CACHE", str(s.cassette_path / "chalkbase_embeddings"))
    # chalkbase 的查询向量缓存是模块级全局：进程里先跑过别的检索就不会再按新的 CHALKBASE_CACHE 重载。
    # 生产里环境变量固定，不受影响；测试里需要显式重置以保证与执行顺序无关。
    import chalkbase.query.embed as embed

    monkeypatch.setattr(embed, "_mem", None, raising=False)
    return s


async def test_smoke_suite_replays_green(offline):
    cases = load_cases(offline.root_dir / "eval" / "datasets", "smoke", "all")
    assert len(cases) >= 6
    res = await run_suite(offline, cases, suite="smoke", split="all", concurrency=3)
    failed = [
        (c.case_id, c.error, [o for o in c.outcomes if not o.passed]) for c in res.cases if not c.passed
    ]
    assert not failed, failed
    agg = res.aggregate
    assert agg.success_rate.p == 1.0 and agg.trace_complete.p == 1.0
    assert all(r.metrics.replayed_calls == r.metrics.n_llm_calls for c in res.cases for r in c.results)
    md = render_markdown(res, models={"fast": "x"}, rev="test")
    assert "F1 运行成功率" in md and "失败样本（0）" in md


def test_case_ids_unique_and_splits_valid(offline):
    cases = load_cases(offline.root_dir / "eval" / "datasets", "smoke", "all")
    assert len({c.id for c in cases}) == len(cases)
    assert {c.split for c in cases} <= {"val", "test"}

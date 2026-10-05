"""意图理解评测的回放回归：全部评测集在无密钥、无网络的环境下必须复现，且指标不低于验收线。

录制来自最终配置（提示词 v3、`fast` = deepseek-v4.1-flash）。回放是确定的：任何破坏理解逻辑（后处理、规则、映射、澄清策略）
的改动都会让这些指标变化；改了提示词或模型则需要重新录制（`python -m verichalk.eval run --suite … --record`）。
"""

from __future__ import annotations

import pytest

from verichalk.core.config import LLMMode, Settings
from verichalk.eval import load_cases, run_suite
from verichalk.eval.understand_report import summary

SUITES = [
    ("understand", "val"),
    ("understand", "test"),
    ("understand_para", "val"),
    ("understand_para", "test"),
    ("understand_fresh", "test"),
]


@pytest.fixture
def offline(monkeypatch):
    for k in ("DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL", "DASHSCOPE_INTL_API_KEY", "DASHSCOPE_INTL_BASE_URL"):
        monkeypatch.delenv(k, raising=False)


async def _run(suite: str, split: str, monkeypatch):
    s = Settings(llm_mode=LLMMode.replay, cassette_namespace=suite)
    monkeypatch.setenv("CHALKBASE_CACHE", str(s.cassette_path / "chalkbase_embeddings"))
    import chalkbase.query.embed as embed_mod

    monkeypatch.setattr(embed_mod, "_mem", None)  # chalkbase 的缓存是模块级全局，见 test_eval_smoke
    cases = load_cases(s.root_dir / "eval" / "datasets", suite, split)
    return await run_suite(s, cases, suite=suite, split=split, concurrency=6)


async def test_understanding_meets_acceptance_lines_on_replay(offline, monkeypatch):
    tp = fp = fn = 0
    for suite, split in SUITES:
        res = await _run(suite, split, monkeypatch)
        assert all(c.error is None for c in res.cases), (
            suite,
            split,
            [c.error for c in res.cases if c.error],
        )
        u = summary(res)
        tag = f"{suite}/{split}"
        assert u["b1_field"] >= 0.95, (tag, u)
        assert u["b1_origin"] >= 0.95, (tag, u)
        assert u["b1_hallucination"] <= 0.03, (tag, u)
        assert u["b3_route"] >= 0.95, (tag, u)
        assert res.aggregate.success_rate.p == 1.0 and res.aggregate.trace_complete.p == 1.0
        tp, fp, fn = tp + u["clarify_tp"], fp + u["clarify_fp"], fn + u["clarify_fn"]
    # 单个评测集里应澄清的用例很少，B2 按全部集合合并计算
    assert tp / (tp + fp) >= 0.85 and tp / (tp + fn) >= 0.80, (tp, fp, fn)

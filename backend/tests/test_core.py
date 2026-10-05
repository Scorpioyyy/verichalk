from __future__ import annotations

import logging

from verichalk.core.errors import LLMTimeout, VerichalkError
from verichalk.core.ids import new_id
from verichalk.core.logging import RedactingFilter
from verichalk.core.redact import redact


def test_ids_prefixed_unique_and_sortable():
    ids = [new_id("run") for _ in range(500)]
    assert len(set(ids)) == 500
    assert all(i.startswith("run_") and len(i) == 4 + 26 for i in ids)
    a = new_id("x")
    import time

    time.sleep(0.003)
    b = new_id("x")
    assert a < b


def test_redact_patterns_and_env(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "abcd1234efgh5678")
    monkeypatch.setenv(
        "DASHSCOPE_BASE_URL", "https://my-workspace.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
    )
    text = "key=abcd1234efgh5678 url=https://my-workspace.ap-southeast-1.maas.aliyuncs.com/x Bearer zzzzzzzzzzzz sk-abcdefghijklmnop"
    out = redact(text)
    assert "abcd1234efgh5678" not in out
    assert "aliyuncs" not in out
    assert "zzzzzzzzzzzz" not in out
    assert "sk-abcdefghijklmnop" not in out


def test_error_dict_is_redacted_and_has_user_message(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "abcd1234efgh5678")
    e = LLMTimeout("timeout talking to host abcd1234efgh5678")
    d = e.to_dict()
    assert "abcd1234efgh5678" not in d["message"]
    assert d["code"] == "llm_timeout" and d["retryable"] is True
    assert d["user_message"] and "Traceback" not in d["user_message"]
    assert VerichalkError("x").to_dict()["code"] == "internal_error"


def test_logging_filter_redacts_args_and_exception(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_INTL_API_KEY", "secretsecret1234")
    rec = logging.LogRecord("t", logging.ERROR, "f", 1, "failed with %s", ("secretsecret1234",), None)
    RedactingFilter().filter(rec)
    assert "secretsecret1234" not in rec.getMessage()
    try:
        raise RuntimeError("boom secretsecret1234")
    except RuntimeError:
        import sys

        rec2 = logging.LogRecord("t", logging.ERROR, "f", 1, "x", (), sys.exc_info())
    RedactingFilter().filter(rec2)
    assert "secretsecret1234" not in (rec2.exc_text or "")

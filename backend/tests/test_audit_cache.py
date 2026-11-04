"""审计缓存（eval/CHANGELOG.md 2026-11-04）：审计模型偶发失败时再试一次；仍不可用的结论不写入缓存，下次运行重新审计。"""

from __future__ import annotations

from pathlib import Path

from verichalk.eval.audit import AuditItem, Auditor, AuditRecord, AuditStore


def _auditor(tmp_path: Path, results: list[str]) -> tuple[Auditor, AuditStore, list[int]]:
    store = AuditStore(tmp_path / "a.jsonl")
    au = Auditor.__new__(Auditor)  # 不建网关：只测缓存与重试的逻辑
    au.store, au.live = store, True
    import asyncio

    au.sem = asyncio.Semaphore(1)
    calls = [0]

    async def fake(it: AuditItem) -> AuditRecord:
        calls[0] += 1
        a2, sep, a3 = results[min(calls[0] - 1, len(results) - 1)].partition("/")
        return AuditRecord(key=it.key, a2=a2, a3=a3 if sep else "in")  # "correct/" = 超纲结论为空

    au._audit = fake  # type: ignore[method-assign]
    return au, store, calls


ITEM = AuditItem(stem="1+1=____", options=[], answers=["2"], solution="", kind="fill", grade=1, lesson_id="l")


async def test_unavailable_is_retried_once_and_never_cached(tmp_path: Path) -> None:
    au, store, calls = _auditor(tmp_path, ["unavailable", "unavailable"])
    assert (await au.audit(ITEM)).a2 == "unavailable" and calls[0] == 2
    assert ITEM.key not in store.records and not (tmp_path / "a.jsonl").exists()


async def test_success_after_retry_is_cached(tmp_path: Path) -> None:
    au, store, calls = _auditor(tmp_path, ["unavailable", "correct"])
    assert (await au.audit(ITEM)).a2 == "correct" and calls[0] == 2
    assert store.records[ITEM.key].a2 == "correct"
    assert (await au.audit(ITEM)).a2 == "correct" and calls[0] == 2  # 命中缓存，不再调用


async def test_missing_scope_verdict_is_not_cached(tmp_path: Path) -> None:
    au, store, calls = _auditor(tmp_path, ["correct/", "correct/"])  # 知识库抽取失败：超纲结论为空
    rec = await au.audit(ITEM)
    assert rec.a3 == "" and calls[0] == 2 and ITEM.key not in store.records

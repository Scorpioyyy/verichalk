"""试卷修订机制：手改、撤销 / 重做 / 回退、复核透明、并发、diff（eval/specs/edit.md 失败模式 4～7、R1、R2）。"""

from __future__ import annotations

import random
import time
from types import SimpleNamespace

import pytest

from verichalk.core.config import Settings
from verichalk.core.errors import Conflict, InvalidRequest
from verichalk.domain.paper import (
    CheckResult,
    CheckStatus,
    Item,
    ItemKind,
    Paper,
    Revision,
    Section,
    Source,
    Verification,
    VerifyStatus,
)
from verichalk.domain.paper_ops import diff_papers
from verichalk.orchestrator.papers import PaperService
from verichalk.stages.paper_edit import describe_ops, number_of


def make_item(i: int, **kw) -> Item:
    kw.setdefault("stem", f"题目 {i}：$1+{i}=$____")
    kw.setdefault("answer", str(1 + i))
    kw.setdefault("verification", Verification(status=VerifyStatus.verified))
    return Item(id=f"i{i}", kind=ItemKind.calc, **kw)


def base_paper() -> Paper:
    return Paper(
        id="p",
        title="试卷",
        rev=1,
        sections=[
            Section(id="s1", title="计算", items=[make_item(1), make_item(2)]),
            Section(id="s2", title="应用", items=[make_item(3)]),
        ],
    )


class Reviewer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[tuple[str, int]]]] = []

    async def __call__(self, session_id: str, targets: list[tuple[str, int]]):
        self.calls.append((session_id, targets))
        return SimpleNamespace(id=f"run_{len(self.calls)}")


@pytest.fixture
async def env(store):
    ses = await store.sessions.create("t")
    await store.papers.save(
        ses.id, base_paper(), Revision(paper_id="p", rev=1, author="agent", ts=time.time())
    )
    rv = Reviewer()
    svc = PaperService(Settings(), store, rv)
    return svc, ses.id, rv, store


def set_field(item_id: str, field: str, value) -> dict:
    return {"op": "replace_field", "item_id": item_id, "field": field, "value": value}


async def stems(store, sid: str) -> list[str]:
    p = await store.papers.get_current(sid)
    assert p is not None
    return [it.stem for it in p.all_items()]


# ---- 手改的约定 ----
async def test_edit_content_marks_pending_edited_and_starts_review(env) -> None:
    svc, sid, rv, _ = env
    out = await svc.edit(sid, [set_field("i1", "stem", "改过的题干 $2+2=$____")])
    it = out.paper.find_item("i1")[2]  # type: ignore[index]
    assert it.verification.status == VerifyStatus.pending and it.provenance.source == Source.edited
    assert it.rev == 2 and out.paper.rev == 2 and out.review_run_id == "run_1"
    assert rv.calls == [(sid, [("i1", 2)])]
    assert "第 1 题" in out.revision.summary and "题干" in out.revision.summary
    # 没改的题保持原样
    other = out.paper.find_item("i2")[2]  # type: ignore[index]
    assert other.verification.status == VerifyStatus.verified and other.rev == 1


async def test_edit_score_does_not_invalidate_verification(env) -> None:
    svc, sid, rv, _ = env
    out = await svc.edit(sid, [set_field("i1", "score", 5)])
    it = out.paper.find_item("i1")[2]  # type: ignore[index]
    assert it.verification.status == VerifyStatus.verified and out.review_run_id is None and not rv.calls


async def test_edit_answer_drops_stale_structured_answer(env) -> None:
    svc, sid, _, store = env
    p = await store.papers.get_current(sid)
    assert p is not None
    await svc.edit(sid, [set_field("i1", "answer_value", [{"label": "", "value": "2", "unit": ""}])])
    out = await svc.edit(sid, [set_field("i1", "answer", "3")])
    assert out.paper.find_item("i1")[2].answer_value is None  # type: ignore[index]


async def test_edit_normalizes_text_and_warns_on_structure(env) -> None:
    svc, sid, _, _ = env
    out = await svc.edit(sid, [set_field("i1", "stem", "计算：$ 3.6-1.25 = $____")])
    stem = out.paper.find_item("i1")[2].stem  # type: ignore[index]
    assert "$ " not in stem and " $" not in stem  # 公式内侧空格被修剪（D27）
    out = await svc.edit(sid, [set_field("i2", "kind", "choice")])  # 选择题没有选项
    assert any("第 2 题" in w for w in out.warnings)


async def test_invalid_patch_is_atomic_and_rejected(env) -> None:
    svc, sid, _, store = env
    before = await stems(store, sid)
    with pytest.raises(InvalidRequest):
        await svc.edit(sid, [set_field("i1", "stem", "新"), set_field("nope", "stem", "x")])
    with pytest.raises(InvalidRequest):
        await svc.edit(sid, [{"op": "explode"}])
    with pytest.raises(InvalidRequest):
        await svc.edit(sid, [])
    assert await stems(store, sid) == before
    assert (await store.papers.head(sid)).rev == 1  # type: ignore[union-attr]


async def test_add_remove_move_sections(env) -> None:
    svc, sid, _, _ = env
    new_item = make_item(9).model_dump(mode="json")
    out = await svc.edit(
        sid,
        [
            {"op": "add_item", "section_id": "s2", "item": new_item},
            {"op": "move_item", "item_id": "i1", "section_id": "s2", "index": 0},
            {"op": "remove_item", "item_id": "i2"},
            {"op": "set_title", "title": "新标题"},
        ],
    )
    assert [it.id for it in out.paper.sections[1].items] == ["i1", "i3", "i9"]
    assert out.paper.title == "新标题" and out.paper.find_item("i9")[2].provenance.source == Source.edited  # type: ignore[index]
    assert out.review_run_id  # 新增的题要复核


# ---- 撤销 / 重做 / 回退 ----
async def test_undo_redo_roundtrip(env) -> None:
    svc, sid, _, store = env
    s0 = await stems(store, sid)
    await svc.edit(sid, [set_field("i1", "stem", "A")])
    s1 = await stems(store, sid)
    await svc.edit(sid, [set_field("i2", "stem", "B")])
    s2 = await stems(store, sid)
    await svc.undo(sid)
    assert await stems(store, sid) == s1
    await svc.undo(sid)
    assert await stems(store, sid) == s0
    with pytest.raises(InvalidRequest):
        await svc.undo(sid)  # 最早的版本之前没有东西
    await svc.redo(sid)
    assert await stems(store, sid) == s1
    await svc.redo(sid)
    assert await stems(store, sid) == s2
    with pytest.raises(InvalidRequest):
        await svc.redo(sid)
    h = await svc.history(sid)
    assert h.can_undo and not h.can_redo


async def test_new_edit_clears_redo_stack(env) -> None:
    svc, sid, _, _ = env
    await svc.edit(sid, [set_field("i1", "stem", "A")])
    await svc.undo(sid)
    assert (await svc.history(sid)).can_redo
    await svc.edit(sid, [set_field("i1", "stem", "C")])
    assert not (await svc.history(sid)).can_redo


async def test_history_is_append_only_and_restore_is_undoable(env) -> None:
    svc, sid, _, store = env
    await svc.edit(sid, [set_field("i1", "stem", "A")])
    await svc.edit(sid, [set_field("i1", "stem", "B")])
    n_before = len((await svc.history(sid)).revisions)
    s_rev1 = (await store.papers.get_revision(sid, 1)).all_items()[0].stem
    out = await svc.restore(sid, 1)
    assert (await stems(store, sid))[0] == s_rev1 and out.revision.kind == "restore"
    h = await svc.history(sid)
    assert len(h.revisions) == n_before + 1  # 只增不删
    await svc.undo(sid)  # 回退这一步本身可撤销
    assert (await stems(store, sid))[0] == "B"


async def test_review_revisions_are_transparent_to_undo(env) -> None:
    svc, sid, _, store = env
    await svc.edit(sid, [set_field("i1", "stem", "A")])
    p = await store.papers.get_current(sid)
    assert p is not None
    ver = Verification(
        status=VerifyStatus.checked, checks=[CheckResult(name="blind", status=CheckStatus.passed)]
    )
    saved = await store.papers.set_verification(sid, "i1", p.find_item("i1")[2].rev, ver)  # type: ignore[index]
    assert saved is not None and saved.kind == "review"
    # 复核之后撤销：撤掉的是用户的编辑，不是复核
    await svc.undo(sid)
    assert (await stems(store, sid))[0] != "A"
    # 重做：拿回编辑，并且带着复核的结论
    await svc.redo(sid)
    it = (await store.papers.get_current(sid)).find_item("i1")[2]  # type: ignore[union-attr,index]
    assert it.stem == "A" and it.verification.status == VerifyStatus.checked


async def test_stale_review_result_is_dropped(env) -> None:
    """R2：复核期间用户又改了这道题，旧的复核结论不得覆盖。"""
    svc, sid, _, store = env
    out = await svc.edit(sid, [set_field("i1", "stem", "A")])
    rev_at_review = out.paper.find_item("i1")[2].rev  # type: ignore[index]
    await svc.edit(sid, [set_field("i1", "stem", "B")])  # 用户又改了
    ver = Verification(status=VerifyStatus.checked)
    assert await store.papers.set_verification(sid, "i1", rev_at_review, ver) is None
    it = (await store.papers.get_current(sid)).find_item("i1")[2]  # type: ignore[union-attr,index]
    assert it.verification.status == VerifyStatus.pending and it.stem == "B"


async def test_conflict_detection_ignores_review_revisions(env) -> None:
    svc, sid, _, store = env
    out = await svc.edit(sid, [set_field("i1", "stem", "A")])
    seen = out.paper.rev  # 客户端看到的版本
    p = out.paper
    await store.papers.set_verification(
        sid, "i1", p.find_item("i1")[2].rev, Verification(status=VerifyStatus.checked)
    )  # type: ignore[index]
    # 后台复核让版本号前进了，但内容没变：不应冲突
    await svc.edit(sid, [set_field("i2", "stem", "B")], base_rev=seen)
    # 别处有过内容修改：冲突
    with pytest.raises(Conflict):
        await svc.edit(sid, [set_field("i3", "stem", "C")], base_rev=seen)


# ---- 属性测试：与参考模型对拍 ----
@pytest.mark.parametrize("seed", range(6))
async def test_random_sequences_match_reference_model(store, seed: int) -> None:
    rnd = random.Random(seed)
    ses = await store.sessions.create("t")
    await store.papers.save(
        ses.id, base_paper(), Revision(paper_id="p", rev=1, author="agent", ts=time.time())
    )
    svc = PaperService(Settings(), store, None)
    sid = ses.id
    undo_stack: list[str] = []  # 参考模型：标题的历史
    redo_stack: list[str] = []
    cur = "试卷"
    for step in range(40):
        op = rnd.choice(["edit", "edit", "undo", "redo", "review"])
        if op == "edit":
            new = f"标题{step}"
            await svc.edit(sid, [{"op": "set_title", "title": new}])
            undo_stack.append(cur)
            cur, redo_stack = new, []
        elif op == "undo":
            if undo_stack:
                await svc.undo(sid)
                redo_stack.append(cur)
                cur = undo_stack.pop()
            else:
                with pytest.raises(InvalidRequest):
                    await svc.undo(sid)
        elif op == "redo":
            if redo_stack:
                await svc.redo(sid)
                undo_stack.append(cur)
                cur = redo_stack.pop()
            else:
                with pytest.raises(InvalidRequest):
                    await svc.redo(sid)
        else:
            p = await store.papers.get_current(sid)
            assert p is not None
            await store.papers.set_verification(
                sid, "i2", p.find_item("i2")[2].rev, Verification(status=VerifyStatus.checked)
            )  # type: ignore[index]
        p = await store.papers.get_current(sid)
        h = await svc.history(sid)
        assert p is not None and p.title == cur, (seed, step, op)
        assert h.can_undo == bool(undo_stack) and h.can_redo == bool(redo_stack), (seed, step, op)
        assert p.rev == h.head_rev


# ---- diff 与描述 ----
def test_diff_papers() -> None:
    a = base_paper()
    b = a.model_copy(deep=True)
    b.sections[0].items[0] = b.sections[0].items[0].model_copy(update={"stem": "新", "score": 3})
    moved = b.sections[0].items.pop(1)
    b.sections[1].items.insert(0, moved)
    b.sections[1].items.append(make_item(7))
    b.title = "新标题"
    d = diff_papers(a, b)
    kinds = {c.item_id: (c.change, c.fields) for c in d.items}
    assert d.title_changed and not d.sections_changed
    assert kinds["i1"] == ("changed", ["stem", "score"])
    assert kinds["i2"][0] == "moved" and kinds["i7"][0] == "added"
    assert diff_papers(a, a.model_copy(deep=True)).empty


def test_describe_ops_and_numbering() -> None:
    from verichalk.domain.paper_ops import parse_ops

    p = base_paper()
    assert number_of(p, "i3") == 3 and number_of(p, "zz") is None
    s = describe_ops(
        p,
        parse_ops(
            [
                set_field("i2", "stem", "x"),
                set_field("i2", "answer", "y"),
                {"op": "remove_item", "item_id": "i3"},
            ]
        ),
    )
    assert "删除第 3 题" in s and "修改第 2 题的题干、答案" in s

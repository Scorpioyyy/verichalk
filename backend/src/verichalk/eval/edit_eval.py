"""M5 自然语言编辑评测（eval/specs/edit.md，指标 C1）。

每个用例在固定的 8 题试卷上执行一句编辑指令（直接跑 `EditStage`，目标由用例给出——理解阶段的路由与目标 M2 已单独评测），
然后检查：①请求的改动体现了；②没有附带破坏（未点名的题逐字段不变）；③被改写的题复核通过。三者同时满足才算 C1 成功。
确定性检查优先（题型 / 选项数 / 删除 / 顺序 / 分值 / 标题 / 数字大小 / 答案不变）；语义类（换情境、难度变化）用与写题模型不同源的判官。
"""

from __future__ import annotations

import asyncio
import re
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from ..core.config import Settings
from ..core.ids import new_id
from ..domain.edit import EditOut
from ..domain.llm import ChatMessage, Role
from ..domain.paper import Item, Paper, Revision, Section, VerifyStatus
from ..llm import LLMRequest, complete_json
from ..orchestrator import build_container
from ..stages import RunContext, run_stage
from ..stages.edit import EditIn, EditStage
from ..store import Store
from ..trace import MemorySink, Tracer, use_tracer

_JUDGE_SYSTEM = (
    "你是小学数学教研员。给你一道题改写前后的两个版本和教师的修改要求，判断**改写后的题是否体现了要求**。"
    '只输出 JSON：{"ok": true 或 false, "reason": "一句话"}。只看是否体现要求，不看答案对错。'
)


class _Verdict(BaseModel):
    ok: bool
    reason: str = ""


@dataclass
class EditCase:
    id: str
    split: str
    cat: str
    instruction: str
    target: str
    touch: list[str] | str
    expect: dict[str, Any]


@dataclass
class EditResult:
    case: EditCase
    c1a: bool = False  # 改动体现
    c1b: bool = False  # 无附带破坏
    c1c: bool = False  # 复核通过
    ms: float = 0.0
    problems: list[str] = field(default_factory=list)
    summary: str = ""

    @property
    def ok(self) -> bool:
        return self.c1a and self.c1b and self.c1c


def load_papers(path: Path) -> dict[str, Paper]:
    out: dict[str, Paper] = {}
    for p in yaml.safe_load(path.read_text(encoding="utf-8")):
        secs = []
        for s in p["sections"]:
            items = [Item.model_validate(it) for it in s["items"]]
            secs.append(Section(id=new_id("sec"), title=s["title"], items=items))
        out[p["id"]] = Paper(id=p["id"], title=p["title"], rev=1, sections=secs, meta=p.get("meta", {}))
    return out


def load_edit_cases(path: Path, split: str = "all") -> list[EditCase]:
    cases = [EditCase(**c) for c in yaml.safe_load(path.read_text(encoding="utf-8"))]
    return [c for c in cases if split == "all" or c.split == split]


def _items(p: Paper) -> dict[str, Item]:
    return {it.id: it for it in p.all_items()}


_NUM = re.compile(r"\d+(?:\.\d+)?")


async def _judge(ctx: RunContext, before: Item, after: Item, requirement: str) -> tuple[bool, str]:
    user = (
        f"改写前题干：{before.stem}\n改写前答案：{before.answer}\n\n改写后题干：{after.stem}\n"
        + (f"改写后选项：{' ｜ '.join(after.options)}\n" if after.options else "")
        + f"改写后答案：{after.answer}\n\n要检查的要求：{requirement}"
    )
    v, _ = await complete_json(
        ctx.llm,
        LLMRequest(
            role=Role.judge,
            messages=[
                ChatMessage(role="system", content=_JUDGE_SYSTEM),
                ChatMessage(role="user", content=user),
            ],
            purpose="eval.edit.judge",
            max_tokens=300,
        ),
        _Verdict,
    )
    return v.ok, v.reason


async def check_case(
    ctx: RunContext, case: EditCase, before: Paper, after: Paper, out: EditOut
) -> EditResult:
    res = EditResult(case=case, summary=out.summary or out.unsupported)
    exp = case.expect
    b, a = _items(before), _items(after)
    changed_rev = out.rev is not None

    if exp.get("no_change"):
        res.c1a = not changed_rev and bool(out.unsupported or out.results)
        if not res.c1a:
            res.problems.append("应当不改试卷并说明原因，却产生了修订或没有说明")
        res.c1b = after.model_dump() == before.model_dump()
        res.c1c = True
        if not res.c1b:
            res.problems.append("试卷被改动了")
        return res

    # ② 无附带破坏：未点名的题逐字段不变（不比较 rev：移动会让被移动的题 rev+1）
    touch = set(b) if case.touch == "all" else set(case.touch)
    removed = set(exp.get("removed", []))
    c1b = True
    for iid, it in b.items():
        if iid in touch or iid in removed:
            continue
        if iid not in a:
            res.problems.append(f"{iid} 不该被删除")
            c1b = False
        elif it.model_dump(exclude={"rev"}) != a[iid].model_dump(exclude={"rev"}):
            res.problems.append(f"{iid} 不该被改动却变了")
            c1b = False
    structural = bool(exp.get("order") or exp.get("removed"))
    if not structural and [i.id for i in before.all_items()] != [i.id for i in after.all_items()]:
        res.problems.append("题目顺序 / 数量变了")
        c1b = False
    res.c1b = c1b

    # ① 改动体现
    c1a = True

    def fail(msg: str) -> None:
        nonlocal c1a
        c1a = False
        res.problems.append(msg)

    for iid, kind in exp.get("kind", {}).items():
        if iid not in a or a[iid].kind.value != kind:
            fail(f"{iid} 的题型应为 {kind}，实际 {a[iid].kind.value if iid in a else '已删除'}")
    for iid, n in exp.get("n_options", {}).items():
        if iid in a and len(a[iid].options) != n:
            fail(f"{iid} 应有 {n} 个选项，实际 {len(a[iid].options)}")
    for iid in removed:
        if iid in a:
            fail(f"{iid} 应被删除")
    if "order" in exp:
        got = [i.id for i in after.all_items()][: len(exp["order"])]
        if got != exp["order"]:
            fail(f"顺序应为 {exp['order']}，实际 {got}")
    for iid, sc in exp.get("scores", {}).items():
        if iid not in a or a[iid].score != sc:
            fail(f"{iid} 的分值应为 {sc}，实际 {a[iid].score if iid in a else '已删除'}")
    if "all_scores" in exp and any(it.score != exp["all_scores"] for it in a.values()):
        fail(f"每题分值应为 {exp['all_scores']}")
    if "total" in exp:
        tot = sum(it.score or 0 for it in a.values())
        if tot != exp["total"]:
            fail(f"总分应为 {exp['total']}，实际 {tot}")
    if "title" in exp and after.title != exp["title"]:
        fail(f"标题应为「{exp['title']}」，实际「{after.title}」")
    for iid, mx in exp.get("numbers_max", {}).items():
        if iid in a:
            nums = [float(x) for x in _NUM.findall(a[iid].stem + " ".join(a[iid].options))]
            if nums and max(nums) > mx:
                fail(f"{iid} 里出现了大于 {mx} 的数：{max(nums):g}")
    for iid, kws in exp.get("keywords_any", {}).items():
        if iid in a and not any(k in a[iid].stem for k in kws):
            fail(f"{iid} 的题干里没有出现情境关键词 {kws}")
    for iid in exp.get("answer_same", []):
        if iid in a and a[iid].answer.strip() != b[iid].answer.strip():
            fail(f"{iid} 的答案变了：{b[iid].answer} → {a[iid].answer}")
    for iid in exp.get("stem_same", []):
        if iid in a and a[iid].stem != b[iid].stem:
            fail(f"{iid} 的题干不该变")
    for iid in exp.get("longer_solution", []):
        if iid in a and len(a[iid].solution) <= len(b[iid].solution):
            fail(f"{iid} 的解析没有变详细")
    for iid in exp.get("difficulty_lte", []):
        if iid in a and a[iid].difficulty > b[iid].difficulty:
            fail(f"{iid} 的难度不降反升：{b[iid].difficulty} → {a[iid].difficulty}")
    for iid in exp.get("difficulty_gte", []):
        if iid in a and a[iid].difficulty < b[iid].difficulty:
            fail(f"{iid} 的难度不升反降：{b[iid].difficulty} → {a[iid].difficulty}")
    for iid, req in exp.get("judge", {}).items():
        if iid in a:
            ok, why = await _judge(ctx, b[iid], a[iid], req)
            if not ok:
                fail(f"{iid} 判官认为没有体现要求：{why}")
    # 没有任何改动却期望有改动
    if not changed_rev and not exp.get("no_change"):
        fail("没有产生任何修改：" + (out.unsupported or "；".join(r.detail for r in out.results)))
    res.c1a = c1a

    # ③ 被改写（内容变了）的题复核通过
    c1c = True
    for iid, it in a.items():
        old = b.get(iid)
        if old is None:
            continue
        if (old.stem, old.options, old.answer, old.solution, old.kind) != (
            it.stem,
            it.options,
            it.answer,
            it.solution,
            it.kind,
        ):
            if it.verification.status not in (VerifyStatus.verified, VerifyStatus.checked):
                why = "；".join(
                    f"{c.name}:{c.detail[:60]}"
                    for c in it.verification.checks
                    if c.status.value in ("warn", "fail")
                )
                res.problems.append(f"{iid} 改写后核验状态为 {it.verification.status.value}（{why}）")
                c1c = False
    res.c1c = c1c
    return res


async def run_edit_cases(
    settings: Settings, cases: list[EditCase], papers: dict[str, Paper], concurrency: int = 4
) -> list[EditResult]:
    c = await build_container(settings, store=await Store.open(":memory:"))
    sem = asyncio.Semaphore(concurrency)

    async def one(case: EditCase) -> EditResult:
        async with sem:
            ses = await c.store.sessions.create(case.id)
            base = papers["base8"]
            await c.store.papers.save(
                ses.id,
                base.model_copy(deep=True),
                Revision(paper_id=base.id, rev=1, author="agent", ts=time.time()),
            )
            ctx = RunContext(
                run_id=new_id("run"),
                session_id=ses.id,
                settings=settings,
                llm=c.llm,
                kb=c.kb,
                store=c.store,
                has_paper=True,
            )
            t0 = time.perf_counter()
            try:
                async with use_tracer(Tracer(ctx.run_id, MemorySink())):
                    out: EditOut = await run_stage(
                        ctx, EditStage(), EditIn(instruction=case.instruction, target=case.target)
                    )
                    ms = (time.perf_counter() - t0) * 1000
                    before = papers["base8"]
                    after = await c.store.papers.get_current(ses.id)
                    assert after is not None
                    return_res = await check_case(ctx, case, before, after, out)
                    return_res.ms = ms
                    return return_res
            except Exception as e:  # 崩溃也是一次失败，不中断评测
                r = EditResult(case=case, problems=[f"崩溃：{type(e).__name__}: {str(e)[:200]}"])
                r.ms = (time.perf_counter() - t0) * 1000
                return r

    try:
        return list(await asyncio.gather(*(one(x) for x in cases)))
    finally:
        await c.close()


def render_edit_report(results: list[EditResult], title: str) -> str:
    n = len(results)
    ok = sum(r.ok for r in results)
    lines = [f"# 编辑评测（C1）：{title}", ""]
    c1 = ok / n if n else 0.0
    lines += [
        "| 指标 | 值 | 阈值 | 判定 |",
        "|---|---|---|---|",
        f"| **C1** 编辑成功率（①②③ 同时满足） | {c1:.3f}（{ok}/{n}） | ≥ 0.90 | {'✅' if c1 >= 0.9 else '❌'} |",
        f"| C1① 改动体现 | {sum(r.c1a for r in results) / n:.3f} | — | |",
        f"| C1② 无附带破坏 | {sum(r.c1b for r in results) / n:.3f} | = 1.00 | {'✅' if all(r.c1b for r in results) else '❌'} |",
        f"| C1③ 复核通过 | {sum(r.c1c for r in results) / n:.3f} | — | |",
    ]
    ms = sorted(r.ms for r in results)
    if ms:
        lines.append(
            f"| 耗时 p50 / p95（秒） | {statistics.median(ms) / 1000:.1f} / {ms[int(0.95 * (len(ms) - 1))] / 1000:.1f} | — | |"
        )
    lines += ["", "## 分类", "", "| 类别 | 用例 | C1 | ① | ② | ③ |", "|---|---|---|---|---|---|"]
    cats: dict[str, list[EditResult]] = {}
    for r in results:
        cats.setdefault(r.case.cat, []).append(r)
    for cat, rs in cats.items():
        k = len(rs)
        lines.append(
            f"| {cat} | {k} | {sum(r.ok for r in rs)}/{k} | {sum(r.c1a for r in rs)}/{k} | {sum(r.c1b for r in rs)}/{k} | {sum(r.c1c for r in rs)}/{k} |"
        )
    bad = [r for r in results if not r.ok]
    if bad:
        lines += ["", "## 失败明细", ""]
        for r in bad:
            lines.append(
                f"- **{r.case.id}**「{r.case.instruction}」：{'；'.join(r.problems) or '（无说明）'}"
            )
    lines += ["", "## 全部用例", "", "| 用例 | 指令 | 结果 | 秒 |", "|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r.case.id} | {r.case.instruction} | {'✅' if r.ok else '❌'} | {r.ms / 1000:.1f} |")
    return "\n".join(lines) + "\n"

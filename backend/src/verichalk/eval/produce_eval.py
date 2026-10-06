"""生成与核验的端到端评测（eval/specs/produce.md §3）：运行、审计、打分、出报告，以及"朴素直出"基线 B0。

流程：用例（自然语言请求）经完整管线（理解 → 规划 → 生成与核验）→ 取出交付的题与蓝图 → 审计（独立于线上核验，见 audit.py）
→ 指标：A1 可直接使用率、A2 答案正确率、A3 不超纲率、A4 需求满足度、A5 新颖度、A6 题面质量、D2 / D3 延迟、E1 / E3 / E4 成本与修复。
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from ..core.config import Settings
from ..core.errors import VerichalkError
from ..core.ids import new_id
from ..domain.blueprint import Blueprint
from ..domain.events import ItemStatus, RunStarted
from ..domain.llm import ChatMessage, Role
from ..domain.paper import Item, ItemKind, Provenance, Source, Tier, Verification, VerifyStatus
from ..domain.understanding import Understanding
from ..knowledge import KnowledgeService
from ..llm import LLMGateway, LLMRequest, complete_json
from ..metrics import compute_run_metrics
from ..metrics.stats import percentile, wilson
from ..orchestrator import build_container
from ..stages.produce import ProduceOut
from ..store import Store
from ..verify.checks import similarity
from .audit import AuditItem, Auditor, AuditRecord
from .cases import Case
from .runner import _drive


@dataclass
class ItemRec:
    item: Item | None
    spec_index: int
    dropped_reason: str = ""
    attempts: int = 0
    failed_checks: list[list[str]] = field(default_factory=list)


@dataclass
class CaseRec:
    case: Case
    status: str = "failed"
    error: str = ""
    lesson_id: str | None = None
    grade: int | None = None
    n_requested: int = 0
    items: list[ItemRec] = field(default_factory=list)
    scenes: list[str] = field(default_factory=list)
    clarified: bool = False
    cost: float | None = None
    calls: int = 0
    e2e_s: float | None = None
    first_item_s: float | None = None
    trace_ok: bool | None = None  # F6：事件日志的 span 树完整；朴素基线没有 trace
    method: str = "verichalk"  # verichalk / naive

    @property
    def delivered(self) -> list[Item]:
        return [r.item for r in self.items if r.item is not None]


# ---------------------------------------------------------------- 运行
async def run_case_e2e(c: Any, case: Case, timeout_s: float = 900.0) -> CaseRec:
    rec = CaseRec(case=case)
    turn = case.turns[0]
    try:
        session = await c.manager.create_session(case.id)
        run = await c.manager.start_turn(session.id, turn.user, [], tags=[f"eval:{case.id}", *case.tags])
        done = await _drive(c, run.id, turn.clarify_answer, timeout_s)
    except (VerichalkError, TimeoutError) as e:
        rec.error = str(e) or "timeout"
        return rec
    rec.status = done.status.value
    if done.error:
        rec.error = f"{done.error.code}: {done.error.message}"
    events = await c.store.events.list(run.id)
    m = compute_run_metrics(events)
    rec.cost, rec.calls, rec.e2e_s = m.cost, m.n_llm_calls, (m.e2e_ms or 0) / 1000
    rec.trace_ok = m.trace_ok
    t0 = next((e.ts for e in events if isinstance(e, RunStarted)), events[0].ts if events else 0.0)
    firsts = [
        e.ts
        for e in events
        if isinstance(e, ItemStatus) and e.status in (VerifyStatus.verified, VerifyStatus.checked)
    ]
    rec.first_item_s = (min(firsts) - t0) if firsts else None
    state = done.state
    for key in ("understand:clarified", "understand"):
        if key in state and state[key].get("brief"):
            u = Understanding.model_validate(state[key])
            if u.brief:
                rec.lesson_id = u.brief.target_lesson.value if u.brief.target_lesson else None
                rec.grade = u.brief.scope.grade.value if u.brief.scope.grade else None
                rec.n_requested = u.brief.count.value if u.brief.count else 0
            rec.clarified = key == "understand:clarified"
            break
    if "plan" in state:
        bp = Blueprint.model_validate(state["plan"])
        rec.lesson_id = rec.lesson_id or bp.lesson_id
        rec.n_requested = rec.n_requested or len(bp.items)
        rec.scenes = [s.scene for s in bp.items]
        for spec in bp.items:
            out = state.get(f"produce:{spec.id}")
            if out:
                po = ProduceOut.model_validate(out)
                rec.items.append(
                    ItemRec(po.item, spec.index, po.dropped_reason, po.attempts, po.failed_checks)
                )
    return rec


async def run_cases(settings: Settings, cases: list[Case], concurrency: int = 4) -> list[CaseRec]:
    c = await build_container(settings, store=await Store.open(":memory:"))
    c.warmer.trigger()
    await c.warmer.wait()
    sem = asyncio.Semaphore(concurrency)

    async def one(case: Case) -> CaseRec:
        async with sem:
            return await run_case_e2e(c, case)

    try:
        return list(await asyncio.gather(*(one(x) for x in cases)))
    finally:
        await c.close()


# ---------------------------------------------------------------- 朴素基线 B0
class _NaiveItem(BaseModel):
    stem: str
    answer: str
    solution: str = ""


class _NaiveOut(BaseModel):
    items: list[_NaiveItem]


async def run_naive(gw: LLMGateway, ref: list[CaseRec]) -> list[CaseRec]:
    """B0：`smart` 模型对教师的原话单次直出，无知识库、无核验。目标课时 / 年级沿用线上管线对同一用例的理解（审计要用）。"""
    sem = asyncio.Semaphore(6)

    async def one(r: CaseRec) -> CaseRec:
        out = CaseRec(
            case=r.case, method="naive", lesson_id=r.lesson_id, grade=r.grade, n_requested=r.n_requested
        )
        text = r.case.turns[0].user
        msgs = [
            ChatMessage(
                role="system",
                content="你是小学数学命题老师。按教师的要求出题，每题给出题面（stem）、最终答案（answer，只写最终答案，不要写过程）、简要解析（solution）。"
                '只输出 JSON：{"items": [{"stem": "", "answer": "", "solution": ""}]}。',
            ),
            ChatMessage(role="user", content=text),
        ]
        async with sem:
            t0 = time.time()
            try:
                parsed, res = await complete_json(
                    gw,
                    LLMRequest(role=Role.smart, messages=msgs, purpose="naive.write", max_tokens=6000),
                    _NaiveOut,
                )
            except VerichalkError as e:
                out.error = str(e)
                return out
        out.status, out.cost, out.calls, out.e2e_s = "succeeded", res.cost, 1, time.time() - t0
        for i, it in enumerate(
            parsed.items[: r.n_requested or None]
        ):  # 教师要 n 道就只取前 n 道（多写的不算）
            item = Item(
                id=new_id("itm"),
                kind=ItemKind.application,
                stem=it.stem,
                answer=it.answer,
                answer_value=[{"value": it.answer}],
                solution=it.solution,
                tier=Tier.consolidate,
                provenance=Provenance(source=Source.novel, model=res.model),
                verification=Verification(status=VerifyStatus.pending),
            )
            out.items.append(ItemRec(item, i + 1))
        return out

    return list(await asyncio.gather(*(one(r) for r in ref if r.status == "succeeded")))


# ---------------------------------------------------------------- 审计与打分
async def audit_records(
    auditor: Auditor, kb: KnowledgeService, recs: list[CaseRec]
) -> dict[str, AuditRecord]:
    g = await kb.graph()
    pending: list[tuple[str, AuditItem]] = []
    for r in recs:
        for ir in r.items:
            if ir.item is None:
                continue
            it = ir.item
            vals = answer_values_of(it)
            ai = AuditItem(
                stem=it.stem,
                options=it.options,
                answers=vals,
                solution=it.solution,
                kind=it.kind.value,
                grade=r.grade,
                lesson_id=r.lesson_id,
                kp_names=[g.nodes[k].name for k in it.kp_ids if k in g.nodes],
                tier=it.tier.value,
            )
            pending.append((it.id, ai))
    recs_out = await asyncio.gather(*(auditor.audit(ai) for _, ai in pending))
    return {iid: rec for (iid, _), rec in zip(pending, recs_out, strict=True)}


def answer_values_of(it: Item) -> list[str]:
    parts = it.answer_value if isinstance(it.answer_value, list) else []
    vals = [str(p.get("value", "")) for p in parts if isinstance(p, dict)]
    return vals or [it.answer]


_QKEYS = ("unambiguous", "complete", "data_plausible", "age_ok", "solution_ok")


def item_passes(it: Item, a: AuditRecord) -> bool:
    """A1 判据：答案正确 ∧ 不超纲 ∧ 题意明确且完整 ∧ 数据合理 ∧ 适龄 ∧ 解析成立 ∧（综合题）真综合。"""
    q = a.quality
    if a.a2 != "correct" or a.a3 == "out" or not q:
        return False
    if not all(q.get(k, False) for k in _QKEYS):
        return False
    if it.tier == Tier.integrated and len(it.kp_ids) > 1 and not all(q.get("kp_used", {}).values()):
        return False
    return True


async def kb_examples(kb: KnowledgeService, kp_ids: list[str]) -> list[str]:
    out: list[str] = []
    for k in kp_ids[:2]:
        for a in await kb.archetypes_for(k, limit=8):
            out += a.examples
    return out


def a4_checks(rec: CaseRec, names: dict[str, str]) -> list[tuple[str, bool]]:
    """需求满足度（确定性部分）：逐项期望 → (名称, 是否满足)。"""
    exp = rec.case.turns[0].expect
    items = rec.delivered
    out: list[tuple[str, bool]] = []
    if "n_items" in exp:
        out.append(("n_items", len(items) == exp["n_items"]))
    if exp.get("topics_any"):
        kws = exp["topics_any"]
        hit = sum(
            1
            for it in items
            if any(k in names.get(kp, "") for kp in it.kp_ids for k in kws) or any(k in it.stem for k in kws)
        )
        out.append(("topics_any", bool(items) and hit * 2 >= len(items)))
    for t, n in (exp.get("tier_min") or {}).items():
        out.append((f"tier_min:{t}", sum(1 for it in items if it.tier.value == t) >= n))
    if exp.get("kinds_only"):
        allowed = set(exp["kinds_only"])
        out.append(("kinds_only", bool(items) and all(it.kind.value in allowed for it in items)))
    if exp.get("scene_any"):
        out.append(
            (
                "scene_any",
                any(any(k in s for k in exp["scene_any"]) for s in rec.scenes)
                or any(any(k in it.stem for k in exp["scene_any"]) for it in items),
            )
        )
    if exp.get("source"):
        want = exp["source"]
        out.append(("source", bool(items) and all(it.provenance.source.value == want for it in items)))
    if exp.get("clarify"):
        out.append(("clarify", rec.clarified))
    return out


def _ci(k: int, n: int) -> str:
    if n == 0:
        return "—"
    lo, hi = wilson(k, n)
    return f"{k / n:.3f} ({k}/{n}) [{lo:.2f},{hi:.2f}]"


async def score(recs: list[CaseRec], audits: dict[str, AuditRecord], kb: KnowledgeService) -> dict[str, Any]:
    g = await kb.graph()
    names = {k: n.name for k, n in g.nodes.items()}
    delivered = [(r, it) for r in recs for it in r.delivered]
    aud = [(r, it, audits.get(it.id)) for r, it in delivered]
    aud = [(r, it, a) for r, it, a in aud if a is not None and a.a2 != "unavailable"]
    out: dict[str, Any] = {"n_cases": len(recs), "n_delivered": len(delivered), "n_audited": len(aud)}
    out["n_requested"] = sum(r.n_requested for r in recs)
    out["fill"] = (len(delivered), out["n_requested"])  # 题量达成率
    out["case_failed"] = sum(1 for r in recs if r.status != "succeeded")
    traced = [r for r in recs if r.trace_ok is not None]
    out["f6"] = (sum(1 for r in traced if r.trace_ok), len(traced))
    # A2
    verified = [(r, it, a) for r, it, a in aud if it.verification.status == VerifyStatus.verified]
    out["a2_all"] = (sum(1 for *_, a in aud if a.a2 == "correct"), len(aud))
    out["a2_verified"] = (sum(1 for *_, a in verified if a.a2 == "correct"), len(verified))
    out["a2_wrong_all"] = sum(1 for *_, a in aud if a.a2 == "wrong")
    out["a2_wrong_verified"] = sum(1 for *_, a in verified if a.a2 == "wrong")
    out["a2_disputed"] = sum(1 for *_, a in aud if a.a2 == "disputed")
    out["issues"] = [
        f"[{a.a2 if a.a2 != 'correct' else 'A3:' + a.a3}] {it.stem[:90].replace(chr(10), ' ')} | 给的答案 {answer_values_of(it)} | 审计 A={a.answers_a} B={a.answers_b}"
        + (f" | 越界 {[v.get('item_value') for v in a.a3_violations][:3]}" if a.a3 == "out" else "")
        for _, it, a in aud
        if a.a2 in ("wrong", "disputed") or a.a3 == "out"
    ]
    # A3
    b = [a for *_, a in aud if a.a3]
    out["a3_out"] = (sum(1 for a in b if a.a3 == "out"), len(b))
    out["a3_borderline"] = (sum(1 for a in b if a.a3 == "borderline"), len(b))
    # A6
    qa = [a.quality for *_, a in aud if a.quality]
    out["a6"] = {k: (sum(1 for q in qa if q.get(k, False)), len(qa)) for k in _QKEYS}
    out["quality_issues"] = [
        f"{it.stem[:70].replace(chr(10), ' ')} | 答案 {answer_values_of(it)} | 解析：{it.solution[:90].replace(chr(10), ' ')} | 判官：{a.quality.get('issues')}"
        for _, it, a in aud
        if a.quality and not all(a.quality.get(k, False) for k in _QKEYS)
    ]
    # A1
    out["a1"] = (sum(1 for _, it, a in aud if item_passes(it, a)), len(aud))
    out["a1_per_requested"] = (sum(1 for _, it, a in aud if item_passes(it, a)), out["n_requested"])
    # A5
    integ = [(it, a) for _, it, a in aud if it.tier == Tier.integrated and len(it.kp_ids) > 1 and a.quality]
    out["a5c"] = (sum(1 for _, a in integ if all(a.quality.get("kp_used", {}).values())), len(integ))
    dup_book, dup_set = 0, 0
    for r in recs:
        stems = [it.stem for it in r.delivered]
        for i, it in enumerate(r.delivered):
            ex = await kb_examples(kb, it.kp_ids)
            if any(similarity(it.stem, e) >= 0.7 for e in ex):
                dup_book += 1
            if any(similarity(it.stem, s) >= 0.6 for j, s in enumerate(stems) if j != i):
                dup_set += 1
    out["a5a"] = (dup_book, len(delivered))
    out["a5b"] = (dup_set, len(delivered))
    # A4
    checks = [c for r in recs for c in a4_checks(r, names)]
    out["a4"] = (sum(1 for _, ok in checks if ok), len(checks))
    fails: dict[str, int] = defaultdict(int)
    for name, ok in checks:
        if not ok:
            fails[name.split(":")[0]] += 1
    out["a4_fail_by"] = dict(fails)
    # 核验状态分布
    st: dict[str, int] = defaultdict(int)
    for _, it in delivered:
        st[it.verification.status.value] += 1
    out["status"] = dict(st)
    # D / E
    firsts = [r.first_item_s for r in recs if r.first_item_s is not None]
    e2e = [r.e2e_s for r in recs if r.e2e_s is not None and r.status == "succeeded"]
    out["d2"] = (percentile(firsts, 0.5), percentile(firsts, 0.95))
    out["d3"] = (percentile(e2e, 0.5), percentile(e2e, 0.95))
    costs = [r.cost for r in recs if r.cost is not None]
    n_del = max(1, len(delivered))
    out["cost_total"] = sum(costs) if costs else None
    out["cost_per_item"] = (sum(costs) / n_del) if costs else None
    out["calls_per_item"] = sum(r.calls for r in recs) / n_del
    attempts = [ir.attempts for r in recs for ir in r.items if ir.attempts]
    out["attempts_mean"] = statistics.fmean(attempts) if attempts else None
    out["dropped"] = sum(1 for r in recs for ir in r.items if ir.item is None)
    first_fail: dict[str, int] = defaultdict(int)
    n_first = 0
    for r in recs:
        for ir in r.items:
            if ir.attempts:
                n_first += 1
                for name in (x.split(":")[0] for x in (ir.failed_checks[0] if ir.failed_checks else [])):
                    first_fail[name] += 1
    out["first_pass"] = (n_first - sum(1 for r in recs for ir in r.items if ir.failed_checks), n_first)
    out["first_fail_by"] = dict(first_fail)
    return out


def _opt(x: float | None, digits: int) -> str:
    return "—" if x is None else f"{x:.{digits}f}"


def render_score(title: str, s: dict[str, Any]) -> str:
    f = _ci
    d2, d3 = s["d2"], s["d3"]
    fmt = lambda x: "—" if x is None else f"{x:.1f}s"  # noqa: E731
    lines = [
        f"### {title}",
        "",
        f"用例 {s['n_cases']}（运行失败 {s['case_failed']}）；要求 {s['n_requested']} 道，交付 {s['n_delivered']} 道，完成审计 {s['n_audited']} 道；"
        f"丢弃 {s['dropped']} 道；核验状态：{s['status']}",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| F1 运行成功率 | {f(s['n_cases'] - s['case_failed'], s['n_cases'])} |",
        f"| F6 trace 完整度 | {f(*s['f6']) if s['f6'][1] else '—'} |",
        f"| **题量达成率**（交付 / 要求） | {f(*s['fill'])} |",
        f"| **A1 可直接使用率**（交付题） | {f(*s['a1'])} |",
        f"| A1（按要求题量，丢弃算失败） | {f(*s['a1_per_requested'])} |",
        f"| **A2(a)** 已核验题答案正确率 | {f(*s['a2_verified'])}；其中错 {s['a2_wrong_verified']} |",
        f"| **A2(b)** 全部交付题答案正确率 | {f(*s['a2_all'])}；错 {s['a2_wrong_all']}，争议 {s['a2_disputed']} |",
        f"| **A3** 审计判超纲（out） | {f(*s['a3_out'])}；borderline {s['a3_borderline'][0]} |",
        f"| A4 需求满足度 | {f(*s['a4'])}；未满足项 {s['a4_fail_by']} |",
        f"| A5(a) 与教材示例雷同 | {f(*s['a5a'])} |",
        f"| A5(b) 套内重复 | {f(*s['a5b'])} |",
        f"| A5(c) 综合题真综合 | {f(*s['a5c'])} |",
        "| A6 题面质量（逐项通过） | " + "；".join(f"{k} {f(*v)}" for k, v in s["a6"].items()) + " |",
        f"| D2 首个已核验题 p50 / p95 | {fmt(d2[0])} / {fmt(d2[1])} |",
        f"| D3 端到端 p50 / p95 | {fmt(d3[0])} / {fmt(d3[1])} |",
        f"| E1 单题成本 / 总成本（元） | {_opt(s['cost_per_item'], 3)} / {_opt(s['cost_total'], 2)} |",
        f"| E3 每道交付题的模型调用数 | {s['calls_per_item']:.1f} |",
        f"| E4 平均写题次数（含修复） | {_opt(s['attempts_mean'], 2)}；一次通过率 {f(*s['first_pass'])}；首次被拦的检查 {s['first_fail_by']} |",
    ]
    if s.get("issues"):
        lines += ["", "**审计发现的问题题目（判错 / 争议 / 超纲）**", ""]
        lines += [f"- {x}" for x in s["issues"][:30]]
    if s.get("quality_issues"):
        lines += ["", "**审计判官认为题面 / 解析有问题的题**", ""]
        lines += [f"- {x}" for x in s["quality_issues"][:20]]
    return "\n".join(lines)


def dump_records(recs: list[CaseRec]) -> str:
    """把一次评测的原始产出写成 JSON（便于复盘；入 tmp 或报告目录由调用方决定）。"""
    rows = []
    for r in recs:
        rows.append(
            {
                "case": r.case.id,
                "status": r.status,
                "error": r.error,
                "lesson": r.lesson_id,
                "items": [
                    {
                        "stem": ir.item.stem,
                        "options": ir.item.options,
                        "answer": ir.item.answer,
                        "solution": ir.item.solution,
                        "status": ir.item.verification.status.value,
                        "kps": ir.item.kp_ids,
                        "tier": ir.item.tier.value,
                        "kind": ir.item.kind.value,
                    }
                    if ir.item
                    else {"dropped": ir.dropped_reason}
                    for ir in r.items
                ],
            }
        )
    return json.dumps(rows, ensure_ascii=False, indent=1)

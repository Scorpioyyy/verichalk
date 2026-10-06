"""拍照出题的端到端评测（eval/specs/perceive.md P8 / P9）：照片 + 一句话 → 整条主管线。

检查：①P9 Brief 符合预期（题量、题型与来源、年级来源、范围来自照片且不追问）；②P8 防雷同：交付的题与照片里的题不是翻版
（确定性：`copy_score`）；③范围相关：交付题的知识点与该页的标注知识点有交集；④交付题数。
识别用 `perceive` 阶段（含确认检查点：评测里自动选"没问题"）。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..core.config import Settings
from ..core.ids import new_id
from ..domain.brief import Origin
from ..domain.events import ItemDelivered, UnderstandingReady
from ..domain.run import Attachment
from ..orchestrator import build_container
from ..orchestrator.pipelines import TurnInput, main_pipeline
from ..stages import RunContext
from ..store import Store
from ..trace import MemorySink, Tracer, use_tracer
from ..verify.checks import NOVELTY_COPY, copy_score
from .perceive_eval import GoldPage, llm_usage

COPY_REPORT = 0.6  # 报告里单列"接近翻版"的阈值（低于拦截阈值，看有多少题贴着线）


@dataclass
class PhotoCaseResult:
    id: str
    page: str
    text: str
    reply: str = ""
    delivered: int = 0
    count: int | None = None
    checks: dict[str, bool] = field(default_factory=dict)
    copies: int = 0  # 与照片里的题相似度 ≥ 拦截阈值的交付题数（P8）
    near: int = 0  # ≥ COPY_REPORT
    max_copy: float = 0.0
    in_scope: int = 0
    clarified: bool = False
    ms: float = 0.0
    cost: float = 0.0
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(self.checks.values()) and not self.problems


def load_photo_cases(path: Path) -> list[dict[str, Any]]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def check_brief(expect: dict[str, Any], brief: Any, clarified: bool) -> dict[str, bool]:
    """P9：Brief 与期望逐项比对。"""
    out: dict[str, bool] = {}
    if "count" in expect:
        out["count"] = bool(brief.count) and brief.count.value == expect["count"]
    if "count_between" in expect:
        lo, hi = expect["count_between"]
        out["count"] = bool(brief.count) and lo <= brief.count.value <= hi
    if "kinds" in expect:
        out["kinds"] = bool(brief.kinds) and [k.value for k in brief.kinds.value] == expect["kinds"]
    if "kinds_origin" in expect:
        out["kinds_origin"] = bool(brief.kinds) and brief.kinds.origin.value == expect["kinds_origin"]
    if "grade" in expect:
        out["grade"] = bool(brief.scope.grade) and brief.scope.grade.value == expect["grade"]
    if "grade_origin" in expect:
        out["grade_origin"] = (
            bool(brief.scope.grade) and brief.scope.grade.origin.value == expect["grade_origin"]
        )
    if expect.get("scope_from_photo"):
        out["scope_from_photo"] = (
            not clarified and bool(brief.scope.kp_ids) and brief.scope.kp_ids.origin == Origin.inferred
        )
    if "difficulty_min" in expect:
        out["difficulty"] = bool(brief.difficulty) and brief.difficulty.value[0] >= expect["difficulty_min"]
    return out


async def run_photo_cases(
    settings: Settings,
    cases: list[dict[str, Any]],
    gold: dict[str, GoldPage],
    photos_dir: Path,
    concurrency: int = 2,
) -> list[PhotoCaseResult]:
    c = await build_container(settings, store=await Store.open(":memory:"))
    sem = asyncio.Semaphore(concurrency)

    async def one(case: dict[str, Any]) -> PhotoCaseResult:
        async with sem:
            res = PhotoCaseResult(case["id"], case["page"], case.get("text", ""))
            ses = await c.store.sessions.create(case["id"])
            asked: list[str] = []

            async def ask(kind, prompt, options, payload):  # type: ignore[no-untyped-def]
                asked.append(kind)
                return {"option": "confirm", "items": []}  # 评测里自动确认识别结果；澄清则按"你来定"

            ctx = RunContext(
                run_id=new_id("run"),
                session_id=ses.id,
                settings=settings,
                llm=c.llm,
                kb=c.kb,
                store=c.store,
                ask_fn=ask,
            )
            path = photos_dir / f"{case['page']}.jpg"
            att = Attachment(
                id=f"att_{case['id']}",
                filename=path.name,
                mime="image/jpeg",
                size=path.stat().st_size,
                sha256="",
                session_id=ses.id,
                path=str(path.resolve()),
                ts=time.time(),
            )
            sink = MemorySink()
            t0 = time.perf_counter()
            try:
                async with use_tracer(Tracer(ctx.run_id, sink)):
                    out = await main_pipeline(ctx, TurnInput(text=case.get("text", ""), attachments=[att]))
                res.reply = out.reply_text
            except Exception as e:
                res.problems.append(f"崩溃：{type(e).__name__}: {str(e)[:150]}")
            res.ms = (time.perf_counter() - t0) * 1000
            res.cost, _ = llm_usage(sink)
            res.clarified = "clarify" in asked
            ur = next((e for e in sink.events if isinstance(e, UnderstandingReady)), None)
            items = [e.item for e in sink.events if isinstance(e, ItemDelivered)]
            res.delivered = len(items)
            if ur is None or ur.understanding.brief is None:
                res.problems.append("没有产出 Brief")
                return res
            brief = ur.understanding.brief
            res.count = brief.count.value if brief.count else None
            res.checks = check_brief(case["expect"], brief, res.clarified)
            refs = [r.text for r in brief.references]
            for it in items:
                sc = max((copy_score(it.stem, r) for r in refs), default=0.0)
                res.max_copy = max(res.max_copy, sc)
                res.copies += sc >= NOVELTY_COPY
                res.near += sc >= COPY_REPORT
            g = gold.get(case["page"])
            if g and items:
                names = set()
                for it in items:
                    for ref in await c.kb.refs(it.kp_ids):
                        names.add((it.id, ref.name))
                ok_ids = {i for i, n in names if n in g.kp}
                res.in_scope = len(ok_ids)
            if res.count and res.delivered < max(1, round(res.count * 0.8)):
                res.problems.append(f"只交付了 {res.delivered}/{res.count} 道")
            return res

    try:
        return list(await asyncio.gather(*(one(x) for x in cases)))
    finally:
        await c.close()


def summarize(rs: list[PhotoCaseResult]) -> dict[str, float | int]:
    n_items = sum(r.delivered for r in rs)
    expected = sum(r.count or 0 for r in rs)
    brief_checks = [v for r in rs for v in r.checks.values()]
    return {
        "cases": len(rs),
        "p9": sum(brief_checks) / len(brief_checks) if brief_checks else 1.0,
        "p9_cases": sum(all(r.checks.values()) for r in rs) / len(rs),
        "delivered_rate": n_items / expected if expected else 0.0,
        "copy_rate": sum(r.copies for r in rs) / n_items if n_items else 0.0,
        "near_rate": sum(r.near for r in rs) / n_items if n_items else 0.0,
        "in_scope": sum(r.in_scope for r in rs) / n_items if n_items else 0.0,
        "clarified": sum(r.clarified for r in rs),
        "cost": sum(r.cost for r in rs),
        "p50_s": sorted(r.ms for r in rs)[len(rs) // 2] / 1000 if rs else 0.0,
    }


def render_report(rs: list[PhotoCaseResult], title: str) -> str:
    s = summarize(rs)
    L = [
        f"# 拍照出题端到端：{title}",
        "",
        "| 指标 | 值 | 阈值 |",
        "|---|---|---|",
        f"| **P9** Brief 逐项符合预期（项 / 用例） | {s['p9']:.3f} / {s['p9_cases']:.3f} | ≥ 0.90 |",
        f"| **P8** 交付题与照片里的题相似度 ≥ {NOVELTY_COPY}（翻版率） | {s['copy_rate']:.3f} | ≤ 0.02 |",
        f"| 接近翻版（≥ {COPY_REPORT}） | {s['near_rate']:.3f} | 报告 |",
        f"| 交付题与该页知识点有交集 | {s['in_scope']:.3f} | 报告 |",
        f"| 交付率（交付 / 要求题量） | {s['delivered_rate']:.3f} | ≥ 0.95 |",
        f"| 触发澄清的用例 | {s['clarified']} | 0（照片已给范围） |",
        f"| 端到端 p50 / 总成本 | {s['p50_s']:.0f}s / ¥{s['cost']:.2f} | — |",
        "",
        "## 逐例",
        "",
        "| 用例 | 页 | 要求 | 题量 | 交付 | Brief 检查 | 翻版 / 接近 / 最大相似 | 秒 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rs:
        bad = [k for k, v in r.checks.items() if not v]
        L.append(
            f"| {r.id} | {r.page} | {r.text or '（只发照片）'} | {r.count} | {r.delivered} | "
            f"{'✅' if not bad else '❌ ' + '、'.join(bad)} | {r.copies} / {r.near} / {r.max_copy:.2f} | {r.ms / 1000:.0f} |"
        )
    probs = [r for r in rs if r.problems]
    if probs:
        L += ["", "## 问题", ""]
        L += [f"- {r.id}：{'；'.join(r.problems)}" for r in probs]
    return "\n".join(L)

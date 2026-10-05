"""追问评测（eval/specs/edit.md，指标 S7）：在固定试卷上回答教师的提问，用与回答模型不同源的判官检查
①回答了问题并满足该用例的期望；②与题目给出的答案 / 解析不矛盾（编造、改答案算矛盾）。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from ..core.config import Settings
from ..core.ids import new_id
from ..domain.llm import ChatMessage, Role
from ..domain.paper import Paper, Revision
from ..llm import LLMRequest, complete_json
from ..orchestrator import build_container
from ..stages import RunContext, run_stage
from ..stages.answer import AnswerIn, AnswerStage
from ..store import Store
from ..trace import MemorySink, Tracer, use_tracer

_SYSTEM = (
    "你是小学数学教研员，检查一段对教师提问的回答。给你试卷里的题目（含已核验的答案与解析）、教师的问题、回答、以及这个回答应当做到的事。"
    '只输出 JSON：{"answered": true/false, "consistent": true/false, "reason": "一句话"}。'
    'answered：回答是否满足"应当做到"。consistent：回答里的答案、数值、结论是否与题目给出的答案和解析一致（教师问错时，回答纠正教师是一致的；'
    "回答编造题目没有的信息、或改了答案，算不一致）。"
)


class _Verdict(BaseModel):
    answered: bool
    consistent: bool
    reason: str = ""


@dataclass
class AskResult:
    id: str
    q: str
    reply: str = ""
    answered: bool = False
    consistent: bool = False
    reason: str = ""
    ms: float = 0.0
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.answered and self.consistent


def load_ask_cases(path: Path) -> list[dict[str, Any]]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


async def run_ask_cases(
    settings: Settings, cases: list[dict[str, Any]], paper: Paper, concurrency: int = 4
) -> list[AskResult]:
    c = await build_container(settings, store=await Store.open(":memory:"))
    sem = asyncio.Semaphore(concurrency)
    items_text = "\n".join(
        f"第 {i} 题：{it.stem}｜答案：{it.answer}｜解析：{it.solution}"
        for i, it in enumerate(paper.all_items(), 1)
    )

    async def one(case: dict[str, Any]) -> AskResult:
        async with sem:
            res = AskResult(case["id"], case["q"])
            ses = await c.store.sessions.create(case["id"])
            await c.store.papers.save(
                ses.id,
                paper.model_copy(deep=True),
                Revision(paper_id=paper.id, rev=1, author="agent", ts=time.time()),
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
                    out = await run_stage(ctx, AnswerStage(), AnswerIn(question=case["q"]))
                    res.reply = out.reply
                    user = f"试卷：\n{items_text}\n\n教师的问题：{case['q']}\n应当做到：{case['expect']}\n\n回答：\n{out.reply}"
                    v, _ = await complete_json(
                        ctx.llm,
                        LLMRequest(
                            role=Role.judge,
                            messages=[
                                ChatMessage(role="system", content=_SYSTEM),
                                ChatMessage(role="user", content=user),
                            ],
                            purpose="eval.ask.judge",
                            max_tokens=300,
                        ),
                        _Verdict,
                    )
                    res.answered, res.consistent, res.reason = v.answered, v.consistent, v.reason
            except Exception as e:
                res.problems.append(f"崩溃：{type(e).__name__}: {str(e)[:150]}")
            res.ms = (time.perf_counter() - t0) * 1000
            return res

    try:
        return list(await asyncio.gather(*(one(x) for x in cases)))
    finally:
        await c.close()


def render_ask_report(results: list[AskResult], title: str) -> str:
    n = len(results)
    ok = sum(r.ok for r in results)
    contradict = sum(not r.consistent for r in results)
    lines = [
        f"# 追问评测（S7）：{title}",
        "",
        "| 指标 | 值 | 阈值 | 判定 |",
        "|---|---|---|---|",
        f"| S7 回答了问题且与题目一致 | {ok / n:.3f}（{ok}/{n}） | ≥ 0.90 | {'✅' if ok / n >= 0.9 else '❌'} |",
        f"| 与题目答案矛盾 | {contradict / n:.3f}（{contradict}/{n}） | ≤ 0.02 | {'✅' if contradict / n <= 0.02 else '❌'} |",
        "",
    ]
    bad = [r for r in results if not r.ok]
    if bad:
        lines += ["## 失败明细", ""]
        for r in bad:
            lines.append(f"- **{r.id}**「{r.q}」：{r.reason or '；'.join(r.problems)}\n  > {r.reply[:160]}")
        lines.append("")
    lines += ["## 全部", "", "| 用例 | 问题 | 结果 | 秒 |", "|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r.id} | {r.q} | {'✅' if r.ok else '❌'} | {r.ms / 1000:.1f} |")
    return "\n".join(lines) + "\n"

"""手改后复核评测（eval/specs/edit.md 指标 C2 与 `edit.review` 的消融）。

在固定试卷上做 20 次手动编辑：10 次正确的（改措辞、换数据且答案同步更新、选项换序且字母同步……），10 次引入错误的
（答案改错、改了数据没改答案、条件被删……）。复核应当：对错误的编辑给出"需复核"（不是"已校对"），对正确的编辑给出"已校对"，
并在几秒内更新状态（C2）。`--off edit.review` 时没有复核，状态停在"待核验"——这就是该模块的贡献。
"""

from __future__ import annotations

import asyncio
import statistics
import time
from dataclasses import dataclass, field

from ..core.config import Settings
from ..domain.paper import Paper, Revision, VerifyStatus
from ..orchestrator import build_container
from ..store import Store

OK = "ok"  # 正确的编辑：应得到"已校对"
BAD = "bad"  # 引入错误的编辑：应被标成"需复核"

# (id, 类型, 题, 字段 -> 新值, 说明)
EDITS: list[tuple[str, str, str, dict[str, object], str]] = [
    (
        "ok-1",
        OK,
        "q6",
        {"stem": "小明买了 $3$ 本练习本，每本 $2.5$ 元，他付给收银员 $10$ 元，应找回多少元？"},
        "只换了人名",
    ),
    ("ok-2", OK, "q3", {"answer": "9.0"}, "答案写成 9.0（数值相同）"),
    (
        "ok-3",
        OK,
        "q8",
        {"solution": "把五次成绩加起来：$102+98+110+105+100=515$，再除以 $5$，得到 $103$（个）。"},
        "解析改写，结论不变",
    ),
    (
        "ok-4",
        OK,
        "q1",
        {"options": ["30", "3", "0.3", "3.5"], "answer": "B", "solution": "$0.6\\times5=3$，所以选 B。"},
        "选项换序，答案字母与解析同步",
    ),
    (
        "ok-5",
        OK,
        "q7",
        {"stem": "三角形的两个内角分别是 $65^\\circ$ 和 $48^\\circ$，求第三个内角的度数。"},
        "题干换了说法，数据不变",
    ),
    (
        "ok-6",
        OK,
        "q2",
        {"stem": "任意三角形的三个内角的和都是 $180^\\circ$。（　）"},
        "判断题换说法，答案仍是对",
    ),
    (
        "ok-7",
        OK,
        "q6",
        {
            "stem": "小红买了 $3$ 本练习本，每本 $1.5$ 元，她付给收银员 $10$ 元，应找回多少元？",
            "answer": "5.5 元",
            "answer_value": None,
            "solution": "$3$ 本练习本共 $1.5\\times3=4.5$（元），应找回 $10-4.5=5.5$（元）。",
        },
        "数据改了，答案与解析同步",
    ),
    (
        "ok-8",
        OK,
        "q3",
        {
            "stem": "每支铅笔 $2$ 元，买 $5$ 支一共需要 ____ 元。",
            "answer": "10",
            "answer_value": None,
            "solution": "$2\\times5=10$（元）。",
        },
        "数据改了，答案与解析同步",
    ),
    ("ok-9", OK, "q4", {"stem": "计算：$12.6-3.45+0.8=$____（写出结果）"}, "题干补了几个字"),
    (
        "ok-10",
        OK,
        "q5",
        {"solution": "先把 $-4.5$ 移到右边：$3x=7.5+4.5=12$，再除以 $3$，$x=4$。"},
        "解析改写，结论不变",
    ),
    ("bad-1", BAD, "q6", {"answer": "3.5 元", "answer_value": None}, "答案改错"),
    ("bad-2", BAD, "q4", {"answer": "9.85", "answer_value": None}, "答案改错"),
    ("bad-3", BAD, "q1", {"answer": "B"}, "字母改错（B 是 0.3）"),
    (
        "bad-4",
        BAD,
        "q7",
        {"stem": "一个三角形的两个内角分别是 $75^\\circ$ 和 $48^\\circ$，第三个内角是多少度？"},
        "改了数据没改答案（应是 57）",
    ),
    (
        "bad-5",
        BAD,
        "q8",
        {"stem": "小明 5 次跳绳的成绩分别是 $102$、$98$、$110$、$105$ 个，他平均每次跳多少个？"},
        "删了一个数据，答案没改",
    ),
    ("bad-6", BAD, "q3", {"answer": "8", "answer_value": None}, "答案改错"),
    ("bad-7", BAD, "q2", {"answer": "错"}, "判断题答案改错"),
    ("bad-8", BAD, "q5", {"stem": "解方程：$3x-4.5=9.5$，$x=$____"}, "改了方程没改答案（x 不是 4）"),
    ("bad-9", BAD, "q6", {"answer": "2 元", "answer_value": None}, "答案改错"),
    ("bad-10", BAD, "q4", {"stem": "计算：$12.6-3.45-0.8=$____"}, "改了运算符号没改答案（应是 8.35）"),
]


@dataclass
class ReviewRow:
    id: str
    kind: str
    note: str
    status: str = ""
    why: str = ""  # 非通过的检查（诊断误报 / 漏报用）
    ms: float = 0.0
    problems: list[str] = field(default_factory=list)

    @property
    def correct(self) -> bool:
        """复核的结论对：正确的编辑 → 已校对 / 已核验；错误的编辑 → 需复核（不是校对通过）。"""
        if self.kind == OK:
            return self.status in (VerifyStatus.checked.value, VerifyStatus.verified.value)
        return self.status in (VerifyStatus.needs_review.value, VerifyStatus.rejected.value)


async def run_review_eval(settings: Settings, paper: Paper, concurrency: int = 6) -> list[ReviewRow]:
    c = await build_container(settings, store=await Store.open(":memory:"))
    sem = asyncio.Semaphore(concurrency)

    async def one(spec: tuple[str, str, str, dict[str, object], str]) -> ReviewRow:
        eid, kind, qid, fields, note = spec
        row = ReviewRow(eid, kind, note)
        async with sem:
            ses = await c.store.sessions.create(eid)
            await c.store.papers.save(
                ses.id,
                paper.model_copy(deep=True),
                Revision(paper_id=paper.id, rev=1, author="agent", ts=time.time()),
            )
            t0 = time.perf_counter()
            try:
                ops = [
                    {"op": "replace_field", "item_id": qid, "field": k, "value": v} for k, v in fields.items()
                ]
                out = await c.papers.edit(ses.id, ops)
                if out.review_run_id:
                    await c.manager.wait(out.review_run_id, 120)
                row.ms = (time.perf_counter() - t0) * 1000
                p = await c.store.papers.get_current(ses.id)
                found = p.find_item(qid) if p else None
                row.status = found[2].verification.status.value if found else "missing"
                if found:
                    row.why = "；".join(
                        f"{ck.name}:{ck.detail[:70]}"
                        for ck in found[2].verification.checks
                        if ck.status.value != "pass"
                    )
            except Exception as e:
                row.problems.append(f"崩溃：{type(e).__name__}: {str(e)[:150]}")
        return row

    try:
        return list(await asyncio.gather(*(one(s) for s in EDITS)))
    finally:
        await c.close()


def render_review_report(rows: list[ReviewRow], title: str, review_on: bool) -> str:
    ok_rows = [r for r in rows if r.kind == OK]
    bad_rows = [r for r in rows if r.kind == BAD]
    detect = sum(r.correct for r in bad_rows) / len(bad_rows)
    pass_ok = sum(r.correct for r in ok_rows) / len(ok_rows)
    ms = sorted(r.ms for r in rows if r.status and r.status != "pending")
    lines = [
        f"# 手改复核评测（C2 与 edit.review 消融）：{title}",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| 错误编辑被标成需复核（检出率） | {detect:.2f}（{sum(r.correct for r in bad_rows)}/{len(bad_rows)}） |",
        f"| 正确编辑得到已校对（无误报） | {pass_ok:.2f}（{sum(r.correct for r in ok_rows)}/{len(ok_rows)}） |",
    ]
    if ms:
        lines.append(
            f"| 复核时延 p50 / p95（秒，含模型调用） | {statistics.median(ms) / 1000:.1f} / {ms[int(0.95 * (len(ms) - 1))] / 1000:.1f}（C2 目标 p95 ≤ 3s） |"
        )
    lines.append(f"| 复核开关 | {'开启' if review_on else '关闭（状态停在待核验）'} |")
    lines += [
        "",
        "| 编辑 | 类型 | 说明 | 复核后状态 | 结论 | 秒 | 未通过的检查 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r.id} | {'正确' if r.kind == OK else '错误'} | {r.note} | {r.status or '（崩溃）'} | {'✅' if r.correct else '❌'} | {r.ms / 1000:.1f} | {r.why} |"
        )
    return "\n".join(lines) + "\n"

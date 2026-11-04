"""教师视角的可用性评审（北极星 A1 的代理，eval/CHANGELOG.md 2026-11-04）。

与审计里的题面质量判官（逐题、看不到教师的要求）不同，这里**带着教师的原话、一次看整套题**，判断每道题教师会不会原样使用：
是否贴合要求（知识点、综合要求、难度、情境）、是否是一道完整自然的题（而不是把互不相关的小问拼在一起）、
数据与情境是否合理、与同套其他题是否雷同。答案对错不在这里判（那是 A2 审计的事），但明显算错的也要标出。

标签：`good` 可直接用且不错；`ok` 可直接用、有小瑕疵；`bad` 教师不会原样使用。可用 = good ∪ ok。
评审模型与写题模型不同厂商；用前须与人工标注（`eval/annotation/teacher_accept/`）校准。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from ..core.errors import VerichalkError
from ..domain.llm import ChatMessage, Role
from ..llm import LLMGateway, LLMRequest, complete_json

JUDGE = {"model": "deepseek-v4-pro", "thinking": False, "temperature": 0.0, "max_tokens": 1800}

REASONS = {
    "off_request": "不符合教师要求（知识点、综合要求、题型、情境、年级）",
    "stitched": "把互不相关的几个小问拼成一道题",
    "contrived": "情境或问法牵强、生硬",
    "implausible": "数据或情境不合常理",
    "duplicate": "与同套其他题的结构或考法雷同",
    "too_easy": "明显低于教师要求的难度",
    "too_hard": "明显超出该年级学生的能力",
    "unclear": "表述不清、有歧义或信息冗杂",
    "wrong": "答案或解析明显有错",
}

SYSTEM = (
    """你是一位有二十年经验的小学数学老师。一位同事用出题工具出了一套题，请你**站在要把这套题发给自己学生的老师的角度**，逐题判断你会不会**原样使用**。

先读老师的要求，再读整套题。逐题给出：
- `label`：`good`（会直接用，而且是好题）／`ok`（可以直接用，只有小瑕疵）／`bad`（不会原样用：要改或者要删）。
- `reasons`：问题代码（可多个，没有问题就留空），只能从下面选：
"""
    + "\n".join(f"  - `{k}`：{v}" for k, v in REASONS.items())
    + """
- `note`：一句话说明（为什么好 / 哪里不行）。

判断要点（按重要性）：
1. **贴合要求**：老师明说的知识点、要“结合 / 综合”的内容、题型、情境、难度，这道题是否做到了？做不到就是 `off_request`，判 `bad`。
   老师要“A 和 B 结合”而题里只用到其中一个，算没做到；老师点名三个主题时，结合其中任意两个就算做到。老师没提的方面不要苛求。
2. **一道完整自然的题**：几个小问如果围绕同一组数据、同一个情境，彼此相关，是正常的多问题；如果是互不相关的独立问题（如先问长度、再问一个无关的时间换算），才是 `stitched`，判 `bad`。
3. **情境与数据真实**：价格、速度、长度、人数等是否符合生活常识；情境与问题是否自然贴合，而不是为了凑知识点硬编。
4. **整套的多样性**：与同套前面的题只是换了数字或换了名字的同一道题，是 `duplicate`——只标后出现的那一道。
5. 表述清楚、适合该年级学生阅读；难度与要求相符（`too_hard` 只用于明显超出该年级能力的题）。

不要因为题目普通就判 `bad`：常规但扎实、贴合要求的题是 `ok` 或 `good`。只有老师确实会改或删的才是 `bad`。

只输出 JSON：`{"items": [{"index": 1, "label": "good", "reasons": [], "note": "…"}]}`，覆盖每一道题，`index` 与题号一致。"""
)


class ItemVerdict(BaseModel):
    index: int
    label: str
    reasons: list[str] = Field(default_factory=list)
    note: str = ""


class SetVerdict(BaseModel):
    items: list[ItemVerdict]


@dataclass
class JudgedItem:
    request: str
    index: int
    label: str
    reasons: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def usable(self) -> bool:
        return self.label in ("good", "ok")


def render_items(rows: list[dict[str, Any]]) -> str:
    out = []
    for r in rows:
        opts = (
            "\n   选项：" + " | ".join(f"{'ABCD'[j]}. {o}" for j, o in enumerate(r.get("options") or []))
            if r.get("options")
            else ""
        )
        figs = "".join(f"\n   （图：{f['text']}）" for f in r.get("figures") or [])
        out.append(
            f"第 {r['index']} 题：{r['stem']}{figs}{opts}\n   答案：{r.get('answer', '')}\n   解析：{r.get('solution', '')}"
        )
    return "\n\n".join(out)


async def judge_set(gw: LLMGateway, request: str, rows: list[dict[str, Any]]) -> list[JudgedItem]:
    """评一套题（同一个请求的全部交付题）；评审失败时返回空列表（调用方按"未评"处理，不计入分母）。"""
    if not rows:
        return []
    user = f"老师的要求：{request}\n\n这套题（共 {len(rows)} 道）：\n\n{render_items(rows)}"
    try:
        v, _ = await complete_json(
            gw,
            LLMRequest(
                role=Role.judge,
                purpose="teacher_judge",
                messages=[ChatMessage(role="system", content=SYSTEM), ChatMessage(role="user", content=user)],
            ),
            SetVerdict,
        )
    except VerichalkError:
        return []
    by = {x.index: x for x in v.items}
    out = []
    for r in rows:
        x = by.get(r["index"])
        if x is None or x.label not in ("good", "ok", "bad"):
            continue
        out.append(
            JudgedItem(request, r["index"], x.label, [c for c in x.reasons if c in REASONS], x.note.strip())
        )
    return out


async def judge_all(gw: LLMGateway, sets: dict[str, list[dict[str, Any]]]) -> list[JudgedItem]:
    res = await asyncio.gather(*(judge_set(gw, req, rows) for req, rows in sets.items()))
    return [j for r in res for j in r]

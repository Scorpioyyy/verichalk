"""教师偏好盲评：同一请求下，VeriChalk 的一套题 vs 通用大模型直出的一套题，哪套更适合发给学生。

这是"好题"的直接度量（A8）：不看答案对错（那由 A2 管），看思维含量、情境、多样性、是否针对易错点、是否贴合教师要求。
做法：不告诉评审哪套来自哪里，每个用例交换顺序评两次（消除位置偏好），两次一致才算胜负，不一致记平局。评审模型与写题模型不同厂商。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from pydantic import BaseModel

from ..core.errors import VerichalkError
from ..domain.llm import ChatMessage, Role
from ..domain.paper import Item
from ..llm import LLMGateway, LLMRequest, complete_json
from ..metrics.stats import wilson

JUDGE = {"model": "deepseek-v4-pro", "thinking": False, "temperature": 0.0, "max_tokens": 700}

SYSTEM = """你是一位资深的小学数学教研员，要在两份练习里选出**更适合发给学生的那一份**。教师的要求会给出。

评判标准（不看答案对错，那由别人核对）：
1. **思维含量**：题目是否需要判断、比较、取舍、多步推理，而不是套一步公式；难度是否名副其实。
2. **情境与数据**：情境是否真实、具体、有趣，数据是否合理可信；是否千篇一律（总是“小明买东西”）。
3. **多样性**：一份练习里的题在结构、情境、考法上是否各不相同。
4. **针对易错点**：是否有意让学生容易犯的错误暴露出来（位数不同的小数、单位不统一、含 0 的退位……）。
5. **贴合要求**：是否符合教师说的年级、知识点、题型、情境、难度、综合性要求；综合题是否真的把几个知识点织在一起。

两份练习的题数可能不同，按“每道题的质量与整份的设计”评，不要因为题多而偏向。
只输出 JSON：`{"winner": "A" 或 "B" 或 "tie", "reason": "一两句话"}`。"""


class Verdict(BaseModel):
    winner: str
    reason: str = ""


@dataclass
class PrefRow:
    case_id: str
    request: str
    result: str  # win / loss / tie（相对 VeriChalk）
    reason: str = ""


def render_set(items: list[Item], n: int) -> str:
    lines = []
    for i, it in enumerate(items[:n], 1):
        opts = (
            (" 选项：" + " | ".join(f"{'ABCD'[j]}.{o}" for j, o in enumerate(it.options)))
            if it.options
            else ""
        )
        lines.append(f"{i}. {it.stem}{opts}")
    return "\n".join(lines)


async def compare(
    gw: LLMGateway, request: str, mine: list[Item], theirs: list[Item], n: int
) -> tuple[str, str]:
    """返回（相对 mine 的结果 win/loss/tie, 理由）。两个顺序都评，一致才定胜负。"""

    async def ask(a: list[Item], b: list[Item]) -> Verdict | None:
        user = f"教师的要求：{request}\n\n【练习 A】\n{render_set(a, n)}\n\n【练习 B】\n{render_set(b, n)}"
        try:
            v, _ = await complete_json(
                gw,
                LLMRequest(
                    role=Role.judge,
                    purpose="preference",
                    messages=[
                        ChatMessage(role="system", content=SYSTEM),
                        ChatMessage(role="user", content=user),
                    ],
                ),
                Verdict,
            )
            return v
        except VerichalkError:
            return None

    v1, v2 = await asyncio.gather(ask(mine, theirs), ask(theirs, mine))
    if v1 is None or v2 is None:
        return "tie", "评审不可用"
    w1 = {"A": "win", "B": "loss"}.get(v1.winner.strip().upper(), "tie")
    w2 = {"B": "win", "A": "loss"}.get(v2.winner.strip().upper(), "tie")
    if w1 == w2:
        return w1, v1.reason
    return "tie", f"两个顺序结论不一致（{v1.reason[:40]} / {v2.reason[:40]}）"


def summarize(rows: list[PrefRow]) -> str:
    n = len(rows)
    win = sum(1 for r in rows if r.result == "win")
    loss = sum(1 for r in rows if r.result == "loss")
    tie = n - win - loss
    decided = win + loss
    lo, hi = wilson(win, decided) if decided else (0.0, 1.0)
    lines = [
        "| 指标 | 值 |",
        "|---|---|",
        f"| **A8 教师偏好**：VeriChalk 胜 / 平 / 负 | {win} / {tie} / {loss}（共 {n} 个请求） |",
        f"| 有胜负的请求里 VeriChalk 胜率 | {win / decided:.2f} [{lo:.2f},{hi:.2f}]（n={decided}） |"
        if decided
        else "| 有胜负的请求里 VeriChalk 胜率 | — |",
        "",
        "**判负的请求（VeriChalk 输在哪）**",
        "",
    ]
    lines += [f"- 「{r.request}」：{r.reason[:140]}" for r in rows if r.result == "loss"][:12] or ["- 无"]
    return "\n".join(lines)

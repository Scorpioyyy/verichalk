"""对比不同模型"直接出题"（无知识库、无核验）的答案错误率：回答"别的模型也会出错吗"。

同一批教师请求，每个模型配置各出一遍（答案用结构化数组，对基线已经很宽松），再由两个不同厂商的思考模型独立解题审计：
两者一致且与题目答案不同 → 错；两者分歧 → 争议；其余 → 对。结果写入报告，并列出错题示例。
用法：python scripts/naive_models.py [--n-requests 12]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend" / "src"))
warnings.simplefilter("ignore")

from pydantic import BaseModel
from verichalk.core.config import LLMMode, Profile, Settings
from verichalk.core.errors import VerichalkError
from verichalk.domain.blueprint import AnswerPart
from verichalk.domain.llm import ChatMessage, Role
from verichalk.eval.audit import AUDITOR_A, AUDITOR_B
from verichalk.eval.cases import load_cases
from verichalk.llm import LLMRequest, build_gateway, complete_json
from verichalk.verify import VerifyEnv, VerifyInput, answers_equal
from verichalk.verify.checks import check_blind

CONFIGS = [
    ("qwen3.8-max", False),
    ("qwen3.8-max", True),
    ("qwen3.7-plus", False),
    ("qwen3.8-flash", False),
    ("deepseek-v4.1-flash", False),
    ("deepseek-v4.1-flash", True),
]
# 偏重计算的请求（容易暴露算错），取自 produce 用例集的 val 划分与我另外补的几条
EXTRA = [
    "四年级，出5道小数加减混合运算的购物应用题，有一定难度",
    "五年级上册小数除法，出5道，难一点",
    "六年级分数混合运算，出4道",
    "三年级两位数乘两位数的应用题，出5道",
]
SYSTEM = (
    "你是小学数学命题老师。按教师的要求出题，每题给出题面（stem）、最终答案（answers，数组，题目问几问就给几个值；"
    "数值只写数字，选择题写选项字母，判断题写“对”或“错”）、简要解析（solution）。"
    '只输出 JSON：{"items": [{"stem": "", "answers": [""], "solution": ""}]}。'
)


class Item(BaseModel):
    stem: str
    answers: list[str]
    solution: str = ""


class Out(BaseModel):
    items: list[Item]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-requests", type=int, default=12)
    args = ap.parse_args()
    s = Settings(
        profile=Profile.intl,
        llm_mode=LLMMode.record,
        cassette_namespace="naive_models",
        llm_concurrency=12,
    )
    cases = load_cases(s.root_dir / "eval" / "datasets", "produce", "val")
    reqs = [c.turns[0].user for c in cases if "template" not in c.tags][
        : args.n_requests - len(EXTRA)
    ] + EXTRA
    reqs = reqs[: args.n_requests]
    gw_a, gw_b = build_gateway(s), build_gateway(s)
    gw_a.registry = gw_a.registry.override("solver", **AUDITOR_A)
    gw_b.registry = gw_b.registry.override("solver", **AUDITOR_B)
    sem = asyncio.Semaphore(10)

    async def audit(stem: str, answers: list[str]) -> tuple[str, list[str], list[str]]:
        inp = VerifyInput(
            kind="application",
            stem=stem,
            answers=[AnswerPart(value=a) for a in answers],
        )
        async with sem:
            ra, rb = await asyncio.gather(
                check_blind(VerifyEnv(llm=gw_a, kb=None), inp),  # type: ignore[arg-type]
                check_blind(VerifyEnv(llm=gw_b, kb=None), inp),  # type: ignore[arg-type]
            )
        a, b = list(ra.evidence.get("blind", [])), list(rb.evidence.get("blind", []))
        if ra.status.value == "skip" or rb.status.value == "skip":
            return "unavailable", a, b
        ok_a, ok_b = answers_equal(a, answers), answers_equal(b, answers)
        if ok_a and ok_b:
            return "correct", a, b
        if not ok_a and not ok_b and answers_equal(a, b):
            return "wrong", a, b
        return "disputed", a, b

    async def run_config(model: str, think: bool) -> dict:
        gw = build_gateway(s)
        gw.registry = gw.registry.override(
            Role.smart,
            model=model,
            thinking=think,
            max_tokens=8000 if think else 3500,
            temperature=0.7,
        )

        async def one(text: str):
            async with sem:
                t = time.time()
                try:
                    out, res = await complete_json(
                        gw,
                        LLMRequest(
                            role=Role.smart,
                            purpose="naive.write",
                            messages=[
                                ChatMessage(role="system", content=SYSTEM),
                                ChatMessage(role="user", content=text),
                            ],
                        ),
                        Out,
                    )
                except VerichalkError:
                    return text, [], 0.0, 0.0
                return text, out.items, time.time() - t, res.cost or 0.0

        gen = await asyncio.gather(*(one(t) for t in reqs))
        flat = [(text, it) for text, items, _, _ in gen for it in items if it.answers]
        verdicts = await asyncio.gather(*(audit(it.stem, it.answers) for _, it in flat))
        wrong = [
            (t, it, a, b)
            for (t, it), (v, a, b) in zip(flat, verdicts, strict=True)
            if v == "wrong"
        ]
        return {
            "model": model + (" 思考" if think else ""),
            "n": len(flat),
            "wrong": len(wrong),
            "disputed": sum(1 for v, *_ in verdicts if v == "disputed"),
            "latency": sum(g[2] for g in gen) / max(len(gen), 1),
            "cost": sum(g[3] for g in gen),
            "examples": wrong[:3],
        }

    rows = await asyncio.gather(*(run_config(m, t) for m, t in CONFIGS))
    lines = [
        f"### 不同模型直接出题（无核验）的答案错误率：{len(reqs)} 个请求",
        "",
        "| 模型 | 题数 | 审计判错 | 争议 | 请求平均耗时 | 出题成本（元） |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['model']} | {r['n']} | {r['wrong']} ({r['wrong'] / max(r['n'], 1):.1%}) | {r['disputed']} | {r['latency']:.1f}s | {r['cost']:.3f} |"
        )
    lines += ["", "**错题示例**", ""]
    for r in rows:
        for text, it, a, b in r["examples"]:
            lines.append(
                f"- {r['model']}｜请求「{text}」｜题：{it.stem[:120]}｜给的答案 {it.answers}｜审计 A={a} B={b}"
            )
    out = (
        ROOT / "eval" / "reports" / f"naive_models_{time.strftime('%Y%m%d-%H%M%S')}.md"
    )
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"报告：{out}")


asyncio.run(main())

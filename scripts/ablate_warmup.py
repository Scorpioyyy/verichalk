"""预热消融实验（D28）：预热到底能为"用户的第一次请求"省多少时间 / 成本？

三种条件（每次试验都用全新的 HTTP 连接，并在提示词前缀前加随机盐，保证服务端前缀缓存必然是冷的）：
  A cold       什么都不做，直接发真实请求；
  B conn-warm  先发一个无关的极小请求（只建立连接），再发真实请求；
  C full-warm  先对**同一个前缀**发 max_tokens=1 的请求（建立连接 + 写入前缀缓存），再发真实请求。
两种前缀长度：~3k token 与 ~12k token（后者更接近最终的提示词：知识库指南 + 边界约束 + 教材示例）。
指标：真实请求的 TTFT 与总时长（毫秒）、命中的缓存 token 数、成本。每个格子重复 N 次，条件随机交错。

用法：python scripts/ablate_warmup.py [--profile intl] [--n 5]
"""
from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend" / "src"))

from verichalk.core.config import LLMMode, Profile, Settings  # noqa: E402
from verichalk.domain.llm import ChatMessage, Role  # noqa: E402
from verichalk.llm import LLMRequest, build_gateway  # noqa: E402

RULE = "第{i}条：题目必须符合北师大版课程标准，数值精确，不得出现超纲概念，答案唯一，情境真实，语言适合小学生。"
QUESTION = "请用不超过三十个字，说明三角形内角和是多少度。"


def prefix(salt: str, n_rules: int) -> str:
    return f"[{salt}] 你是小学数学命题助手。规则：" + "".join(RULE.format(i=i) for i in range(n_rules))


async def trial(profile: Profile, cond: str, n_rules: int) -> dict:
    s = Settings(profile=profile, llm_mode=LLMMode.live, llm_max_retries=1)
    gw = build_gateway(s)  # 全新的传输层 → 全新的连接池 / TLS
    gw.registry = gw.registry.override(Role.fast, max_tokens=60, thinking=False)
    system = prefix(uuid.uuid4().hex, n_rules)
    try:
        if cond == "B conn-warm":
            await gw.complete(LLMRequest(role=Role.fast, purpose="ablate-ping", max_tokens=1,
                                         messages=[ChatMessage(role="user", content="ping")]))
        elif cond == "C full-warm":
            await gw.complete(LLMRequest(role=Role.fast, purpose="ablate-warm", max_tokens=1,
                                         messages=[ChatMessage(role="system", content=system),
                                                   ChatMessage(role="user", content="ping")]))
        if cond != "A cold":
            await asyncio.sleep(0.5)  # 模拟"页面打开 → 用户输入完成"之间的间隔
        t0 = time.perf_counter()
        r = await gw.complete(LLMRequest(role=Role.fast, purpose="ablate-real", messages=[
            ChatMessage(role="system", content=system), ChatMessage(role="user", content=QUESTION)]))
        wall = (time.perf_counter() - t0) * 1000
        return {"cond": cond, "rules": n_rules, "ttft": r.ttft_ms, "total": r.total_ms, "wall": wall,
                "cached": r.usage.cached_tokens, "prompt": r.usage.prompt_tokens, "cost": r.cost}
    finally:
        if gw._transport is not None:  # noqa: SLF001
            await gw._transport.aclose()  # noqa: SLF001


def med(xs: list[float]) -> float:
    return statistics.median(xs)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="intl", choices=["cn", "intl"])
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--conds", default="A,B,C", help="参与的条件首字母，如 A,C")
    ap.add_argument("--tag", default="", help="报告文件名后缀")
    args = ap.parse_args()
    sizes = {"~3k token": 95, "~12k token": 380}
    conds = [c for c in ("A cold", "B conn-warm", "C full-warm") if c[0] in args.conds.split(",")]
    plan = [(c, n) for c in conds for n in sizes.values() for _ in range(args.n)]
    random.Random(7).shuffle(plan)
    rows: list[dict] = []
    for cond, n_rules in plan:
        try:
            rows.append(await trial(Profile(args.profile), cond, n_rules))
        except Exception as e:  # noqa: BLE001  实验脚本：失败的试验记录后跳过
            print("trial failed:", type(e).__name__, file=sys.stderr)
    L = [f"# 预热消融实验（profile = {args.profile}，每格 {args.n} 次，{time.strftime('%Y-%m-%d')}）", "",
         "每次试验使用全新连接与带随机盐的前缀（服务端缓存必然为冷）。表中为**真实请求**的指标，中位数（最小～最大）。", "",
         "| 前缀 | 条件 | TTFT (ms) | 总时长 (ms) | 命中缓存 / 输入 token | 成本（元） |", "|---|---|---|---|---|---|"]
    for label, n_rules in sizes.items():
        for cond in conds:
            xs = [r for r in rows if r["cond"] == cond and r["rules"] == n_rules]
            if not xs:
                continue
            tt = [r["ttft"] for r in xs if r["ttft"] is not None]
            tot = [r["total"] for r in xs]
            costs = [r["cost"] for r in xs if r["cost"] is not None]
            L.append(f"| {label} | {cond} | {med(tt):.0f}（{min(tt):.0f}～{max(tt):.0f}） | {med(tot):.0f}（{min(tot):.0f}～{max(tot):.0f}） | "
                     f"{med([r['cached'] for r in xs]):.0f} / {med([r['prompt'] for r in xs]):.0f} | "
                     f"{(med(costs) if costs else float('nan')):.6f} |")
    out = ROOT / "eval" / "reports" / f"ablation_warmup_{args.profile}{args.tag}.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    asyncio.run(main())

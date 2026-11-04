"""教师视角评审（北极星代理）：对 probe_produce.py 的产出逐套评审，报告按要求题数的可用率与问题分布。

用法：
  python scripts/judge_sets.py a.json [b.json …]                       # 每份产出一行汇总
  python scripts/judge_sets.py a.json --show                          # 逐题打印评审意见（读题用）
  python scripts/judge_sets.py --calibrate eval/annotation/teacher_accept/calibration.yaml   # 与人工标注比一致率
评审用 deepseek-v4-pro（与写题模型不同厂商），一套题一次调用；花费每套约 ¥0.01。
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import sys
import warnings
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend" / "src"))
warnings.simplefilter("ignore")

from verichalk.core.config import LLMMode, Profile, Settings
from verichalk.eval.teacher_judge import JUDGE, JudgedItem, judge_all
from verichalk.llm import build_gateway
from verichalk.metrics.stats import wilson


def load_sets(path: str) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    """probe 产出：{请求: [题]}，另有 `__asked__`：{请求: 要求题数}（旧产出没有时按交付数计）。"""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    asked = raw.pop("__asked__", {})
    return raw, {req: asked.get(req, len(rows)) for req, rows in raw.items()}


def summarize(name: str, judged: list[JudgedItem], asked: dict[str, int]) -> str:
    n_asked = sum(asked.values())
    usable = sum(1 for j in judged if j.usable)
    good = sum(1 for j in judged if j.label == "good")
    reasons = collections.Counter(c for j in judged if not j.usable for c in j.reasons)
    lo, hi = wilson(usable, n_asked)
    return (
        f"{name}: 可用/要求 {usable}/{n_asked} = {usable / max(n_asked, 1):.3f} [{lo:.2f},{hi:.2f}]"
        f"；good {good}；评审 {len(judged)} 道；bad 的原因 {dict(reasons.most_common())}"
    )


def calibrate(path: str, judged: list[JudgedItem]) -> str:
    import yaml

    gold = {
        (r["request"], r["index"]): r
        for r in yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    }
    pairs = [
        (gold[(j.request, j.index)], j) for j in judged if (j.request, j.index) in gold
    ]
    n = len(pairs)
    agree = sum(1 for g, j in pairs if (g["label"] != "bad") == j.usable)
    # 二分类（可用 / 不可用）的 Cohen's κ
    p_g = sum(1 for g, _ in pairs if g["label"] != "bad") / max(n, 1)
    p_j = sum(1 for _, j in pairs if j.usable) / max(n, 1)
    pe = p_g * p_j + (1 - p_g) * (1 - p_j)
    po = agree / max(n, 1)
    kappa = (po - pe) / (1 - pe) if pe < 1 else 1.0
    lines = [f"校准：{n} 道，可用与否一致 {agree}/{n} = {po:.2f}，κ = {kappa:.2f}"]
    for g, j in pairs:
        if (g["label"] != "bad") != j.usable:
            lines.append(
                f"  ✗ 人工 {g['label']} {g['reasons']} / 评审 {j.label} {j.reasons}：{g['stem'][:60]}… ｜ {j.note}"
            )
    return "\n".join(lines)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--calibrate", default="")
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()
    gw = build_gateway(Settings(profile=Profile.intl, llm_mode=LLMMode.live))
    gw.registry = gw.registry.override("judge", **JUDGE)
    if args.calibrate:
        import yaml

        rows = yaml.safe_load(Path(args.calibrate).read_text(encoding="utf-8"))
        sets: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            sets.setdefault(r["request"], []).append(r)
        judged = await judge_all(gw, sets)
        print(calibrate(args.calibrate, judged))
    for f in args.files:
        sets, asked = load_sets(f)
        judged = await judge_all(gw, sets)
        if args.show:
            for j in judged:
                row = next(r for r in sets[j.request] if r["index"] == j.index)
                print(f"[{j.label}] {j.reasons} {row['stem'][:70]}… ｜ {j.note}")
        print(summarize(f, judged, asked))


asyncio.run(main())

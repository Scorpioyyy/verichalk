"""两套出题结果的盲评对比：同一批请求下，A 套 vs B 套哪套更适合发给学生（不看对错，看思维含量 / 情境 / 多样性 / 易错点 / 贴合要求）。

用法：
  python scripts/probe_produce.py --file eval/datasets/quality_probe.yaml --out a.json --quiet      # 一种策略的产出
  python scripts/compare_sets.py a.json b.json                                                    # 对比两种策略
评审用 deepseek-v4-pro（与写题模型不同厂商），每个请求交换顺序评两次，一致才算胜负。花费：每对请求约 ¥0.01。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend" / "src"))
warnings.simplefilter("ignore")

from verichalk.core.config import LLMMode, Profile, Settings
from verichalk.domain.paper import Item, ItemKind
from verichalk.eval.preference import JUDGE, PrefRow, compare, summarize
from verichalk.llm import build_gateway


def to_items(rows: list[dict]) -> list[Item]:
    return [
        Item(
            id=f"x{i}",
            kind=ItemKind.application,
            stem=r["stem"],
            options=r.get("options") or [],
            answer=r.get("answer", ""),
        )
        for i, r in enumerate(rows)
    ]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    args = ap.parse_args()
    a = json.loads(Path(args.a).read_text(encoding="utf-8"))
    b = json.loads(Path(args.b).read_text(encoding="utf-8"))
    gw = build_gateway(Settings(profile=Profile.intl, llm_mode=LLMMode.live))
    gw.registry = gw.registry.override("judge", **JUDGE)

    async def one(req: str) -> PrefRow | None:
        if req not in b or not a[req] or not b[req]:
            return None
        n = max(len(a[req]), len(b[req]))
        res, why = await compare(gw, req, to_items(a[req]), to_items(b[req]), n)
        return PrefRow(req, req, res, why)

    rows = [r for r in await asyncio.gather(*(one(r) for r in a)) if r]
    print(
        f"A = {args.a}（交付 {sum(len(v) for v in a.values())} 道）  B = {args.b}（交付 {sum(len(v) for v in b.values())} 道）"
    )
    print(
        summarize(rows)
        .replace("VeriChalk 胜", "A 胜")
        .replace("VeriChalk 输在哪", "A 输在哪")
    )


asyncio.run(main())

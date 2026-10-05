"""统计录制文件（eval/cassettes）里累计的模型花费：按命名空间与模型汇总，价格取 config/pricing.yaml（缺价格的模型单列 token）。

用法：python scripts/spend.py
注意：录制只在 `record` 模式下写入；命中录制回放的调用不会产生费用，也不会重复计入。
"""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend" / "src"))

from verichalk.domain.llm import Usage
from verichalk.llm.registry import PriceTable


def main() -> None:
    prices = PriceTable.load(ROOT / "config")
    by_ns: dict[str, float] = collections.defaultdict(float)
    by_model: dict[str, list[float]] = collections.defaultdict(
        lambda: [0.0, 0, 0, 0]
    )  # 元, 调用, 输入token, 输出token
    unpriced: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0, 0])
    for f in (ROOT / "eval" / "cassettes").glob("*/*.json"):
        if f.parent.name == "chalkbase_embeddings":
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        u = d.get("usage") or {}
        usage = Usage(
            prompt_tokens=u.get("prompt_tokens", 0),
            completion_tokens=u.get("completion_tokens", 0),
            cached_tokens=u.get("cached_tokens", 0),
        )
        c = prices.cost(d.get("model", ""), usage)
        m = d.get("model", "?")
        if c is None:
            x = unpriced[m]
            x[0] += 1
            x[1] += usage.prompt_tokens
            x[2] += usage.completion_tokens
            continue
        by_ns[f.parent.name] += c
        r = by_model[m]
        r[0] += c
        r[1] += 1
        r[2] += usage.prompt_tokens
        r[3] += usage.completion_tokens
    print("按命名空间（元）：")
    for k, v in sorted(by_ns.items(), key=lambda kv: -kv[1]):
        print(f"  {k:22s} {v:8.2f}")
    print("按模型：")
    for k, v in sorted(by_model.items(), key=lambda kv: -kv[1][0]):
        print(
            f"  {k:24s} ¥{v[0]:7.2f}  调用 {int(v[1]):5d}  输入 {int(v[2]) / 1e6:5.2f}M  输出 {int(v[3]) / 1e6:5.2f}M"
        )
    print(f"已定价合计：¥{sum(by_ns.values()):.2f}")
    if unpriced:
        print("价格表里没有的模型（只列用量，成本未计入）：")
        for k, v in unpriced.items():
            print(
                f"  {k:24s} 调用 {v[0]:5d}  输入 {v[1] / 1e6:5.2f}M  输出 {v[2] / 1e6:5.2f}M"
            )


main()

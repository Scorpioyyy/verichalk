"""探针：服务端隐式前缀缓存能存活多久？决定预热的有效期 `warmup_ttl_s`。

对一组带随机盐的前缀同时写入缓存，然后分别在 30s / 120s / 300s / 600s 后各发一次请求，看命中的缓存 token 数。
用法：python scripts/probe_cache_ttl.py [--profile intl]    （总耗时约 11 分钟）
"""

from __future__ import annotations

import argparse
import asyncio
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
GAPS = [30, 120, 300, 600]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="intl", choices=["cn", "intl"])
    args = ap.parse_args()
    gw = build_gateway(Settings(profile=Profile(args.profile), llm_mode=LLMMode.live))
    gw.registry = gw.registry.override(Role.fast, max_tokens=5, thinking=False)
    salts = {g: uuid.uuid4().hex for g in GAPS}

    def req(salt: str, user: str) -> LLMRequest:
        system = f"[{salt}] 你是小学数学命题助手。规则：" + "".join(
            RULE.format(i=i) for i in range(95)
        )
        return LLMRequest(
            role=Role.fast,
            purpose="ttl-probe",
            messages=[
                ChatMessage(role="system", content=system),
                ChatMessage(role="user", content=user),
            ],
        )

    t0 = time.monotonic()
    await asyncio.gather(
        *(gw.complete(req(s, "ping")) for s in salts.values())
    )  # 写入缓存
    rows: list[tuple[int, int, int]] = []

    async def probe(gap: int) -> None:
        await asyncio.sleep(max(0.0, gap - (time.monotonic() - t0)))
        r = await gw.complete(req(salts[gap], f"第{gap}秒后的请求"))
        rows.append((gap, r.usage.cached_tokens, r.usage.prompt_tokens))

    await asyncio.gather(*(probe(g) for g in GAPS))
    L = [
        f"# 前缀缓存存活时间探针（profile = {args.profile}，{time.strftime('%Y-%m-%d')}）",
        "",
        "写入缓存后间隔一段时间再发相同前缀的请求，观察命中的 token 数。",
        "",
        "| 间隔 | 命中 / 输入 token |",
        "|---|---|",
    ]
    L += [f"| {g}s | {c} / {p} |" for g, c, p in sorted(rows)]
    out = ROOT / "eval" / "reports" / f"probe_cache_ttl_{args.profile}.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    asyncio.run(main())

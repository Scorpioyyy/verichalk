"""快速诊断：理解 → 规划 → 逐题生成与核验（与线上同一条 `produce_specs`，含"其他题的考法"与丢弃后补题），不审计。

打印每道题被哪项检查拦下、各阶段耗时、首题时间、端到端时长与成本；多个请求并行。

用法：
  python scripts/probe_produce.py "四年级下册小数加减法，出6道" ["另一个请求" ...] [--off flag,flag] [--items]
  python scripts/probe_produce.py --file eval/datasets/quality_probe.yaml --out x.json --llm-conc 32
换模型：--role smart=kimi-k3:notemp ；末尾的 SUMMARY 行便于横向比较。
注意：多个请求并行时共享全局模型并发（`--llm-conc`，默认取设置），并发不够会排队、触发单题时间预算，
交付与时延都会被低估。比较时延要在同一并发条件下进行。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import warnings
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend" / "src"))
warnings.simplefilter("ignore")

from verichalk.core.config import LLMMode, Profile, Settings
from verichalk.domain.events import ItemDelivered, LLMCall
from verichalk.figures import describe_figure
from verichalk.orchestrator import build_container
from verichalk.orchestrator.produce_flow import produce_specs
from verichalk.stages import PlanIn, PlanStage, RunContext
from verichalk.stages.understand import UnderstandIn, UnderstandStage
from verichalk.store import Store
from verichalk.trace import MemorySink, Tracer, use_tracer

COLLECTED: dict[str, list[dict[str, Any]]] = {}
ASKED: dict[str, int] = {}


async def probe(
    c, s: Settings, text: str, verbose: bool, show_items: bool
) -> tuple[list[str], dict[str, Any]]:  # type: ignore[no-untyped-def]
    ctx = RunContext(
        run_id="probe",
        session_id="probe",
        settings=s,
        llm=c.llm,
        kb=c.kb,
        store=c.store,
    )
    sink = MemorySink()
    t0 = time.time()
    stats: dict[str, Any] = {
        "asked": 0,
        "delivered": 0,
        "writes": 0,
        "first_pass": 0,
        "cost": 0.0,
    }
    async with use_tracer(Tracer("probe", sink)):
        u = await UnderstandStage().run(ctx, UnderstandIn(text=text))
        if u.brief is None:
            return [f"\n## {text}\n没有解析出出题需求：{u.route}"], stats
        t_u = time.time()
        bp = await PlanStage().run(ctx, PlanIn(brief=u.brief))
        t_p = time.time()
        outs = await produce_specs(
            ctx, bp, bp.items, u.brief, keep_kinds=u.brief.kinds is not None
        )
    t_end = time.time()
    first = min(
        (e.ts for e in sink.events if isinstance(e, ItemDelivered)), default=None
    )
    calls = [e.record for e in sink.events if isinstance(e, LLMCall)]
    cost = sum(r.cost or 0.0 for r in calls)
    stats.update(
        asked=len(bp.items),
        delivered=sum(1 for o in outs if o.item),
        writes=sum(o.attempts for o in outs),
        first_pass=sum(1 for o in outs if o.item and o.attempts == 1),
        cost=cost,
        e2e=t_end - t0,
        first_item=(first - t0) if first else None,
        n_calls=len(calls),
    )
    head = (
        f"\n## {text}\n理解 {t_u - t0:.1f}s · 规划 {t_p - t_u:.1f}s · 生成 {t_end - t_p:.1f}s · "
        f"首题 {stats['first_item'] or float('nan'):.1f}s · 端到端 {stats['e2e']:.1f}s · ¥{cost:.3f} · 调用 {len(calls)}"
    )
    lines = [head]
    for spec, o in zip(bp.items, outs, strict=True):
        msg = (
            f"- 第{spec.index}题 {spec.tier.value} {spec.kind.value} {spec.kp_names} 写{o.attempts}次"
            + ("" if o.item else " ✗丢弃")
        )
        if verbose or o.failed_checks or not o.item:
            msg += f" 被拦:{o.failed_checks} {o.dropped_reason[:120]}"
        lines.append(msg)
    by: dict[str, list[float]] = {}
    for r in calls:
        by.setdefault(r.purpose, []).append(r.total_ms / 1000)
    lines.append(
        "  "
        + "；".join(
            f"{k} n={len(v)} 均{sum(v) / len(v):.1f}s" for k, v in sorted(by.items())
        )
    )
    rows = []
    for spec, o in zip(bp.items, outs, strict=True):
        if not o.item:
            continue
        it = o.item
        rows.append(
            {
                "index": spec.index,
                "tier": it.tier.value,
                "kind": it.kind.value,
                "kps": spec.kp_names,
                "scene": spec.scene,
                "angle": spec.angle,
                "stem": it.stem,
                "figures": [{"kind": f.kind, "params": f.params, "text": describe_figure(f)} for f in it.figures],
                "options": it.options,
                "answer": it.answer,
                "solution": it.solution,
                "status": it.verification.status.value,
                "attempts": o.attempts,
            }
        )
        if show_items:
            lines.append(
                f"  【第{spec.index}题·{it.tier.value}·{it.kind.value}·{spec.scene}】{it.stem}"
            )
            if it.options:
                lines.append(
                    "    选项："
                    + " | ".join(f"{'ABCD'[i]}.{x}" for i, x in enumerate(it.options))
                )
            for f in it.figures:
                lines.append(f"    图：{describe_figure(f)}")
            lines.append(f"    答案：{it.answer}")
    COLLECTED[text] = rows
    ASKED[text] = len(bp.items)
    return lines, stats


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("texts", nargs="*")
    ap.add_argument("--off", default="")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--items", action="store_true", help="打印交付的题目全文（读题用）")
    ap.add_argument(
        "--file",
        default="",
        help="从 YAML 列表读取请求（如 eval/datasets/quality_probe.yaml）",
    )
    ap.add_argument(
        "--out",
        default="",
        help="把交付的题写成 JSON（{请求: [题]}），供 compare_sets.py 盲评对比",
    )
    ap.add_argument(
        "--role", action="append", default=[], help="角色覆盖，如 smart=kimi-k3:notemp"
    )
    ap.add_argument(
        "--conc", type=int, default=0, help="每个请求的逐题并发（默认取设置）"
    )
    ap.add_argument(
        "--llm-conc",
        type=int,
        default=0,
        help="全局模型并发（默认取设置）；并行多个请求时调大，避免排队",
    )
    ap.add_argument("--profile", default="intl", choices=["cn", "intl"])
    args = ap.parse_args()
    if args.file:
        import yaml

        args.texts = args.texts + yaml.safe_load(
            Path(args.file).read_text(encoding="utf-8")
        )
    upd: dict[str, Any] = {}
    if args.conc:
        upd["item_concurrency"] = args.conc
    if args.llm_conc:
        upd["llm_concurrency"] = args.llm_conc
    s = Settings(
        profile=Profile(args.profile),
        llm_mode=LLMMode.live,
        off=args.off,
        **upd,
    )
    c = await build_container(s, store=await Store.open(":memory:"))
    from verichalk.eval.__main__ import apply_overrides, parse_role_overrides

    apply_overrides(c.llm, parse_role_overrides(args.role))
    results = await asyncio.gather(
        *(probe(c, s, t, not args.quiet, args.items) for t in args.texts)
    )
    stats = [st for _, st in results if st.get("asked")]
    for lines, _ in results:
        print("\n".join(lines), flush=True)
    if args.out:
        Path(args.out).write_text(
            json.dumps({**COLLECTED, "__asked__": ASKED}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )

    def tot(k: str) -> float:
        return sum(st[k] for st in stats)

    e2e = [st["e2e"] for st in stats]
    firsts = [st["first_item"] for st in stats if st.get("first_item")]
    asked = max(tot("asked"), 1)
    print(
        f"SUMMARY 关闭={args.off or '无'} smart={c.llm.registry.role('smart').model} 题数={int(tot('asked'))} "
        f"交付={int(tot('delivered'))}（{tot('delivered') / asked:.2f}） 一次通过={int(tot('first_pass'))} "
        f"写题={int(tot('writes'))}（均 {tot('writes') / asked:.2f}） 首题 p50={statistics.median(firsts) if firsts else float('nan'):.0f}s "
        f"端到端 p50={statistics.median(e2e) if e2e else float('nan'):.0f}s max={max(e2e, default=0):.0f}s "
        f"成本 ¥{tot('cost'):.3f}（每交付题 ¥{tot('cost') / max(tot('delivered'), 1):.4f}）",
        flush=True,
    )
    await c.close()


asyncio.run(main())

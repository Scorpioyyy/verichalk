"""快速诊断：只跑创作阶段（不审计），打印每次写题被哪项检查拦下及耗时。多个请求并行，一轮约一分钟。

用法：
  python scripts/probe_produce.py "四年级下册小数加减法，出6道" ["另一个请求" ...] [--off flag,flag] [--n 8] [--show choice]
换模型：--role smart=kimi-k3:notemp ；末尾的 SUMMARY 行便于横向比较。
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

from verichalk.core.config import LLMMode, Profile, Settings
from verichalk.domain.events import LLMCall
from verichalk.orchestrator import build_container
from verichalk.stages import (
    PlanIn,
    PlanStage,
    ProduceIn,
    ProduceStage,
    RunContext,
)
from verichalk.stages.understand import UnderstandIn, UnderstandStage
from verichalk.store import Store
from verichalk.trace import MemorySink, Tracer, use_tracer

SHOW: set[str] = set()
COLLECTED: dict[str, list[dict]] = {}
_orig_write = ProduceStage._write


async def _traced_write(self, ctx, inp, details, examples, repair):  # type: ignore[no-untyped-def]
    w = await _orig_write(self, ctx, inp, details, examples, repair)
    if inp.spec.kind.value in SHOW:
        tag = "·修复" if repair else ""
        print(
            f"[写题 第{inp.spec.index}题{tag}] {w.stem[:80]!r} 选项={w.options} 答案={[a.value for a in w.answers]}",
            flush=True,
        )
        print(f"    程序: {w.solver_code[:220]!r}", flush=True)
    return w


ProduceStage._write = _traced_write  # type: ignore[method-assign]


async def probe(
    c, s: Settings, text: str, n: int, verbose: bool, show_items: bool = False
):  # type: ignore[no-untyped-def]
    """返回（输出行, (要求题数, 交付题数, 写题总次数, 一次通过题数, 创作阶段墙钟秒））。"""
    ctx = RunContext(
        run_id="probe",
        session_id="probe",
        settings=s,
        llm=c.llm,
        kb=c.kb,
        store=c.store,
    )
    sink = MemorySink()
    async with use_tracer(Tracer("probe", sink)):
        u = await UnderstandStage().run(ctx, UnderstandIn(text=text))
        if u.brief is None:
            return [f"\n## {text}\n没有解析出出题需求：{u.route}"], (0, 0, 0, 0, 0.0)
        bp = await PlanStage().run(ctx, PlanIn(brief=u.brief))
        specs = bp.items[: n or None]
        t0 = time.time()
        sem = asyncio.Semaphore(s.item_concurrency)

        async def one(spec):  # type: ignore[no-untyped-def]
            async with sem:
                t = time.time()
                out = await ProduceStage().run(
                    ctx, ProduceIn(spec=spec, grade=bp.grade, lesson_id=bp.lesson_id)
                )
                return spec, out, time.time() - t

        res = await asyncio.gather(*(one(sp) for sp in specs))
    wall = time.time() - t0
    lines = [f"\n## {text}\n创作阶段 {wall:.1f}s"]
    for spec, o, dt in res:
        msg = f"- 第{spec.index}题 {spec.tier.value} {spec.kind.value} {spec.kp_names} {dt:.1f}s 写{o.attempts}次"
        lines.append(
            msg + f" 被拦:{o.failed_checks} {o.dropped_reason[:120]}"
            if verbose or o.failed_checks
            else msg
        )
    by: dict[str, list[float]] = {}
    for e in sink.events:
        if isinstance(e, LLMCall):
            by.setdefault(e.record.purpose, []).append(e.record.total_ms / 1000)
    lines.append(
        "  "
        + "；".join(
            f"{k} n={len(v)} 均{sum(v) / len(v):.1f}s" for k, v in sorted(by.items())
        )
    )
    if show_items:
        for spec, o, _ in res:
            if o.item:
                it = o.item
                lines.append(
                    f"  【第{spec.index}题·{spec.tier.value}·{spec.kind.value}·{spec.scene}】{it.stem}"
                )
                if it.options:
                    lines.append(
                        "    选项："
                        + " | ".join(
                            f"{'ABCD'[i]}.{x}" for i, x in enumerate(it.options)
                        )
                    )
                lines.append(f"    答案：{it.answer}")
    COLLECTED[text] = [
        {
            "stem": o.item.stem,
            "options": o.item.options,
            "answer": o.item.answer,
            "solution": o.item.solution,
            "tier": o.item.tier.value,
        }
        for _, o, _ in res
        if o.item
    ]
    delivered = sum(1 for _, o, _ in res if o.item)
    attempts = sum(o.attempts for _, o, _ in res)
    first = sum(1 for _, o, _ in res if o.item and o.attempts == 1)
    return lines, (len(res), delivered, attempts, first, wall)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("texts", nargs="*")
    ap.add_argument("--off", default="")
    ap.add_argument("--on", default="")
    ap.add_argument("--pipeline", default="classic", choices=["classic", "design"], help="链路预设（默认 classic）")
    ap.add_argument("--n", type=int, default=0, help="每个请求只跑前 n 道")
    ap.add_argument("--show", default="", help="打印这些题型的每次写题内容，如 choice")
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
        help="把交付的题写成 JSON（{请求: [题]}），供 scripts/compare_sets.py 盲评对比",
    )
    ap.add_argument(
        "--role", action="append", default=[], help="角色覆盖，如 smart=kimi-k3:notemp"
    )
    ap.add_argument(
        "--conc", type=int, default=0, help="逐题并发（并发限制严的模型用 1～2）"
    )
    args = ap.parse_args()
    if args.file:
        import yaml

        args.texts = args.texts + yaml.safe_load(
            Path(args.file).read_text(encoding="utf-8")
        )
    SHOW.update(x for x in args.show.split(",") if x)
    s = Settings(profile=Profile.intl, llm_mode=LLMMode.live, off=args.off, on=args.on, pipeline=args.pipeline)
    if args.conc:
        s = s.model_copy(update={"item_concurrency": args.conc})
    c = await build_container(s, store=await Store.open(":memory:"))
    from verichalk.eval.__main__ import apply_overrides, parse_role_overrides

    apply_overrides(c.llm, parse_role_overrides(args.role))
    results = await asyncio.gather(
        *(probe(c, s, t, args.n, not args.quiet, args.items) for t in args.texts)
    )
    tot = [0, 0, 0, 0, 0.0]
    for lines, r in results:
        print("\n".join(lines), flush=True)
        tot = [a + b for a, b in zip(tot, r, strict=True)]
    if args.out:
        import json

        Path(args.out).write_text(
            json.dumps(COLLECTED, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    model = c.llm.registry.role("smart").model
    print(
        f"SUMMARY smart={model} 题数={tot[0]} 交付={tot[1]} 写题次数={tot[2]}（平均 {tot[2] / max(tot[0], 1):.2f}） "
        f"一次通过={tot[3]}/{tot[0]}",
        flush=True,
    )
    await c.close()


asyncio.run(main())

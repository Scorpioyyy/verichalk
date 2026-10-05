"""评测命令行：

python -m verichalk.eval run --suite smoke --split val --mode replay
python -m verichalk.eval run --suite smoke --mode live --record
python -m verichalk.eval compare --base <id> --new <id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from ..core.config import LLMMode, Profile, Settings
from ..core.logging import setup_logging
from ..llm import ModelRegistry
from .cases import load_cases
from .report import write_report
from .runner import run_suite


def _history(out_dir: Path) -> dict[str, dict]:
    f = out_dir / "history.jsonl"
    if not f.exists():
        return {}
    return {
        r["id"]: r for r in (json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip())
    }


def cmd_run(args: argparse.Namespace) -> int:
    mode = LLMMode.record if args.record else LLMMode(args.mode)
    settings = Settings(profile=Profile(args.profile), llm_mode=mode, cassette_namespace=args.suite)
    root = settings.root_dir
    cases = load_cases(root / "eval" / "datasets", args.suite, args.split)
    if args.limit:
        cases = cases[: args.limit]
    models = ModelRegistry.load(settings.config_dir, settings.profile).models()
    result = asyncio.run(
        run_suite(settings, cases, suite=args.suite, split=args.split, concurrency=args.concurrency)
    )
    path = write_report(result, root / "eval" / "reports", models=models, root=root)
    print(path.read_text(encoding="utf-8"))
    print(f"报告：{path}")
    return 0 if all(c.passed for c in result.cases) else 1


def cmd_compare(args: argparse.Namespace) -> int:
    settings = Settings()
    hist = _history(settings.root_dir / "eval" / "reports")
    a, b = hist.get(args.base), hist.get(args.new)
    if not a or not b:
        print("找不到指定的评测记录；可用：", ", ".join(hist) or "无", file=sys.stderr)
        return 2
    keys = [
        "case_pass",
        "n_cases",
        "success_rate",
        "trace_complete",
        "e2e_p50_ms",
        "e2e_p95_ms",
        "cache_hit_ratio",
        "cost_p50",
    ]
    print(f"{'指标':<18}{args.base:<28}{args.new:<28}变化")
    for k in keys:
        va, vb = a.get(k), b.get(k)
        delta = "" if va is None or vb is None else f"{vb - va:+.4g}"
        print(f"{k:<18}{va!s:<28}{vb!s:<28}{delta}")
    return 0


def main() -> None:
    setup_logging("WARNING")
    ap = argparse.ArgumentParser(prog="verichalk.eval")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--suite", required=True)
    r.add_argument("--split", default="val", choices=["val", "test", "all"])
    r.add_argument("--mode", default="replay", choices=[m.value for m in LLMMode])
    r.add_argument("--record", action="store_true", help="实调模型并录制（等价于 --mode record）")
    r.add_argument("--profile", default="intl", choices=["cn", "intl"])
    r.add_argument("--concurrency", type=int, default=4)
    r.add_argument("--limit", type=int, default=0)
    r.set_defaults(fn=cmd_run)
    c = sub.add_parser("compare")
    c.add_argument("--base", required=True)
    c.add_argument("--new", required=True)
    c.set_defaults(fn=cmd_compare)
    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()

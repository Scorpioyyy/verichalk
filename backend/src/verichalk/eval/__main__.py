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
import time
from pathlib import Path

from ..core.config import LLMMode, Profile, Settings
from ..core.features import REGISTRY
from ..core.logging import setup_logging
from ..knowledge import KnowledgeService
from ..llm import ModelRegistry, build_gateway
from .ablation import render_ablation, run_ablation
from .audit import Auditor, AuditStore
from .cases import load_cases
from .produce_eval import audit_records, dump_records, render_score, run_cases, run_naive, score
from .report import write_report
from .runner import run_suite
from .verify_eval import (
    load_bank,
    load_boundary,
    render_bank,
    render_boundary,
    run_bank,
    run_boundary,
    summarize_bank,
    summarize_boundary,
)


def _history(out_dir: Path) -> dict[str, dict]:
    f = out_dir / "history.jsonl"
    if not f.exists():
        return {}
    return {
        r["id"]: r for r in (json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip())
    }


def _settings(args: argparse.Namespace, mode: LLMMode, off: str) -> Settings:
    # 每个变体都构造全新的 Settings（其中缓存了特性开关的解析结果，不能复用或 model_copy）
    ns = getattr(args, "cassette_ns", None) or args.suite
    return Settings(profile=Profile(args.profile), llm_mode=mode, cassette_namespace=ns, off=off)


def cmd_ablate(args: argparse.Namespace) -> int:
    mode = LLMMode.record if args.record else LLMMode(args.mode)
    base = _settings(args, mode, "")
    root = base.root_dir
    cases = load_cases(root / "eval" / "datasets", args.suite, args.split)
    flags = [f.strip() for f in args.flags.split(",") if f.strip()] or sorted(REGISTRY)
    results = asyncio.run(
        run_ablation(
            lambda off: _settings(args, mode, off),
            cases,
            suite=args.suite,
            split=args.split,
            flags=flags,
            concurrency=args.concurrency,
        )
    )
    md = render_ablation(results)
    out = root / "eval" / "reports" / f"ablation_{args.suite}_{time.strftime('%Y%m%d-%H%M%S')}.md"
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"报告：{out}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    mode = LLMMode.record if args.record else LLMMode(args.mode)
    settings = _settings(args, mode, args.off)
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


def parse_role_overrides(items: list[str]) -> dict[str, dict]:
    """`--role solver=deepseek-v4.1-flash:think` → {"solver": {"model": ..., "thinking": True}}；可附 `:t=0.3`、`:max=4000`。"""
    out: dict[str, dict] = {}
    for it in items:
        role, _, rest = it.partition("=")
        parts = rest.split(":")
        spec: dict = {"model": parts[0]}
        for flag in parts[1:]:
            if flag == "think":
                spec["thinking"] = True
            elif flag == "nothink":
                spec["thinking"] = False
            elif flag == "notemp":
                spec["send_temperature"] = False
            elif flag.startswith("t="):
                spec["temperature"] = float(flag[2:])
            elif flag.startswith("max="):
                spec["max_tokens"] = int(flag[4:])
        out[role] = spec
    return out


def apply_overrides(gw, overrides: dict[str, dict]) -> None:
    for role, spec in overrides.items():
        gw.registry = gw.registry.override(role, **spec)


VERIFY_CHECKS = {
    "program": "produce.program_check",
    "blind": "produce.blind_solve",
    "boundary": "produce.boundary_check",
    "quality": "produce.quality_check",
}


def cmd_verify(args: argparse.Namespace) -> int:
    """在核验评测集上评测验证器：`--bank answers`（V1 / V2 / V5）或 `--bank boundary`（V3 / V4）。

    `--checks blind,quality` 指定启用哪些检查（仅 answers 集），其余关闭；`--role` 临时覆盖角色用的模型。"""
    mode = LLMMode.record if args.record else LLMMode(args.mode)
    default_ns = "verify_bank" if args.bank == "answers" else "verify_boundary"
    settings = Settings(
        profile=Profile(args.profile),
        llm_mode=mode,
        cassette_namespace=args.cassette_ns or default_ns,
        llm_concurrency=args.concurrency,
    )
    root = settings.root_dir
    gw = build_gateway(settings)
    overrides = parse_role_overrides(args.role)
    apply_overrides(gw, overrides)
    role_desc = ", ".join(
        f"{r}={v['model']}{':think' if v.get('thinking') else ''}" for r, v in overrides.items()
    )
    if args.bank == "boundary":
        bitems = load_boundary(root / "eval" / "datasets" / "verify" / "boundary.yaml", args.split)
        if args.limit:
            bitems = bitems[: args.limit]

        async def run_b():
            kb = KnowledgeService.from_settings(settings)
            return await run_boundary(gw, kb, bitems, concurrency=args.concurrency)

        bres = asyncio.run(run_b())
        desc = f"{args.tag or 'boundary'} · split={args.split} · " + (role_desc or "默认角色")
        md = render_boundary(desc, summarize_boundary(bres))
    else:
        items = load_bank(root / "eval" / "datasets" / "verify" / "bank.yaml", args.split)
        if args.sample:
            import random

            items = random.Random(0).sample(items, min(args.sample, len(items)))
        if args.limit:
            items = items[: args.limit]
        on = {c.strip() for c in args.checks.split(",") if c.strip()}
        off = ",".join(flag for name, flag in VERIFY_CHECKS.items() if name not in on)
        results = asyncio.run(run_bank(gw, None, items, off=off, concurrency=args.concurrency))
        desc = f"{args.tag or 'verify'} · checks={args.checks} · split={args.split} · " + (
            role_desc or "默认角色"
        )
        md = render_bank(desc, summarize_bank(results))
    out = (
        root
        / "eval"
        / "reports"
        / f"verify_{args.bank}_{args.tag or 'run'}_{time.strftime('%Y%m%d-%H%M%S')}.md"
    )
    out.write_text(md + "\n", encoding="utf-8")
    print(md)
    print(f"报告：{out}")
    return 0


def cmd_produce(args: argparse.Namespace) -> int:
    """创作与核验的端到端评测：运行 → 审计 → 打分；`--naive` 同时跑朴素直出基线 B0。"""
    mode = LLMMode.record if args.record else LLMMode(args.mode)
    settings = Settings(
        profile=Profile(args.profile),
        llm_mode=mode,
        cassette_namespace=args.cassette_ns or "produce",
        off=args.off,
        on=args.on,
        pipeline=args.pipeline,
        llm_concurrency=args.concurrency * 2,
    )
    root = settings.root_dir
    cases = load_cases(root / "eval" / "datasets", "produce", args.split)
    if args.only:
        wanted = {x.strip() for x in args.only.split(",")}
        cases = [c for c in cases if c.id in wanted]
    if args.limit:
        cases = cases[: args.limit]

    async def main() -> str:
        kb = KnowledgeService.from_settings(settings)
        recs = await run_cases(settings, cases, args.concurrency)
        store = AuditStore(root / "eval" / "audits" / "produce.jsonl")
        auditor = Auditor(settings, kb, store)
        audits = {} if args.no_audit else await audit_records(auditor, kb, recs)
        title = f"{args.tag or 'produce'} · split={args.split} · {settings.features.describe()}"
        parts = [render_score(title, await score(recs, audits, kb))]
        if args.naive:
            nrecs = await run_naive(build_gateway(settings), recs)
            if not args.no_audit:
                naudits = await audit_records(auditor, kb, nrecs)
                parts.append(
                    render_score(
                        "朴素直出基线 B0（smart 单次调用，无知识库、无核验）", await score(nrecs, naudits, kb)
                    )
                )
            # 教师偏好盲评：同一请求下两套题哪套更适合发给学生（不看对错，看思维含量 / 情境 / 多样性 / 易错点 / 贴合要求）
            from .preference import JUDGE as PREF_JUDGE
            from .preference import PrefRow, compare
            from .preference import summarize as pref_summary

            pgw = build_gateway(settings)
            pgw.registry = pgw.registry.override("judge", **PREF_JUDGE)
            by_case = {r.case.id: r for r in nrecs}
            rows: list[PrefRow] = []

            async def one_pref(r):  # type: ignore[no-untyped-def]
                other = by_case.get(r.case.id)
                if other is None or not r.delivered or not other.delivered:
                    return None
                n = max(r.n_requested, 1)
                res, why = await compare(pgw, r.case.turns[0].user, r.delivered, other.delivered, n)
                return PrefRow(r.case.id, r.case.turns[0].user, res, why)

            rows = [x for x in await asyncio.gather(*(one_pref(r) for r in recs)) if x]
            parts.append("### A8 教师偏好盲评（VeriChalk vs 朴素直出）\n\n" + pref_summary(rows))
        if args.dump:
            Path(args.dump).write_text(dump_records(recs), encoding="utf-8")  # noqa: ASYNC240
        return "\n\n".join(parts)

    md = asyncio.run(main())
    out = (
        root
        / "eval"
        / "reports"
        / f"produce_{args.tag or 'run'}_{args.split}_{time.strftime('%Y%m%d-%H%M%S')}.md"
    )
    out.write_text(md + "\n", encoding="utf-8")
    print(md)
    print(f"报告：{out}")
    return 0


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


def cmd_export(args: argparse.Namespace) -> int:
    """导出评测（eval/specs/export.md）：不调用模型，确定、零成本。`--dump DIR` 保存所有产出文件供人工抽检。"""
    from .export_eval import latency, load_export_cases, render_report, run_export_eval, summarize

    settings = Settings()
    root = settings.root_dir
    cases = load_export_cases(root / "eval" / "datasets" / "export" / "papers.yaml")
    if args.only:
        keep = {x.strip() for x in args.only.split(",")}
        cases = [c for c in cases if c.id in keep]
    dump = Path(args.dump) if args.dump else None
    if dump:
        dump.mkdir(parents=True, exist_ok=True)
    font_dirs = (str(settings.font_dir),) if settings.font_dir else ()
    rows = run_export_eval(cases, font_dirs=font_dirs, tex_compile=args.tex_compile, dump_dir=dump)
    md = render_report(rows, summarize(rows), latency(font_dirs=font_dirs), args.tag or "export")
    out = root / "eval" / "reports" / f"export_{args.tag or 'run'}_{time.strftime('%Y%m%d-%H%M%S')}.md"
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"报告：{out}")
    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    """自然语言编辑评测（C1）：固定试卷 + 编辑指令；`--record` 实调模型并录制。"""
    from .edit_eval import load_edit_cases, load_papers, render_edit_report, run_edit_cases

    mode = LLMMode.record if args.record else LLMMode(args.mode)
    settings = Settings(
        profile=Profile(args.profile),
        llm_mode=mode,
        cassette_namespace=args.cassette_ns or "edit",
        off=args.off,
        llm_concurrency=args.concurrency * 2,
    )
    root = settings.root_dir
    base = root / "eval" / "datasets" / "edit"
    cases = load_edit_cases(
        base / ("cases_fresh.yaml" if args.cases == "fresh" else "cases.yaml"), args.split
    )
    if args.only:
        wanted = {x.strip() for x in args.only.split(",")}
        cases = [c for c in cases if c.id in wanted]
    if args.limit:
        cases = cases[: args.limit]
    results = asyncio.run(
        run_edit_cases(settings, cases, load_papers(base / "papers.yaml"), args.concurrency)
    )
    md = render_edit_report(
        results, f"{args.tag or 'edit'} · split={args.split} · {settings.features.describe()}"
    )
    out = root / "eval" / "reports" / f"edit_{args.tag or 'run'}_{time.strftime('%Y%m%d-%H%M%S')}.md"
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"报告：{out}")
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    """追问评测（S7）：固定试卷上回答教师的提问，判官检查回答与一致性。"""
    from .ask_eval import load_ask_cases, render_ask_report, run_ask_cases
    from .edit_eval import load_papers

    mode = LLMMode.record if args.record else LLMMode(args.mode)
    settings = Settings(
        profile=Profile(args.profile),
        llm_mode=mode,
        cassette_namespace=args.cassette_ns or "edit",
        off=args.off,
        llm_concurrency=args.concurrency * 2,
    )
    root = settings.root_dir
    base = root / "eval" / "datasets" / "edit"
    cases = load_ask_cases(base / "ask.yaml")
    if args.limit:
        cases = cases[: args.limit]
    paper = load_papers(base / "papers.yaml")["base8"]
    results = asyncio.run(run_ask_cases(settings, cases, paper, args.concurrency))
    md = render_ask_report(results, f"{args.tag or 'ask'} · {settings.features.describe()}")
    out = root / "eval" / "reports" / f"ask_{args.tag or 'run'}_{time.strftime('%Y%m%d-%H%M%S')}.md"
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"报告：{out}")
    return 0


def cmd_perceive(args: argparse.Namespace) -> int:
    """拍照感知评测（B4 / B5 / B6 / P3～P7 / P5 / P6）：真实照片、变体、负例；只评 perceive 阶段。"""
    import os

    from .perceive_eval import (
        dump_predictions,
        load_gold,
        load_photo_cases,
        render_report,
        run_cases,
        summarize,
    )

    if args.model:
        os.environ["VERICHALK_MODEL_VISION"] = args.model
    mode = LLMMode.record if args.record else LLMMode(args.mode)
    settings = Settings(
        profile=Profile(args.profile),
        llm_mode=mode,
        cassette_namespace=args.cassette_ns or "perceive",
        off=args.off,
        llm_concurrency=args.concurrency * 2,
        perceive_max_side=args.max_side,
    )
    root = settings.root_dir
    gold = load_gold(root)
    groups = {g.strip() for g in args.set.split(",") if g.strip()}
    cases = load_photo_cases(root, gold, groups, args.split)
    if args.only:
        keep = {x.strip() for x in args.only.split(",")}
        cases = [c for c in cases if c.id in keep]
    if args.limit:
        cases = cases[: args.limit]
    scores, preds = asyncio.run(run_cases(settings, cases, gold, args.concurrency))
    summary = summarize(scores)
    title = f"{args.tag or 'perceive'} · {args.model or 'vision 默认'} · {settings.features.describe()}"
    md = render_report(scores, title, summary)
    out = root / "eval" / "reports" / f"perceive_{args.tag or 'run'}_{time.strftime('%Y%m%d-%H%M%S')}.md"
    out.write_text(md, encoding="utf-8")
    if args.dump:
        dump_predictions(Path(args.dump), scores, preds)
    print(md)
    print(f"报告：{out}")
    return 0


def cmd_photo_e2e(args: argparse.Namespace) -> int:
    """拍照出题端到端评测（P8 防雷同 / P9 照片 + 一句话）：整条主管线。"""
    import os

    from .perceive_eval import load_gold
    from .photo_e2e import load_photo_cases, render_report, run_photo_cases

    if args.model:
        os.environ["VERICHALK_MODEL_VISION"] = args.model
    mode = LLMMode.record if args.record else LLMMode(args.mode)
    settings = Settings(
        profile=Profile(args.profile),
        llm_mode=mode,
        cassette_namespace=args.cassette_ns or "photo_e2e",
        off=args.off,
        llm_concurrency=args.concurrency * 4,
    )
    root = settings.root_dir
    cases = load_photo_cases(root / "eval" / "datasets" / "cases" / "photo_cases.yaml")
    if args.only:
        keep = {x.strip() for x in args.only.split(",")}
        cases = [c for c in cases if c["id"] in keep]
    results = asyncio.run(
        run_photo_cases(
            settings, cases, load_gold(root), root / "eval" / "datasets" / "photos" / "raw", args.concurrency
        )
    )
    md = render_report(results, f"{args.tag or 'photo-e2e'} · {settings.features.describe()}")
    out = root / "eval" / "reports" / f"perceive_e2e_{args.tag or 'run'}_{time.strftime('%Y%m%d-%H%M%S')}.md"
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"报告：{out}")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    """手改复核评测（C2 与 edit.review 消融）：20 次手动编辑，检出率 / 误报 / 时延。"""
    from .edit_eval import load_papers
    from .review_eval import render_review_report, run_review_eval

    mode = LLMMode.record if args.record else LLMMode(args.mode)
    settings = Settings(
        profile=Profile(args.profile),
        llm_mode=mode,
        cassette_namespace=args.cassette_ns or "edit",
        off=args.off,
        llm_concurrency=args.concurrency * 2,
    )
    root = settings.root_dir
    paper = load_papers(root / "eval" / "datasets" / "edit" / "papers.yaml")["base8"]
    rows = asyncio.run(run_review_eval(settings, paper, args.concurrency))
    md = render_review_report(rows, args.tag or "review", settings.features.enabled("edit.review"))
    out = root / "eval" / "reports" / f"review_{args.tag or 'run'}_{time.strftime('%Y%m%d-%H%M%S')}.md"
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"报告：{out}")
    return 0


def cmd_paper(args: argparse.Namespace) -> int:
    """整卷蓝图评测（P-A～P-D）：确定性，只用知识库，不调用模型。"""
    from .paper_eval import load_paper_specs, render_paper_report, run_paper_eval

    settings = Settings(off="plan.llm")
    root = settings.root_dir
    cases = load_paper_specs(root / "eval" / "datasets" / "edit" / "papers_spec.yaml")
    rows = asyncio.run(run_paper_eval(settings, cases))
    md = render_paper_report(rows, args.tag or "paper")
    out = root / "eval" / "reports" / f"paper_{args.tag or 'run'}_{time.strftime('%Y%m%d-%H%M%S')}.md"
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"报告：{out}")
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
    r.add_argument("--off", default="", help="关闭的特性开关，逗号分隔（消融用）")
    r.set_defaults(fn=cmd_run)
    a = sub.add_parser("ablate", help="依次跑全开与逐项关闭，输出对照表")
    a.add_argument("--suite", required=True)
    a.add_argument("--split", default="val", choices=["val", "test", "all"])
    a.add_argument("--mode", default="replay", choices=[m.value for m in LLMMode])
    a.add_argument("--record", action="store_true")
    a.add_argument("--profile", default="intl", choices=["cn", "intl"])
    a.add_argument("--concurrency", type=int, default=4)
    a.add_argument("--cassette-ns", default=None, help="录制命名空间；消融用独立命名空间，避免覆盖黄金录制")
    a.add_argument("--flags", default="", help="要消融的开关，逗号分隔；缺省为全部已注册开关")
    a.set_defaults(fn=cmd_ablate)
    v = sub.add_parser("verify", help="在 VerifyBank 上评测验证器（V1 / V2 / V5）")
    v.add_argument("--bank", default="answers", choices=["answers", "boundary"])
    v.add_argument("--split", default="val", choices=["val", "test", "all"])
    v.add_argument(
        "--checks",
        default="blind,quality",
        help="启用的检查，逗号分隔：program,blind,boundary,quality",
    )
    v.add_argument(
        "--role", action="append", default=[], help="角色覆盖，如 solver=deepseek-v4.1-flash:think"
    )
    v.add_argument("--mode", default="replay", choices=[m.value for m in LLMMode])
    v.add_argument("--record", action="store_true")
    v.add_argument("--profile", default="intl", choices=["cn", "intl"])
    v.add_argument("--concurrency", type=int, default=8)
    v.add_argument("--limit", type=int, default=0)
    v.add_argument("--sample", type=int, default=0, help="从评测集随机抽 N 条（固定种子，各配置抽到同一批）")
    v.add_argument("--tag", default="")
    v.add_argument("--cassette-ns", default=None)
    v.set_defaults(fn=cmd_verify)
    pr = sub.add_parser("produce", help="创作与核验的端到端评测（含审计；--naive 加跑朴素基线）")
    pr.add_argument("--split", default="val", choices=["val", "test", "all"])
    pr.add_argument("--mode", default="replay", choices=[m.value for m in LLMMode])
    pr.add_argument("--record", action="store_true")
    pr.add_argument("--profile", default="intl", choices=["cn", "intl"])
    pr.add_argument("--concurrency", type=int, default=4)
    pr.add_argument("--limit", type=int, default=0)
    pr.add_argument("--only", default="", help="只跑这些用例 id，逗号分隔")
    pr.add_argument("--off", default="", help="关闭的特性开关（消融用）")
    pr.add_argument("--on", default="", help="额外开启的选择性开关")
    pr.add_argument(
        "--pipeline",
        default="classic",
        choices=["classic", "design"],
        help="链路预设：classic（默认，mid1 稳定链路）| design（好题设计）",
    )
    pr.add_argument("--naive", action="store_true")
    pr.add_argument(
        "--no-audit", action="store_true", help="跳过审计（只看交付数 / 延迟 / 成本 / 修复，快速迭代用）"
    )
    pr.add_argument("--dump", default="", help="把原始产出写到这个路径（复盘用；不要放进仓库）")
    pr.add_argument("--tag", default="")
    pr.add_argument("--cassette-ns", default=None)
    pr.set_defaults(fn=cmd_produce)
    ex = sub.add_parser("export", help="导出评测（PDF / Word / Markdown / LaTeX；不调用模型）")
    ex.add_argument("--only", default="", help="只跑这些用例 id，逗号分隔")
    ex.add_argument("--dump", default="", help="把所有导出文件保存到该目录（人工抽检）")
    ex.add_argument("--tex-compile", action="store_true", help="本机有 xelatex 时实际编译 LaTeX 源码（X9）")
    ex.add_argument("--tag", default="")
    ex.set_defaults(fn=cmd_export)
    ed = sub.add_parser("edit", help="自然语言编辑评测（C1）")
    ed.add_argument("--split", default="val", choices=["val", "test", "all"])
    ed.add_argument(
        "--cases",
        default="main",
        choices=["main", "fresh"],
        help="fresh = 从未调优过的留出集（验收只跑一次）",
    )
    ed.add_argument("--mode", default="replay", choices=[m.value for m in LLMMode])
    ed.add_argument("--record", action="store_true")
    ed.add_argument("--profile", default="intl", choices=["cn", "intl"])
    ed.add_argument("--concurrency", type=int, default=4)
    ed.add_argument("--limit", type=int, default=0)
    ed.add_argument("--only", default="", help="只跑这些用例 id，逗号分隔")
    ed.add_argument("--off", default="", help="关闭的特性开关（消融用）")
    ed.add_argument("--tag", default="")
    ed.add_argument("--cassette-ns", default=None)
    ed.set_defaults(fn=cmd_edit)
    ak = sub.add_parser("ask", help="追问评测（S7）")
    ak.add_argument("--mode", default="replay", choices=[m.value for m in LLMMode])
    ak.add_argument("--record", action="store_true")
    ak.add_argument("--profile", default="intl", choices=["cn", "intl"])
    ak.add_argument("--concurrency", type=int, default=6)
    ak.add_argument("--limit", type=int, default=0)
    ak.add_argument("--off", default="", help="关闭的特性开关（消融用）")
    ak.add_argument("--tag", default="")
    ak.add_argument("--cassette-ns", default=None)
    ak.set_defaults(fn=cmd_ask)
    pc = sub.add_parser("perceive", help="拍照感知评测（B4 / B5 / B6 / P5 / P6；照片与标注不入库）")
    pc.add_argument("--set", default="raw", help="评测集，逗号分隔：raw,variant,negative")
    pc.add_argument("--split", default="val", choices=["val", "test", "all"])
    pc.add_argument("--mode", default="replay_or_live", choices=[m.value for m in LLMMode])
    pc.add_argument("--record", action="store_true")
    pc.add_argument("--profile", default="cn", choices=["cn", "intl"])
    pc.add_argument("--concurrency", type=int, default=4)
    pc.add_argument("--limit", type=int, default=0)
    pc.add_argument("--only", default="", help="只跑这些用例 id，逗号分隔")
    pc.add_argument("--model", default="", help="覆盖 vision 角色的模型（模型对比用）")
    pc.add_argument("--max-side", type=int, default=1600, help="送入模型的图片长边上限")
    pc.add_argument("--off", default="", help="关闭的特性开关（消融用）")
    pc.add_argument("--dump", default="", help="把识别结果与打分写到这个路径（复盘用；不要放进仓库）")
    pc.add_argument("--tag", default="")
    pc.add_argument("--cassette-ns", default=None)
    pc.set_defaults(fn=cmd_perceive)
    pe = sub.add_parser("photo-e2e", help="拍照出题端到端评测（P8 / P9；照片不入库）")
    pe.add_argument("--mode", default="replay_or_live", choices=[m.value for m in LLMMode])
    pe.add_argument("--record", action="store_true")
    pe.add_argument("--profile", default="cn", choices=["cn", "intl"])
    pe.add_argument("--concurrency", type=int, default=2)
    pe.add_argument("--only", default="", help="只跑这些用例 id，逗号分隔")
    pe.add_argument("--model", default="", help="覆盖 vision 角色的模型")
    pe.add_argument("--off", default="", help="关闭的特性开关（消融用）")
    pe.add_argument("--tag", default="")
    pe.add_argument("--cassette-ns", default=None)
    pe.set_defaults(fn=cmd_photo_e2e)
    rv = sub.add_parser("review", help="手改复核评测（C2 与 edit.review 消融）")
    rv.add_argument("--mode", default="replay", choices=[m.value for m in LLMMode])
    rv.add_argument("--record", action="store_true")
    rv.add_argument("--profile", default="intl", choices=["cn", "intl"])
    rv.add_argument("--concurrency", type=int, default=6)
    rv.add_argument("--off", default="", help="关闭的特性开关（edit.review 消融）")
    rv.add_argument("--tag", default="")
    rv.add_argument("--cassette-ns", default=None)
    rv.set_defaults(fn=cmd_review)
    pp = sub.add_parser("paper", help="整卷蓝图评测（P-A～P-D；确定性，不调用模型）")
    pp.add_argument("--tag", default="")
    pp.set_defaults(fn=cmd_paper)
    c = sub.add_parser("compare")
    c.add_argument("--base", required=True)
    c.add_argument("--new", required=True)
    c.set_defaults(fn=cmd_compare)
    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()

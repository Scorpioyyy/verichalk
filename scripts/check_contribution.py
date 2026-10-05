"""每一层核验到底有没有独有的贡献？（消融的轻量版）

做法：只开结构检查，生成一批"原始初稿"（不修复、不核验）→ 审计给每道初稿定真值（答案对错 / 是否超纲 / 题面质量）
→ 把每一层检查单独跑在这批初稿上，统计它抓到的真问题、误杀的好题、以及其他层都没抓到而它抓到的（独有贡献）。
用法：python scripts/check_contribution.py [--file eval/datasets/quality_probe.yaml] [--per 6]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend" / "src"))
warnings.simplefilter("ignore")

import yaml
from verichalk.core.config import LLMMode, Profile, Settings
from verichalk.eval.audit import AuditItem, Auditor, AuditStore
from verichalk.orchestrator import build_container
from verichalk.stages import (
    PlanIn,
    PlanStage,
    ProduceIn,
    ProduceStage,
    RunContext,
)
from verichalk.stages.produce import _normalize
from verichalk.stages.understand import UnderstandIn, UnderstandStage
from verichalk.store import Store
from verichalk.verify import VerifyEnv, VerifyInput
from verichalk.verify.checks import (
    check_blind,
    check_boundary,
    check_program,
    check_quality,
    check_structure,
)

CHECKS = ["structure", "program", "blind", "boundary", "quality"]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="eval/datasets/quality_probe.yaml")
    ap.add_argument("--per", type=int, default=6, help="每个请求取前几道")
    args = ap.parse_args()
    s = Settings(profile=Profile.intl, llm_mode=LLMMode.live)
    c = await build_container(s, store=await Store.open(":memory:"))
    ctx = RunContext(
        run_id="contrib",
        session_id="contrib",
        settings=s,
        llm=c.llm,
        kb=c.kb,
        store=c.store,
    )
    env = VerifyEnv(llm=c.llm, kb=c.kb)
    reqs = yaml.safe_load((ROOT / args.file).read_text(encoding="utf-8"))
    stage = ProduceStage()
    sem = asyncio.Semaphore(12)

    async def draft(text: str):  # type: ignore[no-untyped-def]
        u = await UnderstandStage().run(ctx, UnderstandIn(text=text))
        if u.brief is None:
            return []
        bp = await PlanStage().run(ctx, PlanIn(brief=u.brief))
        out = []

        async def one(spec):  # type: ignore[no-untyped-def]
            async with sem:
                details, examples = await stage._context(ctx, spec)
                w = _normalize(
                    await stage._write(
                        ctx,
                        ProduceIn(spec=spec, grade=bp.grade, lesson_id=bp.lesson_id),
                        details,
                        examples,
                        None,
                    )
                )
                return spec, w, bp

        out = await asyncio.gather(*(one(sp) for sp in bp.items[: args.per]))
        return out

    drafts = [d for ds in await asyncio.gather(*(draft(t) for t in reqs)) for d in ds]
    print(f"初稿 {len(drafts)} 道，开始逐层检查与审计……", flush=True)

    store = AuditStore(ROOT / "eval" / "audits" / "contrib.jsonl")
    auditor = Auditor(s, c.kb, store, concurrency=20)
    g = await c.kb.graph()

    async def evaluate(spec, w, bp):  # type: ignore[no-untyped-def]
        vin = VerifyInput(
            kind=spec.kind.value,
            stem=w.stem,
            options=w.options,
            answers=w.answers,
            solution=w.solution,
            solver_code=w.solver_code,
            kp_names=spec.kp_names,
            tier=spec.tier.value,
            grade=bp.grade,
            lesson_id=bp.lesson_id,
            target_difficulty=spec.difficulty,
        )
        async with sem:
            res = await asyncio.gather(
                check_structure(vin),
                check_program(vin),
                check_blind(env, vin),
                check_boundary(env, vin),
                check_quality(env, vin),
            )
        flagged = {
            n: (r.status.value == "fail")
            for n, r in zip(CHECKS[:4], res[:4], strict=True)
        }
        q, integ = res[4]
        flagged["quality"] = q.status.value == "fail" or integ.status.value == "fail"
        from verichalk.verify.answers import answer_values

        ai = AuditItem(
            stem=w.stem,
            options=w.options,
            answers=answer_values(w.answers),
            solution=w.solution,
            kind=spec.kind.value,
            grade=bp.grade,
            lesson_id=bp.lesson_id,
            kp_names=[g.nodes[k].name for k in spec.kp_ids if k in g.nodes],
            tier=spec.tier.value,
        )
        a = await auditor.audit(ai)
        bad_answer = a.a2 == "wrong"
        bad_scope = a.a3 == "out"
        qd = a.quality or {}
        bad_quality = bool(qd) and not all(
            qd.get(k, False)
            for k in ("unambiguous", "complete", "data_plausible", "solution_ok")
        )
        return (
            flagged,
            {"answer": bad_answer, "scope": bad_scope, "quality": bad_quality},
            a.a2 == "disputed",
        )

    rows = await asyncio.gather(*(evaluate(*d) for d in drafts))
    n = len(rows)
    print(f"\n## 每层检查对 {n} 道原始初稿的贡献（真值来自独立审计）")
    truth = {
        k: sum(1 for _, t, _ in rows if t[k]) for k in ("answer", "scope", "quality")
    }
    anybad = [any(t.values()) for _, t, _ in rows]
    print(
        f"审计认定的真问题：答案错 {truth['answer']}、超纲 {truth['scope']}、题面质量差 {truth['quality']}；至少有一项问题的题 {sum(anybad)}/{n}"
    )
    print(
        "\n| 检查 | 标出的题 | 其中确有问题 | 误杀好题 | 独有抓取（其他层都没标出的真问题题） |\n|---|---|---|---|---|"
    )
    for name in CHECKS:
        fl = [f[name] for f, _, _ in rows]
        hit = sum(1 for x, b in zip(fl, anybad, strict=True) if x and b)
        false = sum(1 for x, b in zip(fl, anybad, strict=True) if x and not b)
        uniq = sum(
            1
            for (f, _, _), b in zip(rows, anybad, strict=True)
            if b and f[name] and not any(f[o] for o in CHECKS if o != name)
        )
        print(f"| {name} | {sum(fl)} | {hit} | {false} | {uniq} |")
    missed = sum(
        1
        for (f, _, _), b in zip(rows, anybad, strict=True)
        if b and not any(f.values())
    )
    print(f"\n所有检查合起来仍漏掉的真问题题：{missed}/{sum(anybad)}")
    await c.close()


asyncio.run(main())

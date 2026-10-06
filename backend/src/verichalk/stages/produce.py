"""生成阶段：`ItemSpec` → `Item`（architecture §5.1，D5、D9、D24）。

`novel` 来源：写题（题面、结构化答案、解析、求解程序）→ 规范化 → 分层核验 → 通过则交付；未通过则带着失败证据修复（≤ 2 次），
仍不通过就从头重写一次，再不行就废弃（不展示）。`template` 来源：题型实例化，答案由知识库的程序算出，仍做结构 / 题面质量检查。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from typing import Any

from pydantic import BaseModel

from .. import trace
from ..core.errors import (
    BudgetExceeded,
    KnowledgeError,
    LLMError,
    NotFound,
    SandboxError,
    SandboxTimeout,
    SandboxViolation,
)
from ..core.ids import new_id
from ..domain.blueprint import AnswerPart, ItemSpec, WriteOut
from ..domain.knowledge import KPDetail
from ..domain.llm import Role
from ..domain.paper import (
    CheckResult,
    CheckStatus,
    Item,
    ItemKind,
    Provenance,
    Source,
    Tier,
    Verification,
    VerifyStatus,
)
from ..llm import LLMRequest, complete_json, get_prompt
from ..sandbox import run_solver
from ..verify import (
    VerifyEnv,
    VerifyInput,
    answers_equal,
    failing,
    format_answer,
    normalize_text,
    verify_item,
)
from ..verify.answers import program_values
from .base import RunContext, Stage

log = logging.getLogger("verichalk.produce")

_TIER_CN = {Tier.consolidate: "巩固", Tier.variation: "变式", Tier.integrated: "综合"}
_KIND_CN = {
    ItemKind.fill: "填空题",
    ItemKind.choice: "选择题",
    ItemKind.calc: "计算题",
    ItemKind.judge: "判断题",
    ItemKind.application: "应用题",
    ItemKind.open: "开放题",
}
_GRADE_CN = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六"}
_FORM_TO_KIND = {
    "compute": ItemKind.calc,
    "fill_blank": ItemKind.fill,
    "word_problem": ItemKind.application,
    "judge": ItemKind.judge,
}
_DIFFICULTY_RULE = {
    1: "一步直接计算。",
    2: "一到两步直接计算。",
    3: "两到三步，需要选对运算或先整理数据。",
    4: "三步以上，或含逆向思考 / 比较择优 / 条件取舍中的至少一种；不能是一步或两步就出结果的题。",
    5: "在 4 的基础上再加一个陷阱或多条件约束（单位不统一、多余条件、需要先判断再计算）。",
}
MAX_REPAIRS = 1  # 每轮最多修复几次（同一思路修第二次的成功率很低，不如从头重写）
REGENERATIONS = 1  # 修复用尽后，从头重写的次数


class _ExplainOut(BaseModel):
    solution: str


class ProduceIn(BaseModel):
    spec: ItemSpec
    grade: int | None = None
    lesson_id: str | None = None  # 能力边界的参照课时
    constraints: str = ""  # 教师的其他要求（如"数字不要太大"）
    avoid: list[str] = []  # 套内其他题的考法摘要（本题要与之不同）
    references: list[str] = []  # 照片里的题的纯题面（防雷同的比较对象）
    reference_notes: list[str] = []  # 同一批题的提示词写法（带大题题干），给写题参考
    rewrite: dict[str, Any] | None = (
        None  # 改写已有的题（编辑阶段）：{stem, options, answer, solution, instruction}
    )


class ProduceOut(BaseModel):
    item: Item | None = None
    attempts: int = 0  # 写题的次数（含修复与重写）
    failed_checks: list[list[str]] = []  # 每次未通过的尝试被哪些检查拦下（诊断一次通过率）
    dropped_reason: str = ""


def _pick_references(refs: list[str], n: int) -> list[str]:
    """从照片里的题挑 n 道给写题参考：均匀取样，让不同大题的考法都有机会被看到（确定性）。"""
    if len(refs) <= n:
        return list(refs)
    step = len(refs) / n
    return [refs[int(i * step)] for i in range(n)]


def _after_equals(v: str) -> str:
    """知识库的答案有时写成整条算式（"98÷8=12……2"）：只取等号后面的结果。"""
    return v.rsplit("=", 1)[-1].strip() if "=" in v else v


def _previous_text(w: WriteOut) -> str:
    return json.dumps(
        {
            "stem": w.stem,
            "options": w.options,
            "answers": [a.model_dump() for a in w.answers],
            "solution": w.solution,
        },
        ensure_ascii=False,
    )


def _problems(ver: Verification) -> list[str]:
    out: list[str] = []
    for c in failing(ver.checks):
        line = f"【{c.name}】{c.detail}"
        ev = c.evidence
        if c.name == "program" and ev.get("program"):
            line += f"（程序结果 {ev.get('program')}，你写的答案 {ev.get('claimed')}；请检查是程序还是答案、题意有问题）"
        if c.name == "blind" and ev.get("blind"):
            line += f"（独立解题者的解答思路：{str(ev.get('work', ''))[:300]}）"
        if c.name == "boundary" and ev.get("violations"):
            line += "。学生在该课时之前没学过这些内容，请改用学过的方法和数据"
        out.append(line)
    return out


def _normalize(w: WriteOut) -> WriteOut:
    return w.model_copy(
        update={
            "stem": normalize_text(w.stem),
            "options": [normalize_text(o) for o in w.options],
            "solution": normalize_text(w.solution),
            "answers": [
                a.model_copy(update={"value": a.value.strip(), "unit": a.unit.strip()}) for a in w.answers
            ],
        }
    )


async def fix_choice_letter(w: WriteOut, kind: ItemKind) -> WriteOut:
    """选择题的答案字母由求解程序的结果确定：程序算出的值恰好等于某一个选项时，字母以它为准。

    模型常把正确值写进了选项，却把字母指向别的位置（核验里最常见的选择题失败）；字母本来就是"哪个选项是对的"的派生信息，
    由程序结果推出比让模型自己对齐更可靠。之后的独立盲解仍会核验这个答案。程序结果与任何选项都对不上时不改（交给核验）。"""
    if kind != ItemKind.choice or not w.solver_code.strip() or len(w.answers) != 1 or not w.options:
        return w
    claimed = w.answers[0].value.strip()
    if not re.fullmatch(r"[A-Da-d]", claimed):
        return w
    try:
        out = await run_solver(w.solver_code, timeout_s=5.0)
    except (SandboxViolation, SandboxTimeout, SandboxError):
        return w
    got = program_values(out.value)
    if len(got) != 1:
        return w
    hits = [i for i, o in enumerate(w.options) if answers_equal(got, [o])]
    if len(hits) != 1 or "ABCD"[hits[0]] == claimed.upper():
        return w
    return w.model_copy(update={"answers": [w.answers[0].model_copy(update={"value": "ABCD"[hits[0]]})]})


class ProduceStage(Stage[ProduceIn, ProduceOut]):
    name = "produce"
    input_model = ProduceIn
    output_model = ProduceOut

    async def run(self, ctx: RunContext, inp: ProduceIn) -> ProduceOut:
        if inp.spec.source == "template":
            return await self._template(ctx, inp)
        return await self._novel(ctx, inp)

    # ---- novel：写题 → 核验 → 修复 ----
    async def _novel(self, ctx: RunContext, inp: ProduceIn) -> ProduceOut:
        spec, feats = inp.spec, ctx.settings.features
        env = VerifyEnv(llm=ctx.llm, kb=ctx.kb)
        details, examples = await self._context(ctx, spec)
        if inp.references and feats.enabled("perceive.references_in_prompt"):
            examples = [*_pick_references(inp.reference_notes or inp.references, 3), *examples][
                :5
            ]  # 学生真做过的题排在前面
        max_repairs = MAX_REPAIRS if feats.enabled("produce.repair") else 0
        rounds = 1 + (REGENERATIONS if feats.enabled("produce.repair") else 0)
        attempts, last_reason = 0, ""
        started = time.monotonic()
        budget_s = ctx.settings.item_budget_s  # 单题时间预算：超过后不再开始新的尝试（有界延迟）
        failed: list[list[str]] = []
        for _ in range(rounds):
            repair: dict[str, Any] | None = None
            for _r in range(1 + max_repairs):
                if attempts and time.monotonic() - started > budget_s:
                    last_reason = (last_reason + "；" if last_reason else "") + "超出单题时间预算"
                    return ProduceOut(
                        item=None, attempts=attempts, failed_checks=failed, dropped_reason=last_reason[:300]
                    )
                try:
                    w = _normalize(
                        await self._write(
                            ctx,
                            inp,
                            details,
                            examples if feats.enabled("context.textbook_examples") else [],
                            repair,
                        )
                    )
                except (LLMError, BudgetExceeded) as e:
                    last_reason = f"写题失败（{e.code}）"
                    attempts += 1
                    break
                attempts += 1
                w = await fix_choice_letter(w, spec.kind)
                vin = VerifyInput(
                    kind=spec.kind.value,
                    stem=w.stem,
                    options=w.options,
                    answers=w.answers,
                    solution=w.solution,
                    solver_code=w.solver_code,
                    kp_names=spec.kp_names,
                    tier=spec.tier.value,
                    grade=inp.grade,
                    lesson_id=inp.lesson_id,
                    target_difficulty=spec.difficulty if feats.enabled("produce.difficulty_check") else None,
                    references=inp.references,
                )
                ver = await verify_item(env, vin, feats)
                if ver.status != VerifyStatus.rejected:
                    item = self._to_item(ctx, spec, w, ver)
                    if inp.rewrite is None:  # 改写的结果不是试卷里的新题，状态由编辑阶段按原题 id 发出
                        await trace.item_status(item.id, ver.status, ver.checks)
                    return ProduceOut(item=item, attempts=attempts, failed_checks=failed)
                problems = _problems(ver)
                failed.append([f"{c.name}:{c.detail[:110]}" for c in failing(ver.checks)])
                last_reason = "；".join(problems)[:300]
                repair = {"previous": _previous_text(w), "problems": problems}
        return ProduceOut(
            item=None, attempts=attempts, failed_checks=failed, dropped_reason=last_reason or "没有通过核验"
        )

    async def _context(self, ctx: RunContext, spec: ItemSpec) -> tuple[list[KPDetail], list[str]]:
        details: list[KPDetail] = []
        examples: list[str] = []
        try:
            for kid in spec.kp_ids[:3]:
                details.append(await ctx.kb.kp(kid))
            for kid in spec.kp_ids[:2]:
                for a in await ctx.kb.archetypes_for(kid, limit=3):
                    if (a.figure_ratio or 0) < 0.5:
                        examples += a.examples[:2]
        except (KnowledgeError, NotFound) as e:
            log.warning("produce: kb context unavailable: %s", e.code)
        return details, examples[:4]

    async def _write(
        self,
        ctx: RunContext,
        inp: ProduceIn,
        details: list[KPDetail],
        examples: list[str],
        repair: dict[str, Any] | None,
    ) -> WriteOut:
        spec = inp.spec
        by_id = {d.id: d for d in details}
        kps = [
            {
                "name": by_id[k].name if k in by_id else n,
                "desc": (by_id[k].description[:140] if k in by_id else ""),
                "errors": (by_id[k].typical_errors[:2] if k in by_id else []),
            }
            for k, n in zip(spec.kp_ids, spec.kp_names, strict=False)
        ]
        roles = [
            f"{n}：{spec.roles[k]}"
            for k, n in zip(spec.kp_ids, spec.kp_names, strict=False)
            if k in spec.roles
        ]
        dp = ctx.settings.features.enabled("produce.design_prompt")
        built = get_prompt("produce.write").render(
            stable={"design_mode": dp},
            dynamic={
                "grade_text": f"{_GRADE_CN.get(inp.grade or 0, '')}年级" if inp.grade else "小学",
                "tier_cn": _TIER_CN[spec.tier],
                "difficulty": spec.difficulty,
                "kind_cn": _KIND_CN[spec.kind],
                "kps": kps,
                "roles": roles,
                "scene": spec.scene,
                "scene_hint": spec.scene_hint,
                "angle": spec.angle,
                "design": spec.design if dp else "",
                "target_error": spec.target_error if dp else "",
                "difficulty_rule": _DIFFICULTY_RULE.get(min(max(spec.difficulty, 1), 5), "")
                if (dp or inp.rewrite)
                else "",
                "number_hint": spec.number_hint,
                "constraints": inp.constraints,
                "examples": examples,
                "avoid": inp.avoid,
                "rewrite": inp.rewrite,
                "repair": repair,
            },
        )
        out, _ = await complete_json(
            ctx.llm,
            LLMRequest(
                role=Role.smart,
                messages=built.messages,
                purpose="produce.write" + (".repair" if repair else ""),
                prompt=built.ref,
                max_tokens=2200,
            ),
            WriteOut,
        )
        return out

    def _to_item(self, ctx: RunContext, spec: ItemSpec, w: WriteOut, ver: Verification) -> Item:
        return Item(
            id=new_id("itm"),
            kind=spec.kind,
            stem=w.stem,
            options=w.options,
            answer=format_answer(w.answers),
            answer_value=[a.model_dump() for a in w.answers],
            solution=w.solution,
            kp_ids=spec.kp_ids,
            difficulty=spec.difficulty,
            tier=spec.tier,
            provenance=Provenance(
                source=Source.novel,
                archetype_ids=spec.archetype_ids,
                run_id=ctx.run_id,
                model=ctx.llm.registry.role(Role.smart).model,
            ),
            verification=ver,
        )

    # ---- template：题型实例化（答案由知识库的程序算出）----
    async def _template(self, ctx: RunContext, inp: ProduceIn) -> ProduceOut:
        """依次尝试候选题型（跳过本次运行里已知题面有缺陷的），每个题型换种子重试一次；通过核验即交付。
        题型卡片的答案由程序算出，但题面质量不保证（括号里写两种情境、占位不清……），所以仍过题面质量检查。"""
        spec = inp.spec
        bad: set[str] = ctx.scratch.setdefault("bad_archetypes", set())
        base_seed = int(hashlib.sha1(f"{ctx.run_id}:{spec.id}".encode()).hexdigest(), 16) % 10_000
        reasons: list[str] = []
        attempts = 0
        tried = [a for a in spec.archetype_ids if a not in bad]
        for aid in tried[:3]:
            for k in range(2):
                attempts += 1
                try:
                    problem = await ctx.kb.instantiate(
                        aid, seed=base_seed + k, lesson_id=inp.lesson_id, only_in_bounds=True
                    )
                except (NotFound, KnowledgeError) as e:
                    reasons.append(f"题型 {aid} 无法实例化（{e.code}）")
                    bad.add(aid)
                    break
                item, why = await self._template_item(ctx, inp, problem)
                if item is not None:
                    return ProduceOut(item=item, attempts=attempts)
                reasons.append(why)
            else:
                bad.add(aid)  # 两个种子都没通过：这个题型的题面大概率有结构性缺陷，本次运行里不再用它
        return ProduceOut(
            attempts=attempts, dropped_reason=("；".join(reasons) or "没有找到可用的题型")[:300]
        )

    async def _explain(self, ctx: RunContext, stem: str, answer: str) -> str:
        """题型卡片里的解析是不含数值的抽象步骤（含 `{a}` 占位符），不能直接给教师：为这道具体的题写一份简短解析。"""
        built = get_prompt("produce.explain").render(dynamic={"stem": stem, "options": [], "answer": answer})
        try:
            out, _ = await complete_json(
                ctx.llm,
                LLMRequest(
                    role=Role.fast,
                    messages=built.messages,
                    purpose="produce.explain",
                    prompt=built.ref,
                    max_tokens=400,
                    temperature=0.2,
                ),
                _ExplainOut,
            )
        except (LLMError, BudgetExceeded):
            return ""
        return normalize_text(out.solution)

    async def _template_item(
        self, ctx: RunContext, inp: ProduceIn, problem: dict[str, Any]
    ) -> tuple[Item | None, str]:
        spec, feats = inp.spec, ctx.settings.features
        raw_answer = problem.get("answer")
        if raw_answer is None:
            return None, "该题型没有程序化答案"
        values = problem.get("answer_value")
        parts = (
            [AnswerPart(value=_after_equals(str(v))) for v in values]
            if isinstance(values, list)
            else [AnswerPart(value=_after_equals(str(raw_answer)))]
        )
        form = str(problem.get("item_form", ""))
        kind = _FORM_TO_KIND.get(form, spec.kind)
        if kind == ItemKind.judge and any(a.value.strip() not in ("对", "错", "正确", "错误") for a in parts):
            kind = ItemKind.calc  # 题型标成判断题，答案却是数值：按计算题处理
        stem = normalize_text(str(problem["problem"]))
        solution = await self._explain(ctx, stem, format_answer(parts))
        vin = VerifyInput(
            kind=kind.value,
            stem=stem,
            answers=parts,
            solution=solution,
            kp_names=spec.kp_names,
            tier=spec.tier.value,
            grade=inp.grade,
            lesson_id=inp.lesson_id,
        )
        bnd = problem.get("boundary") or {}
        preset = {
            "program": CheckResult(
                name="program",
                status=CheckStatus.passed,
                detail="答案由知识库的求解程序算出",
                evidence={
                    "source": "chalkbase",
                    "archetype": problem.get("archetype_id"),
                    "seed": problem.get("seed"),
                },
            ),
            "boundary": CheckResult(
                name="boundary",
                status=CheckStatus.passed if bnd.get("verdict", "in") != "out" else CheckStatus.fail,
                detail="知识库按参数判定",
                evidence={"verdict": bnd.get("verdict"), "source": "chalkbase"},
            ),
        }
        ver = await verify_item(VerifyEnv(llm=ctx.llm, kb=ctx.kb), vin, feats, preset=preset)
        if ver.status == VerifyStatus.rejected:
            return None, "；".join(_problems(ver))[:160]
        item = Item(
            id=new_id("itm"),
            kind=kind,
            stem=stem,
            answer=format_answer(parts),
            answer_value=[a.model_dump() for a in parts],
            solution=solution,
            kp_ids=spec.kp_ids,
            difficulty=int(problem.get("difficulty") or spec.difficulty),
            tier=Tier.consolidate,
            provenance=Provenance(
                source=Source.template, archetype_ids=[str(problem.get("archetype_id"))], run_id=ctx.run_id
            ),
            verification=ver,
        )
        await trace.item_status(item.id, ver.status, ver.checks)
        return item, ""

"""核验检查：每个检查是一个独立的、带类型的异步函数，返回 `CheckResult`（D9，architecture §5.1）。

线上（produce 阶段）与评测（核验评测集）调用的是同一批函数，因此评测测到的就是线上跑的。
状态约定：`pass` 通过；`fail` 必须修复或废弃；`warn` 可交付但要如实标注；`skip` 未执行（被关闭 / 降级 / 前置检查失败）。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .. import trace
from ..core.errors import (
    BudgetExceeded,
    KnowledgeError,
    LLMError,
    SandboxError,
    SandboxTimeout,
    SandboxViolation,
)
from ..domain.blueprint import AnswerPart
from ..domain.llm import Role
from ..domain.paper import CheckResult, CheckStatus
from ..knowledge import KnowledgeService
from ..llm import LLMGateway, LLMRequest, complete_json, get_prompt
from ..sandbox import run_solver
from .answers import answer_values, answers_equal, format_answer, program_values
from .content import structure_issues

log = logging.getLogger("verichalk.verify")
GRADE_CN = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六"}


@dataclass
class VerifyInput:
    """被核验的一道题（与 `Item` 解耦：评测集里的题也走同一套检查）。"""

    kind: str  # choice / fill / calc / judge / application / open
    stem: str
    options: list[str] = field(default_factory=list)
    answers: list[AnswerPart] = field(default_factory=list)
    solution: str = ""
    solver_code: str = ""
    kp_names: list[str] = field(default_factory=list)
    tier: str = "consolidate"
    grade: int | None = None
    lesson_id: str | None = None  # 能力边界的参照课时
    target_difficulty: int | None = None  # 规格要求的难度：判官估计低它 2 级以上就判"偏简单"
    references: list[str] = field(default_factory=list)  # 照片里的题：新题不得是它们的翻版


@dataclass
class VerifyEnv:
    llm: LLMGateway
    kb: KnowledgeService


def _res(name: str, status: CheckStatus, detail: str = "", **evidence: Any) -> CheckResult:
    return CheckResult(name=name, status=status, detail=detail, evidence=evidence)


def _clip(s: str, n: int = 500) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _grade_text(grade: int | None) -> str:
    return f"{GRADE_CN[grade]}年级" if grade in GRADE_CN else "小学"


# ---- 结构（确定性）----
async def check_structure(inp: VerifyInput) -> CheckResult:
    async with trace.check_span("structure") as sp:
        issues = structure_issues(
            kind=inp.kind,
            stem=inp.stem,
            options=inp.options,
            answer_values=answer_values(inp.answers),
            solution=inp.solution,
        )
        sp.set(n_issues=len(issues))
        if issues:
            return _res("structure", CheckStatus.fail, "；".join(issues), issues=issues)
        return _res("structure", CheckStatus.passed)


# ---- 防雷同（确定性）----
NOVELTY_COPY = 0.75  # 与参考题的字面相似度达到它：判为照抄
_NUM_SEQ = re.compile(r"\d+(?:\.\d+)?")


def _raw_shingles(text: str, n: int = 3) -> set[str]:
    t = _NOISE.sub("", text)
    return {t[i : i + n] for i in range(max(0, len(t) - n + 1))} or {t}


def copy_score(stem: str, ref: str) -> float:
    """新题与参考题的"翻版"程度（0～1）。数字参与比较：同样的算式换了数字不算翻版（"照这页再出 5 道"本来就要同类题）；
    数字与措辞都几乎不变才算。"""
    a, b = _raw_shingles(stem), _raw_shingles(ref)
    jac = len(a & b) / len(a | b)
    if _NUM_SEQ.findall(stem) == _NUM_SEQ.findall(ref) and len(_NUM_SEQ.findall(ref)) >= 2:
        jac = max(jac, similarity(stem, ref))  # 数字序列完全相同：再按"形状"比一次
    return jac


async def check_novelty(inp: VerifyInput) -> CheckResult:
    async with trace.check_span("novelty") as sp:
        worst, hit = 0.0, ""
        for ref in inp.references:
            sc = copy_score(inp.stem, ref)
            if sc > worst:
                worst, hit = sc, ref
        sp.set(max_similarity=round(worst, 2))
        if worst >= NOVELTY_COPY:
            return _res(
                "novelty",
                CheckStatus.fail,
                f"与照片里的题几乎相同：「{_clip(hit, 60)}」。请换数字、情境和问法，不要照抄",
                similarity=round(worst, 2),
                reference=_clip(hit, 120),
            )
        return _res("novelty", CheckStatus.passed, similarity=round(worst, 2))


# ---- 求解程序（沙箱）----
async def check_program(inp: VerifyInput) -> CheckResult:
    async with trace.check_span("program") as sp:
        if not inp.solver_code.strip():
            return _res("program", CheckStatus.skip, "没有求解程序")
        claimed = answer_values(inp.answers)
        try:
            out = await run_solver(inp.solver_code, timeout_s=5.0)
        except (SandboxViolation, SandboxTimeout, SandboxError) as e:
            sp.set(error=type(e).__name__)
            return _res(
                "program",
                CheckStatus.fail,
                f"求解程序无法运行：{e}",
                code=_clip(inp.solver_code, 800),
                error=str(e),
            )
        got = program_values(out.value)
        ok = answers_equal(got, claimed)
        if not ok and inp.kind == "choice" and claimed and re.fullmatch(r"[A-Da-d]", claimed[0].strip()):
            idx = (
                ord(claimed[0].strip().upper()) - 65
            )  # 选择题：程序给出正确选项的内容，与所选字母对应的选项比较
            ok = idx < len(inp.options) and answers_equal(got, [inp.options[idx]])
        sp.set(match=ok)
        if ok:
            return _res(
                "program",
                CheckStatus.passed,
                program=got,
                claimed=claimed,
                duration_ms=round(out.duration_ms),
            )
        return _res(
            "program",
            CheckStatus.fail,
            f"求解程序算出 {'、'.join(got)}，与题目答案 {'、'.join(claimed)} 不一致",
            program=got,
            claimed=claimed,
            code=_clip(inp.solver_code, 800),
        )


# ---- 盲解（独立模型，看不到答案）----
class BlindOut(BaseModel):
    work: str = ""
    answers: list[AnswerPart] = Field(default_factory=list)
    ambiguous: bool = False
    ambiguity: str = ""


async def check_blind(env: VerifyEnv, inp: VerifyInput) -> CheckResult:
    async with trace.check_span("blind") as sp:
        built = get_prompt("verify.blind").render(
            dynamic={"grade": _grade_text(inp.grade), "stem": inp.stem, "options": inp.options}
        )
        try:
            out, res = await complete_json(
                env.llm,
                LLMRequest(
                    role=Role.solver, messages=built.messages, purpose="verify.blind", prompt=built.ref
                ),
                BlindOut,
            )
        except (LLMError, BudgetExceeded) as e:
            sp.set(degraded=e.code)
            return _res("blind", CheckStatus.skip, f"盲解不可用（{e.code}）")
        got = [
            v for v in answer_values(out.answers) if "=" not in v
        ]  # 解题者有时把整条算式也当作一个答案写出来：忽略
        claimed = answer_values(inp.answers)
        compare = got
        if inp.kind == "choice":  # 选择题：解题者常把"选项内容"和"字母"一起写出，只比较字母
            letters = [v for v in got if re.fullmatch(r"\s*[（(]?[A-Da-d][)）]?\s*", v)]
            compare = letters or got
        ok = answers_equal(compare, claimed)
        ev: dict[str, Any] = {
            "blind": got,
            "claimed": claimed,
            "work": _clip(out.work, 700),
            "ambiguous": out.ambiguous,
            "model": res.model,
        }
        sp.set(match=ok, ambiguous=out.ambiguous)
        if not ok:
            return _res(
                "blind",
                CheckStatus.fail,
                f"独立求解得到 {'、'.join(got) or '（无答案）'}，与题目答案 {'、'.join(claimed)} 不一致",
                ambiguity=out.ambiguity,
                **ev,
            )
        if out.ambiguous:
            return _res(
                "blind", CheckStatus.warn, f"答案一致，但解题者认为题意可能有歧义：{out.ambiguity}", **ev
            )
        return _res("blind", CheckStatus.passed, **ev)


# ---- 能力边界（特征抽取 + 确定性判定）----
class FeaturesRaw(BaseModel):
    model_config = ConfigDict(extra="allow")


async def check_boundary(env: VerifyEnv, inp: VerifyInput) -> CheckResult:
    async with trace.check_span("boundary") as sp:
        if not inp.lesson_id:
            return _res("boundary", CheckStatus.skip, "没有参照课时")
        try:
            vocab = await env.kb.feature_vocab()
            built = get_prompt("verify.extract").render(
                stable=vocab,
                dynamic={"stem": inp.stem, "options": inp.options, "solution": inp.solution},
            )
            raw, res = await complete_json(
                env.llm,
                LLMRequest(
                    role=Role.extract, messages=built.messages, purpose="verify.extract", prompt=built.ref
                ),
                FeaturesRaw,
            )
            rep = await env.kb.check_features(raw.model_dump(), inp.lesson_id)
        except (LLMError, BudgetExceeded, KnowledgeError) as e:
            sp.set(degraded=e.code)
            return _res("boundary", CheckStatus.skip, f"边界核验不可用（{e.code}）")
        viol = [v.model_dump() for v in rep.violations]
        ev = {
            "verdict": rep.verdict,
            "violations": viol,
            "unknown": rep.unknown[:6],
            "lesson_id": inp.lesson_id,
            "features": raw.model_dump(),
            "model": res.model,
        }
        sp.set(verdict=rep.verdict)
        if rep.verdict == "out":
            desc = "；".join(
                f"{v.dimension}：{v.item_value}（{('在 ' + v.introduced_at + ' 才学') if v.introduced_at else '教材未引入'}）"
                for v in rep.violations[:4]
            )
            return _res("boundary", CheckStatus.fail, f"超出该课时之前学过的范围：{desc}", **ev)
        if rep.verdict == "borderline":
            return _res("boundary", CheckStatus.passed, "含同一单元内稍后才引入的内容，建议教师复核", **ev)
        return _res("boundary", CheckStatus.passed, **ev)


# ---- 题面质量与综合性（判官）----
class QualityOut(BaseModel):
    unambiguous: bool = True
    complete: bool = True
    data_plausible: bool = True
    age_ok: bool = True
    solution_ok: bool = True
    difficulty: int = 3
    kp_used: dict[str, bool] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)


_QUALITY_LABELS = {
    "unambiguous": "题意有歧义",
    "complete": "条件缺失或矛盾",
    "data_plausible": "数据不合理",
    "solution_ok": "解析与答案不一致或有错",
}


async def check_quality(env: VerifyEnv, inp: VerifyInput) -> tuple[CheckResult, CheckResult]:
    """返回（题面质量, 综合性）。一次判官调用产出两项结论。"""
    async with trace.check_span("quality") as sp:
        built = get_prompt("verify.quality").render(
            dynamic={
                "grade": _grade_text(inp.grade),
                "kp_names": inp.kp_names,
                "stem": inp.stem,
                "options": inp.options,
                "answer": format_answer(inp.answers),
                "solution": inp.solution,
            }
        )
        try:
            q, res = await complete_json(
                env.llm,
                LLMRequest(
                    role=Role.judge,
                    messages=built.messages,
                    purpose="verify.quality",
                    prompt=built.ref,
                    max_tokens=700,
                ),
                QualityOut,
            )
        except (LLMError, BudgetExceeded) as e:
            sp.set(degraded=e.code)
            skip = _res("quality", CheckStatus.skip, f"题面评审不可用（{e.code}）")
            return skip, _res("integration", CheckStatus.skip, "未评审")
        ev = q.model_dump() | {"model": res.model}
        bad = [label for key, label in _QUALITY_LABELS.items() if not getattr(q, key)]
        if inp.target_difficulty and q.difficulty <= inp.target_difficulty - 2:
            bad.append(
                f"题目偏简单（评审估计难度 {q.difficulty}，要求 {inp.target_difficulty}）：需要更多步骤、逆向、比较或取舍"
            )
        sp.set(n_bad=len(bad))
        if bad:
            quality = _res("quality", CheckStatus.fail, "；".join(bad + q.issues[:3]), **ev)
        elif not q.age_ok:
            quality = _res(
                "quality", CheckStatus.warn, "语言或情境可能不够适龄：" + "；".join(q.issues[:2]), **ev
            )
        else:
            quality = _res("quality", CheckStatus.passed, **ev)
        unused = [k for k, v in q.kp_used.items() if not v]
        if len(inp.kp_names) < 2:
            integration = _res("integration", CheckStatus.skip, "单知识点题")
        elif unused:
            st = CheckStatus.fail if inp.tier == "integrated" else CheckStatus.warn
            integration = _res("integration", st, "解答里并没有真正用到：" + "、".join(unused), unused=unused)
        else:
            integration = _res("integration", CheckStatus.passed)
        return quality, integration


# ---- 新颖度（确定性）----
_DIGITS = re.compile(r"\d+(?:\.\d+)?")
_NOISE = re.compile(r"[\s，。,.？?！!：:；;、（）()$\\{}_\-—]+")


def _shingles(text: str, n: int = 3) -> set[str]:
    t = _NOISE.sub("", _DIGITS.sub("#", text))
    return {t[i : i + n] for i in range(max(0, len(t) - n + 1))}


def similarity(a: str, b: str) -> float:
    sa, sb = _shingles(a), _shingles(b)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    return max(inter / len(sa | sb), inter / min(len(sa), len(sb)) * 0.9)  # Jaccard 与（打折的）包含度取大

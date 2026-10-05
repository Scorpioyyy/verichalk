"""构建核验评测集 `VerifyBank`（eval/specs/produce.md §2.2）：直接测验证器，不依赖写题模型。

组成：
- 正确题：教材题型实例化（chalkbase 的程序答案）；再经一个**与线上盲解不同厂商**的审计模型独立求解，答案一致才保留
  （剔除题面残缺 / 有歧义 / 模板缺陷的题）；
- 错误题：对正确题的答案做**程序化注入**（数位滑错、±1、小数点移位、运算选错、判断翻转），并为一部分错误题让写题厂商的模型
  写一份"看起来合理、实际在某一步出错"的解析（自洽的错误，最难检出）；
- 缺陷题：对正确题做构造式破坏（缺条件 / 条件矛盾 / 歧义 / 数据荒谬），标签由构造得到。

用法：python scripts/build_verify_bank.py [--n 300] [--profile intl]
产物：eval/datasets/verify/bank.yaml；模型响应录制在 eval/cassettes/verify_bank/（可复现）。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import random
import re
import sys
import warnings
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend" / "src"))

from verichalk.core.config import LLMMode, Profile, Settings  # noqa: E402
from verichalk.core.errors import VerichalkError  # noqa: E402
from verichalk.domain.llm import ChatMessage, Role  # noqa: E402
from verichalk.llm import LLMRequest, build_gateway, complete_json, get_prompt  # noqa: E402
from verichalk.verify.answers import answers_equal, parse_value  # noqa: E402
from verichalk.verify.checks import BlindOut  # noqa: E402

KIND = {
    "compute": "calc",
    "fill_blank": "fill",
    "word_problem": "application",
    "judge": "judge",
}
BAD_WORDS = re.compile(r"如图|下图|上图|图中|表格|\||画|量一量|数一数|摆|折|剪|拼")
ERR_TYPES = ["digit", "off1", "shift", "op", "flip"]


class Sol(BaseModel):
    solution: str


class Stem(BaseModel):
    stem: str


def sample_templates(n: int, seed: int) -> list[dict[str, Any]]:
    """从知识库的 program 题型里分层抽样并实例化（同步，较慢：每次实例化起一个沙箱子进程）。"""
    warnings.simplefilter("ignore")
    from chalkbase import Curriculum

    cur = Curriculum()
    rng = random.Random(seed)
    ats = [
        a
        for a in cur.archetypes(verifiable_type="program")
        if getattr(a.item_form, "value", a.item_form) in KIND
    ]
    rng.shuffle(ats)
    out: list[dict[str, Any]] = []
    per_grade: dict[int, int] = {}
    cap = n // 6 + 1
    for a in ats:
        try:
            lessons = cur.archetype_lessons(a.id)
            if not lessons:
                continue
            lid = lessons[-1]
            grade = cur.lesson_location(lid).grade
            if per_grade.get(grade, 0) >= cap:
                continue
            p = cur.instantiate(a.id, seed=rng.randint(0, 50), lesson_id=lid)
        except Exception:
            continue
        form = getattr(a.item_form, "value", a.item_form)
        v = p.answer_value
        if (
            p.warnings
            or p.verdict != "in"
            or BAD_WORDS.search(p.problem)
            or len(p.problem) > 150
        ):
            continue
        if isinstance(v, bool):
            if "判断" not in p.problem or "______" in p.problem:
                continue
            ans = "对" if v else "错"
        elif isinstance(v, int | Decimal | Fraction):
            ans = p.answer
            if form == "judge":
                continue
        else:
            continue
        if not ans or p.problem.count("______") > 1:
            continue
        per_grade[grade] = per_grade.get(grade, 0) + 1
        out.append(
            {
                "base": f"{a.id}#{p.seed}",
                "grade": grade,
                "lesson_id": lid,
                "kind": KIND[form],
                "stem": p.problem.strip(),
                "answer": str(ans),
                "params": {k: str(x) for k, x in (p.params or {}).items()},
                "kp": a.primary_knowledge_point_id,
            }
        )
        if len(out) >= n:
            break
    return out


def _hash_split(key: str) -> str:
    return "val" if int(hashlib.sha1(key.encode()).hexdigest(), 16) % 2 == 0 else "test"


def inject(item: dict[str, Any], rng: random.Random) -> list[tuple[str, str]]:
    """对一道正确题的答案做程序化注入，返回 [(错误类型, 错误答案)]（只返回确实不等于正确答案的）。"""
    true = parse_value(item["answer"])
    out: list[tuple[str, str]] = []
    if true in ("对", "错"):
        return [("flip", "错" if true == "对" else "对")]
    if not isinstance(true, Fraction):
        return []
    dec = Decimal(true.numerator) / Decimal(true.denominator)

    def fmt(x: Fraction | Decimal) -> str:
        if isinstance(x, Fraction):
            return (
                str(x.numerator)
                if x.denominator == 1
                else f"{x.numerator}/{x.denominator}"
            )
        s = format(x.normalize(), "f")
        return s

    # off1：最后一位 ±1
    if true.denominator == 1:
        step = Fraction(rng.choice([1, 1, 10]))
    else:
        step = Fraction(
            1,
            10
            ** max(
                1, len(item["answer"].split(".")[-1]) if "." in item["answer"] else 1
            ),
        )
    out.append(("off1", fmt(true + step * rng.choice([-1, 1]))))
    # shift：小数点移位
    out.append(("shift", fmt(true * rng.choice([10, Fraction(1, 10), 100]))))
    # digit：改一位数字
    digits = [i for i, ch in enumerate(item["answer"]) if ch.isdigit()]
    if digits:
        i = rng.choice(digits)
        d = (int(item["answer"][i]) + rng.randint(1, 8)) % 10
        s = item["answer"][:i] + str(d) + item["answer"][i + 1 :]
        if not (s.startswith("0") and len(s) > 1 and s[1].isdigit()):
            out.append(("digit", s))
    # op：用题里的两个数换一种运算
    nums = [
        Fraction(Decimal(v))
        for v in item["params"].values()
        if re.fullmatch(r"-?\d+(\.\d+)?", v)
    ]
    if len(nums) >= 2:
        a, b = nums[0], nums[1]
        cands = [a + b, abs(a - b), a * b] + ([a / b] if b else [])
        cands = [c for c in cands if c != true and c > 0]
        if cands:
            out.append(("op", fmt(rng.choice(cands))))
    del dec
    return [(t, s) for t, s in out if s and not answers_equal([s], [item["answer"]])]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--profile", default="intl", choices=["cn", "intl"])
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    print("采样教材题型并实例化……")
    bases = await asyncio.to_thread(sample_templates, int(args.n * 1.5), args.seed)
    print(f"候选正确题 {len(bases)}")

    s = Settings(
        profile=Profile(args.profile),
        llm_mode=LLMMode.record,
        cassette_namespace="verify_bank",
        llm_concurrency=8,
    )
    gw = build_gateway(s)
    sem = asyncio.Semaphore(8)
    # 审计模型：与线上盲解（deepseek）不同厂商，思考模式
    gw.registry = gw.registry.override(
        Role.solver,
        model="qwen3.8-max",
        thinking=True,
        temperature=0.0,
        max_tokens=6000,
    )

    async def audit(b: dict[str, Any]) -> bool:
        built = get_prompt("verify.blind").render(
            dynamic={"grade": f"{b['grade']}年级", "stem": b["stem"], "options": []}
        )
        async with sem:
            try:
                out, _ = await complete_json(
                    gw,
                    LLMRequest(
                        role=Role.solver,
                        messages=built.messages,
                        purpose="bank.audit",
                        prompt=built.ref,
                    ),
                    BlindOut,
                )
            except VerichalkError:
                return False
        return answers_equal([a.value for a in out.answers], [b["answer"]])

    keep_flags = await asyncio.gather(*(audit(b) for b in bases))
    bases = [b for b, k in zip(bases, keep_flags, strict=True) if k][: args.n]
    print(f"审计一致后保留 {len(bases)}")

    items: list[dict[str, Any]] = []
    for i, b in enumerate(bases):
        items.append(
            {
                "id": f"vb-c{i:03d}",
                "split": _hash_split(b["base"]),
                "label": "correct",
                "error_type": None,
                "base": b["base"],
                "grade": b["grade"],
                "lesson_id": b["lesson_id"],
                "kind": b["kind"],
                "stem": b["stem"],
                "options": [],
                "answers": [b["answer"]],
                "gold": [b["answer"]],
                "solution": "",
            }
        )
    # 错误题
    wrong: list[dict[str, Any]] = []
    for it, b in zip(list(items), bases, strict=True):
        cands = inject({"answer": b["answer"], "params": b["params"]}, rng)
        rng.shuffle(cands)
        for et, bad in cands[:2]:
            wrong.append(
                {
                    **it,
                    "id": f"vb-w{len(wrong):03d}",
                    "label": "wrong",
                    "error_type": et,
                    "answers": [bad],
                }
            )
    items += wrong

    # 自洽的错误解析（写题厂商的模型扮演"在某一步出错"）与正确解析
    gw.registry = gw.registry.override(Role.smart, temperature=0.7, max_tokens=900)

    async def write_solution(it: dict[str, Any], *, wrong_answer: bool) -> str:
        if wrong_answer:
            prompt = (
                "下面是一道小学数学题。请扮演一位**在某一步出现细微错误**的老师，写一份看起来合理、逐步推导的参考解析，"
                f"但推导过程中出现一个不易察觉的错误（如进位 / 借位错误、小数点位置错、看错一个数、漏一步），最终得出**指定的答案：{it['answers'][0]}**。"
                "解析要自洽、语气像正常的参考解析，不要提示或暗示有错误，不超过 150 字。\n"
                f'题目：{it["stem"]}\n只输出 JSON：{{"solution": "…"}}'
            )
        else:
            prompt = (
                f"下面是一道小学数学题，正确答案是 {it['answers'][0]}。请写一份简洁、正确的参考解析（不超过 150 字），"
                "最后得出这个答案。\n"
                f'题目：{it["stem"]}\n只输出 JSON：{{"solution": "…"}}'
            )
        async with sem:
            try:
                out, _ = await complete_json(
                    gw,
                    LLMRequest(
                        role=Role.smart,
                        purpose="bank.solution",
                        messages=[ChatMessage(role="user", content=prompt)],
                    ),
                    Sol,
                )
            except VerichalkError:
                return ""
        return out.solution.strip()

    with_sol_w = [
        it for it in items if it["label"] == "wrong" and it["error_type"] != "flip"
    ]
    rng.shuffle(with_sol_w)
    with_sol_w = with_sol_w[: len(with_sol_w) * 2 // 5]
    with_sol_c = [it for it in items if it["label"] == "correct"][
        : len(with_sol_w) // 2 + 20
    ]
    sols = await asyncio.gather(
        *(write_solution(it, wrong_answer=True) for it in with_sol_w),
        *(write_solution(it, wrong_answer=False) for it in with_sol_c),
    )
    extra: list[dict[str, Any]] = []
    for it, sol in zip(with_sol_w + with_sol_c, sols, strict=True):
        if sol and "错" not in sol[:6]:
            wrongish = it["label"] == "wrong"
            extra.append(
                {
                    **it,
                    "id": it["id"] + "s",
                    "solution": sol,
                    "error_type": (it["error_type"] + "+solution")
                    if wrongish
                    else None,
                }
            )
    items += extra

    # 缺陷题
    defect_types = {
        "missing": "删去解题必需的一个已知条件（保持题面通顺，仍然在问同一个问题）",
        "contradict": "加入一条与已有条件相矛盾的数据，使题目无法同时满足",
        "ambiguous": "把问法改得含糊，使得有两种都说得通的理解，且两种理解的答案不同",
        "absurd": "把其中一个数据改成不合常理的值（如价格 0.003 元、年龄 150 岁、人数 3.5 个），其余不变",
    }
    word = [
        it
        for it in items
        if it["label"] == "correct"
        and it["kind"] in ("application", "calc")
        and len(it["stem"]) > 18
    ]
    rng.shuffle(word)

    async def make_defect(it: dict[str, Any], dtype: str) -> dict[str, Any] | None:
        prompt = (
            f"对下面这道小学数学题做一个破坏：{defect_types[dtype]}。只改题面，不要添加解释。\n题目：{it['stem']}\n"
            '只输出 JSON：{"stem": "破坏后的题面"}'
        )
        async with sem:
            try:
                out, _ = await complete_json(
                    gw,
                    LLMRequest(
                        role=Role.smart,
                        purpose="bank.defect",
                        messages=[ChatMessage(role="user", content=prompt)],
                    ),
                    Stem,
                )
            except VerichalkError:
                return None
        if out.stem.strip() == it["stem"] or len(out.stem) > 220:
            return None
        return {
            **it,
            "id": f"vb-d{len(extra)}-{dtype}-{it['id'][-3:]}",
            "label": "defect",
            "error_type": dtype,
            "stem": out.stem.strip(),
            "gold": [],
        }

    plan = [(it, list(defect_types)[i % 4]) for i, it in enumerate(word[:80])]
    defects = [
        d for d in await asyncio.gather(*(make_defect(it, t) for it, t in plan)) if d
    ]
    items += defects

    head = (
        "# 核验评测集：由 scripts/build_verify_bank.py 生成（见 eval/specs/produce.md §2.2）。\n"
        "# label: correct（答案正确）/ wrong（答案被注入错误）/ defect（题面被构造性破坏）；answers 是被核验的答案，gold 是正确答案。\n"
    )
    path = ROOT / "eval" / "datasets" / "verify" / "bank.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    yaml.SafeDumper.ignore_aliases = lambda self, data: True  # type: ignore[method-assign]
    with path.open("w", encoding="utf-8") as f:
        f.write(head)
        yaml.safe_dump(
            items,
            f,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=None,
            width=140,
        )
    from collections import Counter

    print(Counter((x["label"], x["split"]) for x in items))
    print(Counter(x["error_type"] for x in items if x["label"] != "correct"))
    print(f"写入 {path}（{len(items)} 项）")


if __name__ == "__main__":
    asyncio.run(main())

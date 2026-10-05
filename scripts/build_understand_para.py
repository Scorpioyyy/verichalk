"""构建"换说法"评测集 `understand_para`：对作者撰写的单轮用例，由不同厂商的模型改写说法，保持意图不变。

动机（eval/CHANGELOG.md）：用例由规则解析的作者撰写，说法偏规整，区分度不足。改写由 `judge` 角色（deepseek 系）完成，
被测的理解模型是 qwen 系，二者不同源；改写后由两个模型逐条核验"期望里的每个事实仍然成立、没有新增信息"，两个都通过才入集。

用法：python scripts/build_understand_para.py [--profile intl]
产物：eval/datasets/cases/understand_para.yaml；模型响应录制在 eval/cassettes/build_para/（可复现）。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import yaml
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend" / "src"))

from verichalk.core.config import LLMMode, Profile, Settings  # noqa: E402
from verichalk.core.errors import VerichalkError  # noqa: E402
from verichalk.domain.llm import ChatMessage, Role  # noqa: E402
from verichalk.llm import LLMRequest, build_gateway, complete_json  # noqa: E402

SKIP_TAGS = {"inject", "english", "emoji", "typo", "answered", "multi"}


def junk(text: str) -> bool:
    """无意义输入（如 "。。。"）不适合改写：改写模型会脑补内容。"""
    import re

    return not re.search(r"[一-龥]", text) or len(text.strip()) < 3
KIND_CN = {"fill": "填空题", "choice": "选择题", "calc": "计算题", "judge": "判断题", "application": "应用题"}
TIER_CN = {"consolidate": "巩固基础", "variation": "变式", "integrated": "综合"}
ABSENT_CN = {"count": "题量", "difficulty": "难度要求", "kinds": "题型", "tier": "题目档位（巩固/变式/综合）", "scenes": "情境", "constraints": "其他限制", "source": "对题目来源的要求", "topics": "具体知识点"}


class Paras(BaseModel):
    a: str
    b: str


class Verdict(BaseModel):
    all_true: bool
    failed: list[str] = []


def facts_of(exp: dict) -> list[str]:
    """把期望翻译成"改写后仍应成立 / 不应新增"的自然语言事实，供核验。"""
    out: list[str] = []
    b = exp.get("brief") or {}
    if "grade" in b:
        out.append(f"年级是{b['grade']}年级（或能由所说内容唯一确定）")
    if "semester" in b:
        out.append("学期是" + ("上册" if b["semester"] == "a" else "下册"))
    if "unit" in b:
        out.append(f"指定了第{b['unit']}单元")
    if "count" in b:
        out.append(f"题量是{b['count']}道")
    if "difficulty" in b:
        d = b["difficulty"]
        out.append("难度要求：" + ("偏简单/基础" if d.get("max") and not d.get("min") else "偏难/有挑战" if d.get("min", 0) >= 4 else "有一定难度" if d.get("min") == 3 else "中等"))
    for k in b.get("kinds", []) + b.get("kinds_include", []):
        out.append(f"题型包含{KIND_CN[k]}")
    if "tier" in b:
        out.append(f"倾向于{TIER_CN[b['tier']]}类题目")
    if "source" in b:
        out.append("要求" + ("课本上那种基础题（同类批量题）" if b["source"] == "template" else "有创意、不是课本原题"))
    if "scenes_any" in b:
        out.append(f"情境与{'/'.join(b['scenes_any'])}之一有关")
    if "constraints_any" in b:
        out.append(f"有限制：{'/'.join(b['constraints_any'])}（意思相同即可）")
    if "topics_any" in b:
        out.append(f"知识点与「{'」「'.join(b['topics_any'])}」之一有关")
    if "paper" in b:
        p = b["paper"]
        out.append("整卷参数：" + "，".join(f"{'时长' if k == 'duration_min' else '总分'}{v}" for k, v in p.items()))
    if exp.get("route") == "paper":
        out.append("要的是一整份试卷（不是几道题）")
    if b.get("action") == "review":
        out.append("带有复习性质（把以前学过的内容穿插进来）")
    out += [f"**没有**提到{ABSENT_CN[a]}" for a in exp.get("absent", []) if a in ABSENT_CN]
    if exp.get("clarify") and exp.get("route") in ("generate", "paper"):
        out.append("没有说明年级、知识点或单元（范围缺失）")
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="intl", choices=["cn", "intl"])
    args = ap.parse_args()
    s = Settings(profile=Profile(args.profile), llm_mode=LLMMode.record, cassette_namespace="build_para", llm_concurrency=8)
    gw = build_gateway(s)
    gw.registry = gw.registry.override(Role.judge, temperature=0.9, max_tokens=600)
    base = yaml.safe_load((ROOT / "eval/datasets/cases/understand.yaml").read_text(encoding="utf-8"))
    cases = [c for c in base if len(c["turns"]) == 1 and not SKIP_TAGS & set(c["tags"]) and not junk(c["turns"][0]["user"])]
    out: list[dict] = []
    sem = asyncio.Semaphore(8)

    async def paraphrase(case: dict) -> Paras | None:
        text = case["turns"][0]["user"]
        prompt = (
            "你是一位小学数学老师，正在给一个AI命题助手发请求。请把下面这句话用**完全不同的说法**重写两次（a、b），"
            "语气像真实老师：a 要口语化、简短、可以省略；b 要稍啰嗦、带一点背景或语气词。\n"
            "硬性要求：保留原话里的**全部信息**（年级学期、知识点、题量、难度、题型、情境、限制、整卷参数……），"
            "不新增任何原话没有的信息，不遗漏；数字可以用汉字或阿拉伯数字；不要照抄原句的连续五个字以上。\n"
            f"原话：{text}\n只输出 JSON：{{\"a\": \"…\", \"b\": \"…\"}}"
        )
        async with sem:
            try:
                p, _ = await complete_json(gw, LLMRequest(role=Role.judge, purpose="para.gen", messages=[ChatMessage(role="user", content=prompt)]), Paras)
                return p
            except VerichalkError:
                return None

    async def verify(role: Role, original: str, para: str, facts: list[str]) -> bool:
        listing = "\n".join(f"{i + 1}. {f}" for i, f in enumerate(facts)) or "（无）"
        prompt = (
            "判断下面“改写后的话”是否逐条满足事实清单。每条事实必须被改写后的话明确表达（或在“没有提到”类事实里确实没有提到）。\n"
            f"原话：{original}\n改写后：{para}\n事实清单：\n{listing}\n"
            '只输出 JSON：{"all_true": true/false, "failed": ["不满足的事实编号或原文"]}'
        )
        async with sem:
            try:
                v, _ = await complete_json(gw, LLMRequest(role=role, purpose="para.verify", messages=[ChatMessage(role="user", content=prompt)], temperature=0.0), Verdict)
                return v.all_true
            except VerichalkError:
                return False

    async def build(case: dict) -> list[dict]:
        text, exp = case["turns"][0]["user"], case["turns"][0]["expect"]
        paras = await paraphrase(case)
        if paras is None:
            return []
        facts = facts_of(exp)
        res = []
        for tag, p in (("a", paras.a.strip()), ("b", paras.b.strip())):
            if not p or p == text or len(p) > 200 or "××" in p:
                continue
            ok1 = await verify(Role.smart, text, p, facts)  # qwen3.8-max
            ok2 = await verify(Role.judge, text, p, facts)  # deepseek
            if ok1 and ok2:
                new = {k: v for k, v in case.items() if k != "turns"}
                new["id"] = f"p-{case['id'][2:]}-{tag}"
                new["tags"] = [*case["tags"], "para"]
                new["turns"] = [{**case["turns"][0], "user": p}]
                res.append(new)
        return res

    built = await asyncio.gather(*(build(c) for c in cases))
    for r in built:
        out += r
    head = ("# 意图理解评测集（换说法版）：由 scripts/build_understand_para.py 生成。\n"
            "# 对作者撰写的单轮用例，由不同厂商的模型改写说法（意图与期望不变），并经两个模型核验后入集。\n")
    path = ROOT / "eval/datasets/cases/understand_para.yaml"
    with path.open("w", encoding="utf-8") as f:
        f.write(head)
        yaml.SafeDumper.ignore_aliases = lambda self, data: True  # type: ignore[method-assign]  展开共享对象，避免 YAML 锚点
        yaml.safe_dump(out, f, allow_unicode=True, sort_keys=False, default_flow_style=None, width=140)
    n_in = len(cases)
    print(f"基础用例 {n_in} 条 → 改写入集 {len(out)} 条（每条最多 2 个改写；通过率 {len(out) / (2 * n_in):.0%}）")


if __name__ == "__main__":
    asyncio.run(main())

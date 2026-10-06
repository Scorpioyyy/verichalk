"""自然语言编辑阶段（architecture §5 `edit`，指标 C1）：一句话 → 编辑计划 → 补丁 → 复核。

分工：模型只把教师的话译成**编辑计划**（小词表里的动作 + 对第几题 + 参数）；其余都是确定性代码：
- 目标限定：教师点名的题（理解阶段给出）是硬范围，未点名的题不会进入执行——"附带破坏"在结构上被排除；
- 改写（换情境 / 调难度 / 改题型 / 调数字）复用生成阶段（写题 → 核验 → 修复），所以"复核通过"免费获得；
- 删除 / 移动 / 交换 / 分值 / 标题是纯规则；
- 所有改动合成**一个补丁、一条修订**，教师一次撤销就回到改前。
"""

from __future__ import annotations

import asyncio
import logging
import re

from pydantic import BaseModel

from .. import trace
from ..core.errors import Conflict, KnowledgeError, LLMError, NotFound
from ..core.ids import new_id
from ..domain.blueprint import ItemSpec
from ..domain.edit import EditAction, EditItemResult, EditOut, EditPlan
from ..domain.llm import Role
from ..domain.paper import Item, Paper, Tier, Verification, VerifyStatus
from ..domain.paper_ops import MoveItem, Op, RemoveItem, ReplaceField, SetMeta, SetTitle
from ..domain.paper_rules import distribute_scores
from ..llm import LLMRequest, complete_json, get_prompt
from .base import RunContext, Stage, run_stage
from .paper_edit import commit_ops
from .plan import _GRADE_CN, _KIND_CN
from .produce import ProduceIn, ProduceStage
from .review import reverify

log = logging.getLogger("verichalk.edit")

_REWRITE_CAP = 12  # 一次最多改写的题数（整卷降难度也不超过它；超出的提示教师分批）


class EditIn(BaseModel):
    instruction: str
    target: str = ""  # 理解阶段给出的目标："item:3" / "items:1,3" / "all" / "kind:choice"


class _TextOut(BaseModel):
    text: str


# ---- 目标解析（确定性）----
def resolve_targets(paper: Paper, target: str) -> list[int] | None:
    """把理解阶段给出的目标变成题号列表；无法解析返回 None（此时由计划里的题号决定）。"""
    n = len(paper.all_items())
    t = target.strip().lower()
    if t in ("all", "全部", "整卷"):
        return list(range(1, n + 1))
    m = re.fullmatch(r"items?:\s*([\d,\s、，]+)", t)
    if m:
        nums = [int(x) for x in re.findall(r"\d+", m.group(1))]
        return sorted(set(nums))
    m = re.fullmatch(r"kind:\s*(\w+)", t)
    if m:
        kind = m.group(1)
        return [i for i, it in enumerate(paper.all_items(), start=1) if it.kind.value == kind]
    return None


# ---- 规则快速通道：整句都是确定性动作时不调用模型 ----
_NUMS = r"\d+(?:\s*[、,，和与及]\s*\d+)*"
_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"(?:把)?(?:删除|删掉|去掉|去除)第\s*({_NUMS})\s*题"), "remove"),
    (
        re.compile(r"(?:把)?第\s*(\d+)\s*题(?:和|与|跟)第\s*(\d+)\s*题(?:交换|对调|互换|换位置|换个位置)"),
        "swap",
    ),
    (re.compile(r"(?:交换|对调|互换)第\s*(\d+)\s*题(?:和|与|跟)第\s*(\d+)\s*题"), "swap"),
    (re.compile(r"(?:试卷)?标题(?:改成|改为|改作|换成|设为|叫)\s*[「“\"]?(.+?)[」”\"]?"), "title"),
    (re.compile(r"总分(?:改成|改为|设为|定为|调整为)\s*(\d+(?:\.\d+)?)\s*分?"), "total"),
    (re.compile(r"每题\s*(\d+(?:\.\d+)?)\s*分"), "score_all"),
]


def rule_plan(text: str) -> EditPlan | None:
    clauses = [
        c.strip()
        for c in re.split(r"[，,；;。！!\n]+(?=\s*(?:把)?(?:删|去|交换|对调|互换|第|标题|总分|每题))", text)
        if c.strip()
    ]
    if not clauses:
        return None
    actions: list[EditAction] = []
    for c in clauses:
        for pat, kind in _RULES:
            m = pat.fullmatch(c)
            if not m:
                continue
            if kind == "remove":
                actions.append(
                    EditAction(op="remove", items=[int(x) for x in re.findall(r"\d+", m.group(1))])
                )
            elif kind == "swap":
                actions.append(EditAction(op="swap", items=[int(m.group(1)), int(m.group(2))]))
            elif kind == "title":
                actions.append(EditAction(op="set_title", title=m.group(1).strip()))
            elif kind == "total":
                actions.append(EditAction(op="set_total", score=float(m.group(1))))
            else:
                actions.append(EditAction(op="set_score", items=[], score=float(m.group(1))))
            break
        else:
            return None  # 有一句规则吃不下：整句交给模型
    return EditPlan(actions=actions)


# ---- 模型：译成编辑计划 ----
async def plan_edit(ctx: RunContext, paper: Paper, instruction: str, targets: list[int] | None) -> EditPlan:
    rows = [
        {
            "number": i,
            "kind": _KIND_CN[it.kind],
            "difficulty": it.difficulty,
            "stem": re.sub(r"\s+", " ", it.stem)[:50],
        }
        for i, it in enumerate(paper.all_items(), start=1)
    ]
    tgt = "、".join(f"第 {n} 题" for n in targets) if targets else "（没有明确指出，请根据要求判断）"
    built = get_prompt("edit.plan").render(
        dynamic={"n": len(rows), "rows": rows, "targets": tgt, "instruction": instruction}
    )
    plan, _ = await complete_json(
        ctx.llm,
        LLMRequest(
            role=Role.fast, messages=built.messages, purpose="edit.plan", prompt=built.ref, max_tokens=900
        ),
        EditPlan,
    )
    return plan


def validate_plan(plan: EditPlan, paper: Paper, targets: list[int] | None) -> EditPlan:
    """题号必须在试卷范围内；教师点名的题是硬范围（未点名的题不进入执行）。"""
    n = len(paper.all_items())
    if plan.unsupported or not plan.actions:
        return plan
    keep: list[EditAction] = []
    problems: list[str] = []
    for a in plan.actions:
        items = list(dict.fromkeys(a.items))
        bad = [i for i in items if not 1 <= i <= n]
        if bad:
            problems.append(f"试卷里只有 {n} 道题，没有第 {'、'.join(map(str, bad))} 题")
            continue
        if a.op in ("rewrite", "rewrite_text", "remove", "set_score") and items and targets:
            outside = [i for i in items if i not in targets]
            if outside:  # 教师没点名这些题：不动
                items = [i for i in items if i in targets]
        if a.op == "set_score" and not items and targets and len(targets) < n:
            items = list(targets)
        if a.op in ("rewrite", "rewrite_text", "remove") and not items:
            problems.append("没有找到要修改的题，请告诉我是第几题")
            continue
        if a.op == "swap" and len(items) != 2:
            problems.append("交换需要指明两道题")
            continue
        if a.op == "move" and (len(items) != 1 or a.to is None or not 1 <= a.to <= n):
            problems.append("移动需要指明哪一道题、移到第几题的位置")
            continue
        if a.op in ("set_score", "set_total") and (a.score is None or a.score <= 0):
            problems.append("需要给出分值")
            continue
        if a.op == "set_title" and not a.title.strip():
            problems.append("需要给出新的标题")
            continue
        keep.append(a.model_copy(update={"items": items}))
    if not keep:
        return EditPlan(
            unsupported="；".join(dict.fromkeys(problems)) or "没有听明白要怎么改，能再说具体一点吗？"
        )
    return EditPlan(actions=keep, unsupported="")


# ---- 位置计算（确定性）----
def _flat(paper: Paper) -> list[tuple[str, str]]:
    return [(s.id, it.id) for s in paper.sections for it in s.items]


def move_op(
    flat: list[tuple[str, str]], item_id: str, to_number: int
) -> tuple[MoveItem, list[tuple[str, str]]]:
    """把 item 移到"第 to_number 题"的位置，返回补丁与移动后的位置表。只有被移动的题自己变化。"""
    cur = [p for p in flat if p[1] != item_id]
    sec_of = next(s for s, i in flat if i == item_id)
    pos = max(0, min(to_number - 1, len(cur)))
    if pos < len(cur):
        sec = cur[pos][0]
    else:
        sec = cur[-1][0] if cur else sec_of
    index = sum(1 for s, _ in cur[:pos] if s == sec)
    new_flat = [*cur[:pos], (sec, item_id), *cur[pos:]]
    return MoveItem(item_id=item_id, section_id=sec, index=index), new_flat


# ---- 改写 ----
def _target_difficulty(item: Item, a: EditAction) -> int:
    if a.difficulty:
        return a.difficulty
    return min(5, max(1, item.difficulty + (a.difficulty_delta or 0)))


def _rewrite_instruction(item: Item, a: EditAction, difficulty: int) -> str:
    bits = [a.instruction.strip()] if a.instruction.strip() else []
    if a.kind and a.kind != item.kind:
        bits.append(f"题型改成{_KIND_CN[a.kind]}")
    if a.scene:
        bits.append(f"情境换成「{a.scene}」")
    if difficulty != item.difficulty:
        bits.append(f"难度由 {item.difficulty} 调整为 {difficulty}（1～5）")
    return "；".join(bits) or "换一种问法或情境，考查的知识点与难度不变"


def _fields_changed(old: Item, new: Item) -> list[str]:
    names = {
        "stem": "题干",
        "options": "选项",
        "answer": "答案",
        "solution": "解析",
        "kind": "题型",
        "difficulty": "难度",
    }
    return [cn for f, cn in names.items() if getattr(old, f) != getattr(new, f)]


class EditStage(Stage[EditIn, EditOut]):
    name = "edit"
    input_model = EditIn
    output_model = EditOut

    async def run(self, ctx: RunContext, inp: EditIn) -> EditOut:
        paper = await ctx.store.papers.get_current(ctx.session_id)
        if paper is None or not paper.all_items():
            return EditOut(unsupported="还没有试卷可以修改，先出几道题吧。")
        targets = resolve_targets(paper, inp.target)
        plan = rule_plan(inp.instruction)
        if plan is None:
            try:
                plan = await plan_edit(ctx, paper, inp.instruction, targets)
            except LLMError as e:
                log.warning("edit.plan failed: %s", e.code)
                return EditOut(unsupported="这次没能理解修改要求，请换个说法再试一次。")
        plan = validate_plan(plan, paper, targets)
        if plan.unsupported or not plan.actions:
            return EditOut(unsupported=plan.unsupported or "没有听明白要怎么改，能再说具体一点吗？")
        return await self._execute(ctx, paper, plan)

    async def _execute(self, ctx: RunContext, paper: Paper, plan: EditPlan) -> EditOut:
        items = paper.all_items()
        by_no = {i: it for i, it in enumerate(items, start=1)}
        results: list[EditItemResult] = []
        ops: list[Op] = []
        remove_ops: list[Op] = []  # 删除放在最后：移动的位置是按"改前的完整试卷"算的
        removed = {n for a in plan.actions if a.op == "remove" for n in a.items}
        # 先做确定性动作
        flat = _flat(paper)
        for a in plan.actions:
            if a.op == "remove":
                for n in a.items:
                    remove_ops.append(RemoveItem(item_id=by_no[n].id))
                    results.append(
                        EditItemResult(number=n, item_id=by_no[n].id, op="remove", ok=True, detail="已删除")
                    )
            elif a.op == "swap":
                x, y = a.items
                mv1, flat = move_op(flat, by_no[x].id, y)
                ops.append(mv1)
                mv2, flat = move_op(flat, by_no[y].id, x)
                ops.append(mv2)
                results += [
                    EditItemResult(
                        number=x, item_id=by_no[x].id, op="swap", ok=True, detail=f"已与第 {y} 题交换位置"
                    ),
                    EditItemResult(
                        number=y, item_id=by_no[y].id, op="swap", ok=True, detail=f"已与第 {x} 题交换位置"
                    ),
                ]
            elif a.op == "move":
                n = a.items[0]
                mv, flat = move_op(flat, by_no[n].id, a.to or n)
                ops.append(mv)
                results.append(
                    EditItemResult(
                        number=n, item_id=by_no[n].id, op="move", ok=True, detail=f"已移到第 {a.to} 题的位置"
                    )
                )
            elif a.op == "set_score":
                nums = a.items or list(by_no)
                for n in nums:
                    if n in removed:
                        continue
                    if by_no[n].score != a.score:
                        ops.append(ReplaceField(item_id=by_no[n].id, field="score", value=a.score))
                results.append(
                    EditItemResult(
                        number=nums[0],
                        item_id=by_no[nums[0]].id,
                        op="set_score",
                        ok=True,
                        detail=f"{len(nums)} 道题的分值设为 {a.score:g} 分",
                    )
                )
            elif a.op == "set_total":
                total = a.score or 0
                kept = [(n, it) for n, it in by_no.items() if n not in removed]
                if total != int(total) or total < len(kept):
                    results.append(
                        EditItemResult(
                            number=0,
                            item_id="",
                            op="set_total",
                            ok=False,
                            detail="总分需要是不小于题数的整数",
                        )
                    )
                else:
                    scores = distribute_scores([it.kind for _, it in kept], int(total))
                    for (_n, it), s in zip(kept, scores, strict=True):
                        if it.score != s:
                            ops.append(ReplaceField(item_id=it.id, field="score", value=float(s)))
                    ops.append(SetMeta(key="total_score", value=int(total)))
                    results.append(
                        EditItemResult(
                            number=0,
                            item_id="",
                            op="set_total",
                            ok=True,
                            detail=f"总分设为 {int(total)} 分，已按题型重新分配",
                        )
                    )
            elif a.op == "set_title":
                ops.append(SetTitle(title=a.title.strip()))
                results.append(
                    EditItemResult(
                        number=0, item_id="", op="set_title", ok=True, detail=f"标题改为「{a.title.strip()}」"
                    )
                )
        # 再做需要模型的改写（并行）
        jobs: list[tuple[int, EditAction]] = []
        seen: set[int] = set()
        for a in plan.actions:
            if a.op in ("rewrite", "rewrite_text"):
                for n in a.items:
                    if n not in removed and n not in seen:
                        seen.add(n)
                        jobs.append((n, a))
        if len(jobs) > _REWRITE_CAP:
            skipped = jobs[_REWRITE_CAP:]
            jobs = jobs[:_REWRITE_CAP]
            for n, a in skipped:
                results.append(
                    EditItemResult(
                        number=n,
                        item_id=by_no[n].id,
                        op=a.op,
                        ok=False,
                        detail=f"一次最多改 {_REWRITE_CAP} 道题，这道请再说一次",
                    )
                )
        sem = asyncio.Semaphore(ctx.settings.item_concurrency)
        total = len(jobs)
        done = 0
        if jobs:
            await trace.progress("开始修改题目", 0, total)

        async def one(n: int, a: EditAction) -> tuple[list[Op], EditItemResult]:
            nonlocal done
            async with sem:
                try:
                    out = await (
                        self._rewrite(ctx, paper, by_no[n], n, a)
                        if a.op == "rewrite"
                        else self._rewrite_text(ctx, paper, by_no[n], n, a)
                    )
                except Exception as e:  # 一道题改失败不影响其他题
                    log.warning("edit item %s failed: %s", by_no[n].id, e)
                    out = (
                        [],
                        EditItemResult(
                            number=n,
                            item_id=by_no[n].id,
                            op=a.op,
                            ok=False,
                            detail="这道题没有改成功，已保持原样",
                        ),
                    )
            done += 1
            await trace.progress(f"已修改 {done}/{total} 道题", done, total)
            return out

        for new_ops, res in await asyncio.gather(*(one(n, a) for n, a in jobs)):
            ops += new_ops
            results.append(res)
        results.sort(key=lambda r: (r.number == 0, r.number))
        ops += remove_ops
        if not ops:
            return EditOut(results=results, summary="没有改动试卷")
        summary = "；".join(
            f"第 {r.number} 题：{r.detail}" if r.number else r.detail for r in results if r.ok
        )
        try:
            done_c = await commit_ops(
                ctx.store,
                ctx.session_id,
                paper,
                ops,
                author="agent",
                summary=summary[:200],
                run_id=ctx.run_id,
            )
        except Conflict:
            return EditOut(results=results, unsupported="试卷在修改的过程中被改动了，请再说一次。")
        await trace.paper_patched(done_c.paper.id, done_c.revision.rev, done_c.revision.patch, summary[:200])
        for r in results:
            if r.ok and r.status is not None:
                found = done_c.paper.find_item(r.item_id)
                if found:
                    await trace.item_status(r.item_id, r.status, found[2].verification.checks)
        return EditOut(results=results, summary=summary, rev=done_c.revision.rev)

    # ---- 改写：复用生成阶段 ----
    async def _kp_names(self, ctx: RunContext, item: Item) -> list[str]:
        names: list[str] = []
        for kid in item.kp_ids[:3]:
            try:
                names.append((await ctx.kb.kp(kid)).name)
            except (KnowledgeError, NotFound):
                names.append(kid)
        return names

    async def _rewrite(
        self, ctx: RunContext, paper: Paper, item: Item, n: int, a: EditAction
    ) -> tuple[list[Op], EditItemResult]:
        difficulty = _target_difficulty(item, a)
        if (
            a.difficulty_delta
            and a.difficulty_delta < 0
            and item.difficulty <= 1
            and not (a.kind or a.scene or a.instruction.strip().replace("降低难度", ""))
        ):
            return [], EditItemResult(
                number=n,
                item_id=item.id,
                op="rewrite",
                ok=False,
                detail="这道题已经是最简单的难度了，没有改动",
            )
        spec = ItemSpec(
            id=new_id("ed"),
            index=n,
            kp_ids=item.kp_ids,
            kp_names=await self._kp_names(ctx, item),
            tier=Tier.integrated
            if len(item.kp_ids) > 1
            else item.tier,  # 多个知识点的题改写后仍要同时用到它们
            difficulty=difficulty,
            kind=a.kind or item.kind,
            scene=a.scene,
        )
        grade = paper.meta.get("grade")
        lesson = paper.meta.get("lesson_id")
        others = [re.sub(r"\s+", " ", o.stem)[:40] for o in paper.all_items() if o.id != item.id]
        pin = ProduceIn(
            spec=spec,
            grade=grade if isinstance(grade, int) else None,
            lesson_id=lesson if isinstance(lesson, str) else None,
            avoid=others[:6],
            rewrite={
                "stem": item.stem,
                "options": item.options,
                "answer": item.answer,
                "solution": item.solution,
                "instruction": _rewrite_instruction(item, a, difficulty),
                "from_difficulty": item.difficulty,
            },
        )
        out = await run_stage(ctx, ProduceStage(), pin, key=f"edit:{item.id}")
        if out.item is None or out.item.verification.status == VerifyStatus.needs_review:
            # 教师明确要改这道题，值得再整体试一次：生成阶段的重试已用尽，或只得到"需复核"（如知识点没真正用上）
            again = await run_stage(ctx, ProduceStage(), pin, key=f"edit:{item.id}:retry")
            if again.item is not None and (
                out.item is None or again.item.verification.status != VerifyStatus.needs_review
            ):
                out = again
        new = out.item
        if new is None:
            return [], EditItemResult(
                number=n,
                item_id=item.id,
                op="rewrite",
                ok=False,
                detail=f"没有改成功（{out.dropped_reason[:80] or '没有通过核验'}），已保持原样",
            )
        ops: list[Op] = [
            ReplaceField(item_id=item.id, field="kind", value=new.kind.value),
            ReplaceField(item_id=item.id, field="stem", value=new.stem),
            ReplaceField(item_id=item.id, field="options", value=new.options),
            ReplaceField(item_id=item.id, field="answer", value=new.answer),
            ReplaceField(item_id=item.id, field="answer_value", value=new.answer_value),
            ReplaceField(item_id=item.id, field="solution", value=new.solution),
            ReplaceField(item_id=item.id, field="difficulty", value=new.difficulty),
            ReplaceField(item_id=item.id, field="figures", value=[]),
            ReplaceField(
                item_id=item.id, field="verification", value=new.verification.model_dump(mode="json")
            ),
        ]
        changed = "、".join(_fields_changed(item, new)) or "表述"
        return ops, EditItemResult(
            number=n,
            item_id=item.id,
            op="rewrite",
            ok=True,
            detail=f"已改写（{changed}）",
            status=new.verification.status,
        )

    # ---- 只改文字 ----
    async def _rewrite_text(
        self, ctx: RunContext, paper: Paper, item: Item, n: int, a: EditAction
    ) -> tuple[list[Op], EditItemResult]:
        grade = paper.meta.get("grade")
        built = get_prompt("edit.rewrite_text").render(
            dynamic={
                "grade_text": f"{_GRADE_CN.get(grade, '')}年级" if isinstance(grade, int) else "小学",
                "kind_cn": _KIND_CN[item.kind],
                "field_cn": "解析" if a.field == "solution" else "题干",
                "stem": item.stem,
                "options": item.options,
                "answer": item.answer,
                "solution": item.solution,
                "instruction": a.instruction or "表述更清楚",
            }
        )
        out, _ = await complete_json(
            ctx.llm,
            LLMRequest(
                role=Role.smart,
                messages=built.messages,
                purpose="edit.rewrite_text",
                prompt=built.ref,
                max_tokens=900,
            ),
            _TextOut,
        )
        text = out.text.strip()
        if not text or text == getattr(item, a.field):
            return [], EditItemResult(
                number=n, item_id=item.id, op="rewrite_text", ok=False, detail="没有需要改的地方，保持原样"
            )
        updated = item.model_copy(update={a.field: text})
        ver: Verification = await reverify(ctx, updated, paper.meta)
        cn = "解析" if a.field == "solution" else "题干"
        ops: list[Op] = [
            ReplaceField(item_id=item.id, field=a.field, value=text),
            ReplaceField(item_id=item.id, field="verification", value=ver.model_dump(mode="json")),
        ]
        return ops, EditItemResult(
            number=n, item_id=item.id, op="rewrite_text", ok=True, detail=f"已改写{cn}", status=ver.status
        )


def compose_edit_reply(out: EditOut, paper_after: Paper | None = None) -> str:
    """修改完成后的回复：做了什么、哪些没成功、核验情况、怎样撤销。"""
    if out.unsupported and not out.results:
        return out.unsupported
    lines: list[str] = []
    ok = [r for r in out.results if r.ok]
    bad = [r for r in out.results if not r.ok]
    if ok:
        lines.append("已按您的要求修改：")
        for r in ok:
            tag = {
                "verified": "（已核验）",
                "checked": "（已校对）",
                "needs_review": "（需复核，请看一下）",
            }.get(r.status.value if r.status else "", "")
            lines.append(f"- {'第 ' + str(r.number) + ' 题：' if r.number else ''}{r.detail}{tag}")
    for r in bad:
        lines.append(f"- {'第 ' + str(r.number) + ' 题：' if r.number else ''}{r.detail}")
    if out.unsupported:
        lines.append(out.unsupported)
    if out.rev is not None:
        lines.append("不满意可以随时撤销。")
    return "\n".join(lines) if lines else "没有改动试卷。"

"""回复文本：确定性回复（不调用模型）。理解阶段的复述，以及出题完成后的总结。"""

from __future__ import annotations

from ..domain.blueprint import Blueprint
from ..domain.paper import Item, VerifyStatus
from ..domain.understanding import Route, Understanding


def compose_reply(u: Understanding) -> str:
    if u.route == Route.offtopic:
        return "我是小学数学命题助手，可以帮您出题、组卷、修改和导出。这个请求不在我的能力范围内——您可以告诉我想给哪个年级、哪个知识点出题。"
    if u.route in (Route.edit, Route.ask, Route.export):
        what = {Route.edit: "修改题目", Route.ask: "解答追问", Route.export: "导出"}[u.route]
        return f"收到。{what}的功能还在开发中，暂时不能执行。"
    lines = ["好的，我是这样理解您的需求的："]
    lines += [f"- {c.label}：{c.value}" for c in u.chips]
    if u.brief and u.brief.assumptions:
        lines.append("")
        lines += [f"（{a}）" for a in u.brief.assumptions]
    for n in u.notes:
        lines.append(f"提示：{n}")
    lines += ["", "整卷出题（先出蓝图和样题，确认后再成卷）还在开发中，您可以先让我出几道题看看效果。"]
    return "\n".join(lines)


_STATUS_TEXT = {
    VerifyStatus.verified: "已核验（程序求解与独立解题一致）",
    VerifyStatus.checked: "已校对（单路核验通过）",
    VerifyStatus.needs_review: "需复核",
}


def compose_generate_reply(u: Understanding, bp: Blueprint, items: list[Item], dropped: list[str]) -> str:
    """出题完成后的总结：数量、核验情况、被丢弃的题、本次假设与提示。"""
    n, want = len(items), len(bp.items)
    names: list[str] = []
    for it in items:
        names += [k for k in it.kp_ids if k not in names]
    lines = [f"已为您出好 {n} 道题" + (f"（您要的是 {want} 道）" if n != want else "") + "。"]
    counts = {st: sum(1 for it in items if it.verification.status == st) for st in _STATUS_TEXT}
    lines += [f"- {c} 道{_STATUS_TEXT[st]}" for st, c in counts.items() if c]
    for it in items:
        if any(
            c.name == "boundary" and c.evidence.get("verdict") == "borderline" for c in it.verification.checks
        ):
            lines.append("- 个别题含本单元稍后才学的内容，已在题目上标注，建议您看一下")
            break
    if dropped:
        lines.append(f"- 另有 {len(dropped)} 道题没有通过核验，已经舍弃，没有展示给您")
    if u.brief and u.brief.assumptions:
        lines.append("")
        lines += [f"（{a}）" for a in u.brief.assumptions]
    for note in [*u.notes, *bp.notes]:
        lines.append(f"提示：{note}")
    return "\n".join(lines)

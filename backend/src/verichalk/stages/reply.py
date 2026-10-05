"""回复文本：确定性回复（不调用模型）。理解阶段的复述，以及出题完成后的总结。"""

from __future__ import annotations

from ..domain.blueprint import Blueprint
from ..domain.paper import Item, VerifyStatus
from ..domain.understanding import Route, Understanding


def compose_reply(u: Understanding) -> str:
    if u.route == Route.offtopic:
        return "我是小学数学命题助手，可以帮您出题、组卷、修改和导出。这个请求不在我的能力范围内——您可以告诉我想给哪个年级、哪个知识点出题。"
    if u.route == Route.edit:
        return "现在还没有试卷可以修改，先让我出几道题吧。"
    if u.route == Route.ask:
        return "现在还没有题目可以讲解，先让我出几道题吧；有了题目，您可以问我“第 2 题为什么选 B”这样的问题。"
    if u.route == Route.export:
        return "导出请点击页面上的“导出”按钮：可以选 PDF、Word、Markdown 或 LaTeX，教师版（含答案与解析）或学生版（空白卷）。现在还没有试卷，先让我出几道题吧。"
    lines = ["好的，我是这样理解您的需求的："]
    lines += [f"- {c.label}：{c.value}" for c in u.chips]
    if u.brief and u.brief.assumptions:
        lines.append("")
        lines += [f"（{a}）" for a in u.brief.assumptions]
    for n in u.notes:
        lines.append(f"提示：{n}")
    return "\n".join(lines)


_STATUS_TEXT = {
    VerifyStatus.verified: "已核验（程序求解与独立解题一致）",
    VerifyStatus.checked: "已校对（单路核验通过）",
    VerifyStatus.needs_review: "需复核",
}


def compose_generate_reply(
    u: Understanding, bp: Blueprint, items: list[Item], dropped: list[str], how: str = "new"
) -> str:
    """出题完成后的总结：数量、核验情况、被丢弃的题、本次假设与提示。"""
    n, want = len(items), len(bp.items)
    names: list[str] = []
    for it in items:
        names += [k for k in it.kp_ids if k not in names]
    lead = {"append": "已追加 {n} 道题到试卷末尾", "replace": "已换成新的 {n} 道题（可以随时撤销）"}.get(
        how, "已为您出好 {n} 道题"
    )
    lines = [lead.format(n=n) + (f"（您要的是 {want} 道）" if n != want else "") + "。"]
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

"""回复文本：M2 阶段的确定性回复（不调用模型）。M3 起由生成结果的总结取代。"""

from __future__ import annotations

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
    lines += ["", "目前的版本只完成了需求理解，出题功能还在开发中。"]
    return "\n".join(lines)

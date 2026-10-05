"""意图理解的评分器：把每一轮的期望（见 eval/annotation/understand/guideline.md）逐项判定。

输出的 `CheckOutcome` 名称约定：`route`（B3）、`clarify`（B2）、`field:<名>`（B1）、`origin:<名>`（B1-origin）、
`absent:<名>`（B1-幻觉率）。全部是对 `Understanding` 的确定性比较，不调用模型。
"""

from __future__ import annotations

from typing import Any

from ..domain.brief import Brief, Origin, Slot
from ..domain.understanding import Understanding
from .checks import CheckOutcome, RunResult

# 期望字段名 → Brief 里对应的槽位
_SLOTS = {
    "grade": lambda b: b.scope.grade,
    "semester": lambda b: b.scope.semester,
    "unit": lambda b: b.scope.units,
    "count": lambda b: b.count,
    "difficulty": lambda b: b.difficulty,
    "kinds": lambda b: b.kinds,
    "tier": lambda b: b.tier_mix,
    "source": lambda b: b.source,
    "scenes": lambda b: b.scenes,
    "constraints": lambda b: b.constraints,
    "action": lambda b: b.action,
}


def final_understanding(r: RunResult) -> tuple[Understanding | None, Understanding | None]:
    """返回 (首次理解, 最终理解)。澄清后重新理解的结果存在 `understand:clarified`。"""
    st = r.run.state
    first = st.get("understand")
    final = st.get("understand:clarified") or first
    parse = lambda d: Understanding.model_validate(d) if d else None  # noqa: E731
    return parse(first), parse(final)


def _slot(b: Brief, name: str) -> Slot | None:
    return _SLOTS[name](b)


def _asserted(b: Brief, name: str) -> bool:
    """该字段是否被以 user / inferred 来源断言了具体值（默认值不算断言）。"""
    if name == "topics":
        return b.scope.kp_ids is not None or b.scope.topics is not None
    s = _slot(b, name)
    return s is not None and s.origin != Origin.default


def _tier_dominant(b: Brief) -> str | None:
    s = b.tier_mix
    if s is None or s.origin == Origin.default:
        return None
    return max(s.value.items(), key=lambda kv: kv[1])[0].value


def _check_value(name: str, want: Any, b: Brief, aux: dict[str, Any]) -> tuple[bool, str]:
    if name == "grade":
        s = b.scope.grade
        return (s is not None and s.value == want), f"期望 {want}，实际 {s.value if s else None}"
    if name == "semester":
        s = b.scope.semester
        return (s is not None and s.value == want), f"期望 {want}，实际 {s.value if s else None}"
    if name == "count":
        s = b.count
        return (
            s is not None and s.value == want and s.origin != Origin.default
        ), f"期望 {want}，实际 {s.value if s else None}"
    if name == "unit":
        ords = [aux.get("unit_ordinal", {}).get(u) for u in (b.scope.units.value if b.scope.units else [])]
        return want in ords, f"期望第 {want} 单元，实际 {ords}"
    if name == "difficulty":
        s = b.difficulty
        if s is None or s.origin == Origin.default:
            return False, "未解析出难度"
        lo, hi = s.value
        ok = (want.get("min") is None or lo >= want["min"]) and (want.get("max") is None or hi <= want["max"])
        return ok, f"期望 {want}，实际 [{lo},{hi}]"
    if name == "kinds_include" or name == "kinds":
        got = {k.value for k in (b.kinds.value if b.kinds else [])}
        return set(want) <= got, f"期望包含 {want}，实际 {sorted(got)}"
    if name == "tier":
        got = _tier_dominant(b)
        return got == want, f"期望 {want}，实际 {got}"
    if name == "source":
        got = b.source.value.value if b.source else "auto"
        return got == want, f"期望 {want}，实际 {got}"
    if name == "scenes_any":
        scenes = " ".join(b.scenes.value) if b.scenes else ""
        return any(
            k in scenes or any(s in k for s in (b.scenes.value if b.scenes else [])) for k in want
        ), f"期望含 {want}，实际 {scenes!r}"
    if name == "constraints_any":
        cons = " ".join(b.constraints.value) if b.constraints else ""
        return any(k in cons for k in want), f"期望含 {want}，实际 {cons!r}"
    if name == "topics_any":
        ids = b.scope.kp_ids.value if b.scope.kp_ids else []
        texts = [aux.get("kp_text", {}).get(i, "") for i in ids]
        return any(
            k in t for k in want for t in texts
        ), f"期望知识点含 {want}，实际映射 {[t.split('|')[0] for t in texts]}"
    if name == "action":
        got = b.action.value.value if b.action else "generate"
        return got == want, f"期望 {want}，实际 {got}"
    if name == "paper":
        p = b.paper
        ok = p is not None and all(getattr(p, k) == v for k, v in want.items())
        return ok, f"期望 {want}，实际 {p.model_dump() if p else None}"
    return False, f"未知的期望字段：{name}"


def understand_checks(r: RunResult) -> list[CheckOutcome]:
    exp = r.turn_expect
    if not exp:
        return []
    first, final = final_understanding(r)
    rid = r.run.id
    out: list[CheckOutcome] = []
    if first is None:
        return [
            CheckOutcome(
                "route", False, "运行没有产生理解结果", rid, {"expected": exp.get("route"), "actual": None}
            )
        ]

    if "route" in exp:
        out.append(
            CheckOutcome(
                "route",
                first.route.value == exp["route"],
                f"期望 {exp['route']}，实际 {first.route.value}",
                rid,
                {"expected": exp["route"], "actual": first.route.value},
            )
        )
    if "clarify" in exp:
        asked = first.clarify is not None
        ok = asked == bool(exp["clarify"])
        detail = f"期望{'提问' if exp['clarify'] else '不提问'}，实际{'提问' if asked else '未提问'}"
        if asked and first.clarify:
            n = len(first.clarify.options)
            if not 2 <= n <= 6:
                ok, detail = False, f"选项数 {n} 不在 2～6"
        out.append(
            CheckOutcome("clarify", ok, detail, rid, {"expected": bool(exp["clarify"]), "actual": asked})
        )

    brief = final.brief if final else None
    for name, want in (exp.get("brief") or {}).items():
        if brief is None:
            out.append(CheckOutcome(f"field:{name}", False, "没有 Brief", rid))
            continue
        ok, detail = _check_value(name, want, brief, r.aux)
        out.append(CheckOutcome(f"field:{name}", ok, detail, rid))
    for name in exp.get("user_fields", []):
        s = _slot(brief, name) if brief else None
        ok = s is not None and s.origin == Origin.user
        out.append(
            CheckOutcome(f"origin:{name}", ok, f"来源应为 user，实际 {s.origin.value if s else '无'}", rid)
        )
    for name in exp.get("absent", []):
        asserted = _asserted(brief, name) if brief else False
        out.append(
            CheckOutcome(f"absent:{name}", not asserted, "用户没说，却断言了具体值" if asserted else "", rid)
        )
    return out

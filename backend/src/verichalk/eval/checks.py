"""确定性检查：对"用例的期望 × 运行结果"逐项判定。新增检查在 `CHECKS` 里注册（architecture §10）。

检查只读事件与运行记录，不调用模型；需要判官的语义检查另放 `judges`（M3 起）。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..domain.events import Event, MessageDone
from ..domain.run import Run
from ..metrics import RunMetrics


@dataclass
class RunResult:
    """一个用例中某一轮的运行结果。"""

    run: Run
    events: list[Event]
    metrics: RunMetrics
    reply: str = ""
    turn_expect: dict[str, Any] = field(default_factory=dict)
    aux: dict[str, Any] = field(default_factory=dict)  # 评分用的辅助数据（知识点文本、单元序号）


@dataclass
class CheckOutcome:
    name: str
    passed: bool
    detail: str = ""
    run_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)  # 期望 / 实际，供混淆矩阵等聚合使用


@dataclass
class CaseResult:
    case_id: str
    tags: list[str]
    results: list[RunResult] = field(default_factory=list)
    outcomes: list[CheckOutcome] = field(default_factory=list)
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.error is None and all(o.passed for o in self.outcomes)


Check = Callable[[Any, list[RunResult]], list[CheckOutcome]]


def _last(rs: list[RunResult]) -> RunResult:
    return rs[-1]


def check_status(expected: Any, rs: list[RunResult]) -> list[CheckOutcome]:
    r = _last(rs)
    ok = r.run.status.value == expected
    return [
        CheckOutcome(
            "status",
            ok,
            f"期望 {expected}，实际 {r.run.status.value}"
            + (f"（{r.run.error.code}）" if r.run.error else ""),
            r.run.id,
        )
    ]


def check_trace(expected: Any, rs: list[RunResult]) -> list[CheckOutcome]:
    return [
        CheckOutcome(
            "trace_complete",
            r.metrics.trace_ok == bool(expected),
            "; ".join(r.metrics.trace_problems)[:200],
            r.run.id,
        )
        for r in rs
    ]


def check_max_e2e_ms(expected: Any, rs: list[RunResult]) -> list[CheckOutcome]:
    r = _last(rs)
    v = r.metrics.e2e_ms
    return [
        CheckOutcome(
            "max_e2e_ms",
            v is not None and v <= float(expected),
            f"{v:.0f}ms 上限 {expected}" if v is not None else "无耗时",
            r.run.id,
        )
    ]


def check_reply_contains(expected: Any, rs: list[RunResult]) -> list[CheckOutcome]:
    r = _last(rs)
    missing = [s for s in expected if s not in r.reply]
    return [CheckOutcome("reply_contains", not missing, f"缺少：{missing}" if missing else "", r.run.id)]


def check_reply_not_contains(expected: Any, rs: list[RunResult]) -> list[CheckOutcome]:
    r = _last(rs)
    hit = [s for s in expected if s in r.reply]
    return [CheckOutcome("reply_not_contains", not hit, f"不应出现：{hit}" if hit else "", r.run.id)]


_SECRET = re.compile(r"sk-[A-Za-z0-9_\-]{12,}|aliyuncs\.com|Bearer\s+\S{8,}")


def check_no_secrets(expected: Any, rs: list[RunResult]) -> list[CheckOutcome]:
    out = []
    for r in rs:
        blob = "".join(e.model_dump_json() for e in r.events)
        out.append(
            CheckOutcome(
                "no_secrets",
                not _SECRET.search(blob),
                "事件中出现疑似密钥 / 端点" if _SECRET.search(blob) else "",
                r.run.id,
            )
        )
    return out


def check_min_events(expected: Any, rs: list[RunResult]) -> list[CheckOutcome]:
    r = _last(rs)
    types = {e.type for e in r.events}
    missing = [t for t in expected if t not in types]
    return [CheckOutcome("has_events", not missing, f"缺少事件：{missing}" if missing else "", r.run.id)]


CHECKS: dict[str, Check] = {
    "status": check_status,
    "trace_complete": check_trace,
    "max_e2e_ms": check_max_e2e_ms,
    "reply_contains": check_reply_contains,
    "reply_not_contains": check_reply_not_contains,
    "has_events": check_min_events,
}

# 对所有用例默认执行（不需要在 expect 里写）
DEFAULT_CHECKS: dict[str, Any] = {"trace_complete": True}
ALWAYS = {"no_secrets": check_no_secrets}


def reply_of(events: list[Event]) -> str:
    done = [e for e in events if isinstance(e, MessageDone)]
    return done[-1].text if done else ""


def evaluate(expect: dict[str, Any], rs: list[RunResult]) -> list[CheckOutcome]:
    outcomes: list[CheckOutcome] = []
    merged = {**DEFAULT_CHECKS, **expect}
    for name, arg in merged.items():
        fn = CHECKS.get(name)
        if fn is None:
            outcomes.append(CheckOutcome(name, False, f"未知检查项：{name}"))
            continue
        outcomes += fn(arg, rs)
    for fn in ALWAYS.values():
        outcomes += fn(None, rs)
    from .understand_checks import understand_checks  # 延迟导入：避免与本模块循环依赖

    for r in rs:
        outcomes += understand_checks(r)
    return outcomes

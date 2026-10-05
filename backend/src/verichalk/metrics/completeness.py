"""F6：trace 完整度检查——span 树必须闭合、父节点存在、seq 连续。"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.events import Event, SpanFinished, SpanStarted


@dataclass
class CompletenessReport:
    n_events: int = 0
    n_spans: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def check_trace_completeness(events: list[Event]) -> CompletenessReport:
    rep = CompletenessReport(n_events=len(events))
    seqs = [e.seq for e in events]
    if seqs != list(range(1, len(events) + 1)):
        rep.problems.append("seq 不连续或不从 1 开始")

    started: dict[str, SpanStarted] = {}
    finished: dict[str, SpanFinished] = {}
    for e in events:
        if isinstance(e, SpanStarted):
            if e.span_id in started:
                rep.problems.append(f"span 重复开始：{e.span_id}")
            started[e.span_id or ""] = e
        elif isinstance(e, SpanFinished):
            if e.span_id not in started:
                rep.problems.append(f"span 未开始就结束：{e.span_id}")
            if e.span_id in finished:
                rep.problems.append(f"span 重复结束：{e.span_id}")
            finished[e.span_id or ""] = e
    rep.n_spans = len(started)
    for sid, s in started.items():
        if sid not in finished:
            rep.problems.append(f"span 未闭合：{s.name}")
        if s.parent_id is not None and s.parent_id not in started:
            rep.problems.append(f"孤儿 span（父不存在）：{s.name}")
    return rep

"""运行摘要：把一次运行的事件日志与阶段快照整理成"分析助手"能读懂的上下文（调试台的 AI 分析用）。

设计思路——上下文分两层：
1. **摘要**（常驻在提示里，约 2～3 万字符以内）：运行概况、用户输入与最终回复、需求理解、阶段与耗时、逐题生成过程（尝试次数 / 每次被哪项检查拦下 / 为什么丢弃）、
   模型调用清单（一行一次调用，含 token / 延迟 / 成本 / 错误）、知识检索、题目与核验证据、错误、指标。每一行都带着 id，模型可以拿 id 去查细节；
2. **按需查询**（工具）：`get_llm_call`（完整提示与回复）、`get_span`、`get_item`（完整核验证据）、`get_stage_output`（阶段快照）、`find_events`。
   摘要里放不下的长文本（提示词全文、求解程序、盲解过程）都放在这一层，避免每次都把几万字塞给模型。

摘要是**纯函数**（输入事件与运行记录，输出文本），可以单元测试；长度由 `budget` 控制，超出时从调用清单与题目细节里裁。
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel

from ..domain.events import (
    Event,
    LLMCall,
    MessageDone,
    PerceptionReady,
    RetrievalResult,
    RunFinished,
    SpanFinished,
    SpanStarted,
    UnderstandingReady,
)
from ..domain.llm import LLMCallRecord
from ..domain.paper import Item
from ..domain.run import Run
from ..metrics import RunMetrics, compute_run_metrics

DEFAULT_BUDGET = 30_000  # 摘要的字符预算
_CLIP = 160


def clip(text: object, n: int = _CLIP) -> str:
    s = " ".join(str(text).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _plain(v: Any) -> Any:
    """枚举 / 模型 / 字典键里的枚举 → 可读的普通值（摘要里不出现 `<Tier.x: 'x'>` 这类对象表示）。"""
    if isinstance(v, Enum):
        return v.value
    if isinstance(v, BaseModel):
        return v.model_dump(mode="json")
    if isinstance(v, dict):
        return {str(_plain(k)): _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [_plain(x) for x in v]
    return v


def _cost(v: float | None, none: str = "—") -> str:
    return none if v is None else f"¥{v:.4f}"


def _ms(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v / 1000:.1f}s" if v >= 1000 else f"{v:.0f}ms"


@dataclass
class SpanInfo:
    id: str
    parent: str | None
    kind: str
    name: str
    start_seq: int
    attrs: dict[str, Any] = field(default_factory=dict)
    status: str = "?"
    ms: float | None = None
    error: dict[str, Any] | None = None


@dataclass
class RunIndex:
    """一次运行的可查询索引：摘要与工具都读它。"""

    run: Run
    input_text: str
    events: list[Event]
    metrics: RunMetrics
    spans: dict[str, SpanInfo] = field(default_factory=dict)
    children: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    calls: list[LLMCallRecord] = field(default_factory=list)
    call_span: dict[str, str | None] = field(default_factory=dict)  # 调用 id → 它所在的 span
    items: dict[str, Item] = field(default_factory=dict)
    item_order: list[str] = field(default_factory=list)
    reply: str = ""
    understanding: Any = None
    perception: Any = None

    def stage_of(self, span_id: str | None) -> str:
        """某个 span 所属的阶段名（沿父链找到第一个 stage span）。"""
        seen = 0
        while span_id and seen < 50:
            s = self.spans.get(span_id)
            if s is None:
                return ""
            if s.kind == "stage":
                return s.name + (f"[{s.attrs['key']}]" if s.attrs.get("key") else "")
            span_id, seen = s.parent, seen + 1
        return ""


def build_index(run: Run, events: list[Event], input_text: str = "") -> RunIndex:
    idx = RunIndex(run=run, input_text=input_text, events=events, metrics=compute_run_metrics(events))
    for e in events:
        if isinstance(e, SpanStarted):
            idx.spans[e.span_id or ""] = SpanInfo(
                e.span_id or "", e.parent_id, e.kind.value, e.name, e.seq, dict(e.attrs)
            )
            if e.parent_id:
                idx.children[e.parent_id].append(e.span_id or "")
        elif isinstance(e, SpanFinished):
            s = idx.spans.get(e.span_id or "")
            if s:
                s.status, s.ms = e.status.value, e.duration_ms
                s.attrs.update(e.attrs)
                s.error = e.error.model_dump(mode="json") if e.error else None
        elif isinstance(e, LLMCall):
            idx.calls.append(e.record)
            idx.call_span[e.record.id] = e.span_id
        elif isinstance(e, MessageDone):
            idx.reply = e.text
        elif isinstance(e, UnderstandingReady):
            idx.understanding = e.understanding
        elif isinstance(e, PerceptionReady):
            idx.perception = e.references
        elif e.type == "item.delivered":
            item = e.item  # type: ignore[attr-defined]
            if item.id not in idx.items:
                idx.item_order.append(item.id)
            idx.items[item.id] = item
        elif e.type == "item.status":
            it = idx.items.get(e.item_id)  # type: ignore[attr-defined]
            if it is not None:
                it.verification.status = e.status  # type: ignore[attr-defined]
                it.verification.checks = e.checks  # type: ignore[attr-defined]
    return idx


# ---------------------------------------------------------------- 摘要的各节
def _overview(idx: RunIndex) -> str:
    r, m = idx.run, idx.metrics
    lines = [
        "# 运行概况",
        f"- 运行 id：{r.id}　管线：{r.pipeline}　状态：{r.status.value}",
        f"- 总耗时 {_ms(m.e2e_ms)}；首反馈 {_ms(m.first_feedback_ms)}；事件 {m.n_events} 条、span {m.n_spans} 个；trace 完整度：{'完整' if m.trace_ok else '有缺失：' + '；'.join(m.trace_problems[:3])}",
        f"- 模型调用 {len(idx.calls)} 次；token：输入 {m.usage.prompt_tokens}（缓存命中 {m.usage.cached_tokens}）、输出 {m.usage.completion_tokens}；成本 {_cost(m.cost, '未知')}",
    ]
    if any(c.from_cassette for c in idx.calls):
        lines.append(
            "- **注意：这次运行的模型调用来自录制回放**——调用清单里的耗时与 token 是录制时的真实值，"
            "而阶段 / 运行耗时是回放时的实际耗时（几乎为零），二者不能直接比较；分析延迟请以调用清单为准。"
        )
    if r.error:
        lines.append(
            f"- **失败**：{r.error.code}｜{clip(r.error.message, 300)}（给用户的话：{clip(r.error.user_message)}）"
        )
    return "\n".join(lines)


def _io(idx: RunIndex) -> str:
    out = ["# 用户输入与最终回复", f"- 用户输入：{idx.input_text or '（无文字输入）'}"]
    out.append(f"- 助手最终回复：{clip(idx.reply, 700) or '（没有回复）'}")
    return "\n".join(out)


def _understanding(idx: RunIndex) -> str:
    u = idx.understanding
    if u is None:
        return ""
    lines = [
        "# 需求理解（understanding.ready）",
        f"- 路由：{u.route.value}；解析方式：{u.method}；理由：{clip(u.reason)}",
    ]
    if u.clarify:
        lines.append(f"- 澄清：{clip(u.clarify.prompt)}")
    b = u.brief
    if b is not None:
        s = b.scope

        def slot(x: Any) -> str:
            if x is None:
                return "未指定"
            return f"{json.dumps(_plain(x.value), ensure_ascii=False)}（来源 {x.origin.value}）"

        lines += [
            f"- 范围：年级 {slot(s.grade)}；学期 {slot(s.semester)}；知识点 {slot(s.kp_ids)}；单元 {slot(s.units)}；自由主题 {slot(s.topics)}",
            f"- 题量 {slot(b.count)}；难度 {slot(b.difficulty)}；题型 {slot(b.kinds)}；档位 {slot(b.tier_mix)}；来源模式 {slot(b.source)}",
            f"- 情境 {slot(b.scenes)}；限制 {slot(b.constraints)}；目标课时 {slot(b.target_lesson)}",
        ]
        if b.assumptions:
            lines.append("- 本次假设：" + "；".join(b.assumptions))
        if b.references:
            lines.append(f"- 照片里的参考题：{len(b.references)} 道（防雷同集合）")
    if u.notes:
        lines.append("- 提示：" + "；".join(u.notes))
    p = idx.perception
    if p is not None:
        bad = [pg for pg in p.pages if pg.verdict.value != "worksheet"]
        lines.append(
            f"- 照片识别：{len(p.pages)} 张，可用 {len(p.pages) - len(bad)} 张，识别出 {len(p.items)} 题；需核对：{p.needs_confirm}；"
            + (f"不可用：{[(pg.verdict.value, clip(pg.reason, 60)) for pg in bad]}" if bad else "")
        )
    return "\n".join(lines)


def _stages(idx: RunIndex) -> str:
    """阶段与耗时：顶层 stage span 列表；并行的逐题子流程（带 key）合并成一张表，避免刷屏。"""
    top = [s for s in idx.spans.values() if s.kind == "stage"]
    plain = [s for s in top if not s.attrs.get("key")]
    keyed: dict[str, list[SpanInfo]] = defaultdict(list)
    for s in top:
        if s.attrs.get("key"):
            keyed[s.name].append(s)
    lines = ["# 阶段与耗时（stage span）"]
    for s in sorted(plain, key=lambda x: x.start_seq):
        err = f"｜错误 {s.error['code']}" if s.error else ""
        reused = "｜复用快照" if s.attrs.get("reused") else ""
        lines.append(f"- [{s.id}] {s.name}：{_ms(s.ms)}，{s.status}{reused}{err}")
    for name, spans in keyed.items():
        ms = [s.ms or 0 for s in spans]
        bad = [s for s in spans if s.status != "ok"]
        lines.append(
            f"- {name} × {len(spans)}（逐题并行）：单个耗时 最短 {_ms(min(ms))} / 中位 {_ms(sorted(ms)[len(ms) // 2])} / 最长 {_ms(max(ms))}；失败 {len(bad)} 个"
        )
    by_stage: dict[str, list[LLMCallRecord]] = defaultdict(list)
    for c in idx.calls:
        by_stage[idx.stage_of(idx.call_span.get(c.id)).split("[")[0] or "（阶段外）"].append(c)
    if by_stage:
        lines.append("- 各阶段的模型调用（次数｜调用耗时合计｜token 输入/输出｜成本）：")
        for st, cs in by_stage.items():
            costs = [c.cost for c in cs if c.cost is not None]
            lines.append(
                f"  - {st}：{len(cs)} 次｜{_ms(sum(c.total_ms for c in cs))}｜"
                f"{sum(c.usage.prompt_tokens for c in cs)}/{sum(c.usage.completion_tokens for c in cs)}｜"
                f"{_cost(sum(costs) if costs else None)}"
            )
    return "\n".join(lines)


def _produce(idx: RunIndex) -> str:
    """逐题生成过程：来自运行的阶段快照（尝试次数、每次被哪项检查拦下、丢弃原因）。"""
    st = idx.run.state or {}
    keys = [k for k in st if k.startswith("produce:")]
    if not keys:
        return ""
    lines = ["# 逐题生成过程（produce 阶段快照：每题尝试几次、被哪些检查拦下）"]
    delivered = dropped = 0
    for k in keys:
        out = st[k] or {}
        item = out.get("item")
        attempts = out.get("attempts")
        failed = out.get("failed_checks") or []
        tag = "交付" if item else "丢弃"
        delivered += bool(item)
        dropped += not item
        stem = clip(item.get("stem", ""), 50) if item else ""
        why = f"｜丢弃原因：{clip(out.get('dropped_reason', ''), 120)}" if not item else ""
        blocked = (
            f"｜被拦：{'；'.join('/'.join(x)[:90] if isinstance(x, list) else str(x)[:90] for x in failed[:3])}"
            if failed
            else ""
        )
        lines.append(f"- {k}｜{tag}｜尝试 {attempts} 次{blocked}{why}{('｜' + stem) if stem else ''}")
    lines.insert(1, f"（共 {len(keys)} 个题位：交付 {delivered}，丢弃 {dropped}）")
    return "\n".join(lines)


def _calls(idx: RunIndex, budget: int) -> str:
    lines = [
        "# 模型调用清单（一行一次；用 get_llm_call 查完整提示与回复）",
        "格式：序号｜call_id｜所属阶段｜purpose｜角色/模型｜输入(缓存)/输出 token｜首 token / 总耗时｜成本｜重试｜结束原因｜备注",
    ]
    used = 0
    for i, c in enumerate(idx.calls, 1):
        u = c.usage
        flag = []
        if c.error:
            flag.append(f"错误 {c.error.code}")
        if c.from_cassette:
            flag.append("回放")
        row = (
            f"{i}｜{c.id}｜{idx.stage_of(idx.call_span.get(c.id))}｜{c.purpose}｜{c.role}/{c.model}｜"
            f"{u.prompt_tokens}({u.cached_tokens})/{u.completion_tokens}｜{_ms(c.ttft_ms)} / {_ms(c.total_ms)}｜"
            f"{_cost(c.cost)}｜{c.retries}｜{c.finish_reason or '—'}｜{'、'.join(flag)}"
        )
        used += len(row)
        if used > budget:
            lines.append(
                f"……（预算已满，其余 {len(idx.calls) - i + 1} 次调用请用 get_llm_call 或 find_events 查询）"
            )
            break
        lines.append(row)
    return "\n".join(lines)


def _retrieval(idx: RunIndex) -> str:
    rs = [e for e in idx.events if isinstance(e, RetrievalResult)]
    if not rs:
        return ""
    lines = ["# 知识检索与规划（retrieval.result）"]
    for e in rs[:14]:
        p = e.payload
        names = "、".join(f"{n.name}({n.role})" for n in p.nodes[:6])
        combos = f"；组合 {len(p.combos)} 个" if p.combos else ""
        lines.append(
            f"- {p.step}｜查询「{clip(p.query, 40)}」｜{names}{combos}{('；' + clip('；'.join(p.notes), 100)) if p.notes else ''}"
        )
    if len(rs) > 14:
        lines.append(f"……另有 {len(rs) - 14} 条")
    return "\n".join(lines)


def _items(idx: RunIndex, budget: int) -> str:
    if not idx.items:
        return ""
    lines = ["# 交付的题目与核验（用 get_item 查完整证据）"]
    used = 0
    for n, iid in enumerate(idx.item_order, 1):
        it = idx.items[iid]
        checks = "、".join(f"{c.name}:{c.status.value}" for c in it.verification.checks)
        bad = [
            f"{c.name}（{clip(c.detail, 80)}）"
            for c in it.verification.checks
            if c.status.value in ("fail", "warn")
        ]
        row = (
            f"- 第{n}题 [{it.id}]｜{it.kind.value}｜难度 {it.difficulty}｜档位 {it.tier.value}｜状态 {it.verification.status.value}｜"
            f"题干：{clip(it.stem, 110)}｜答案：{clip(it.answer, 40)}｜检查：{checks}"
            + (f"｜问题：{'；'.join(bad)}" if bad else "")
        )
        used += len(row)
        if used > budget:
            lines.append(f"……（预算已满，其余 {len(idx.item_order) - n + 1} 道题请用 get_item 查询）")
            break
        lines.append(row)
    return "\n".join(lines)


def _errors(idx: RunIndex) -> str:
    bad = [s for s in idx.spans.values() if s.status not in ("ok", "?") or s.error]
    fin = next((e for e in idx.events if isinstance(e, RunFinished)), None)
    if not bad and not (fin and fin.error):
        return ""
    lines = ["# 异常（出错或取消的 span）"]
    for s in sorted(bad, key=lambda x: x.start_seq)[:20]:
        e = s.error or {}
        lines.append(
            f"- [{s.id}] {s.kind}:{s.name}｜{s.status}｜{e.get('code', '')}｜{clip(e.get('message', ''), 160)}"
        )
    return "\n".join(lines)


def _models(idx: RunIndex) -> str:
    if not idx.metrics.models:
        return ""
    lines = ["# 按模型汇总"]
    for m in idx.metrics.models:
        lines.append(
            f"- {m.role}/{m.model}：{m.calls} 次，输入 {m.prompt_tokens}（缓存 {m.cached_tokens}）输出 {m.completion_tokens}，"
            f"首 token p50 {_ms(m.ttft_p50_ms)}，总耗时 p50 {_ms(m.total_p50_ms)}，成本 {_cost(m.cost)}，重试 {m.retries}，错误 {m.errors}"
        )
    return "\n".join(lines)


def render_digest(idx: RunIndex, budget: int = DEFAULT_BUDGET) -> str:
    """生成摘要。`budget` 约束总字符数：调用清单与题目细节各占一部分，超出的由工具查询。"""
    fixed = [
        _overview(idx),
        _io(idx),
        _understanding(idx),
        _stages(idx),
        _produce(idx),
        _models(idx),
        _retrieval(idx),
        _errors(idx),
    ]
    head = "\n\n".join(x for x in fixed if x)
    room = max(4000, budget - len(head))
    parts = [head, _calls(idx, int(room * 0.6)), _items(idx, int(room * 0.4))]
    return "\n\n".join(x for x in parts if x)


# ---------------------------------------------------------------- 工具的数据来源
def _text_of(content: Any, limit: int) -> str:
    if isinstance(content, list):
        content = " ".join(str(p.get("text", p.get("image_url", ""))) for p in content if isinstance(p, dict))
    s = str(content or "")
    return s if len(s) <= limit else s[:limit] + f"…[已截断，共 {len(s)} 字符]"


def call_detail(idx: RunIndex, call_id: str, limit: int = 6000) -> dict[str, Any]:
    if call_id.strip().isdigit():  # 摘要清单里的序号（从 1 起）
        n = int(call_id) - 1
        c = idx.calls[n] if 0 <= n < len(idx.calls) else None
    else:
        c = next((x for x in idx.calls if x.id == call_id.strip()), None)
    if c is None:
        return {"error": f"没有这次调用：{call_id}（用清单里的 call_id 或序号）"}
    return {
        "id": c.id,
        "stage": idx.stage_of(idx.call_span.get(c.id)),
        "purpose": c.purpose,
        "role": c.role,
        "model": c.model,
        "params": c.params,
        "prompt": c.prompt.model_dump(mode="json") if c.prompt else None,
        "usage": c.usage.model_dump(),
        "ttft_ms": c.ttft_ms,
        "total_ms": c.total_ms,
        "retries": c.retries,
        "finish_reason": c.finish_reason,
        "error": c.error.model_dump(mode="json") if c.error else None,
        "messages": [
            {"role": m.get("role"), "content": _text_of(m.get("content"), limit)} for m in c.messages
        ],
        "response": _text_of(c.response_text, limit),
        "reasoning": _text_of(c.reasoning_text, 2500),
        "tool_calls": [t.model_dump() for t in c.tool_calls],
    }


def span_detail(idx: RunIndex, span_id: str) -> dict[str, Any]:
    s = idx.spans.get(span_id)
    if s is None:
        return {"error": f"没有这个 span：{span_id}"}
    kids = [idx.spans[k] for k in idx.children.get(span_id, []) if k in idx.spans]
    return {
        "id": s.id,
        "kind": s.kind,
        "name": s.name,
        "parent": s.parent,
        "status": s.status,
        "ms": s.ms,
        "attrs": json.loads(json.dumps(s.attrs, ensure_ascii=False, default=str)),
        "error": s.error,
        "children": [
            {"id": k.id, "kind": k.kind, "name": k.name, "ms": k.ms, "status": k.status} for k in kids[:40]
        ],
        "llm_calls_directly_under": [c.id for c in idx.calls if idx.call_span.get(c.id) == span_id],
    }


def item_detail(idx: RunIndex, item_id: str) -> dict[str, Any]:
    it = idx.items.get(item_id)
    if it is None:
        return {"error": f"没有这道题：{item_id}（交付的题 id 见摘要；被丢弃的题看 get_stage_output）"}
    d = it.model_dump(mode="json")
    for c in d["verification"]["checks"]:
        ev = json.dumps(c.get("evidence", {}), ensure_ascii=False, default=str)
        c["evidence"] = ev if len(ev) <= 3000 else ev[:3000] + "…[已截断]"
    return d


def stage_output(idx: RunIndex, key: str | None) -> dict[str, Any]:
    st = idx.run.state or {}
    if not key:
        return {"keys": sorted(st)}
    if key not in st:
        return {"error": f"没有这个阶段快照：{key}", "keys": sorted(st)}
    text = json.dumps(st[key], ensure_ascii=False, default=str)
    return {
        "key": key,
        "output": text if len(text) <= 9000 else text[:9000] + f"…[已截断，共 {len(text)} 字符]",
    }


def find_events(
    idx: RunIndex, type_: str | None = None, text: str | None = None, limit: int = 25
) -> dict[str, Any]:
    out = []
    for e in idx.events:
        if type_ and e.type != type_:
            continue
        raw = e.model_dump_json()
        if text and text not in raw:
            continue
        out.append({"seq": e.seq, "type": e.type, "span_id": e.span_id, "json": clip(raw, 400)})
        if len(out) >= limit:
            break
    return {"matches": out, "truncated": len(out) >= limit}

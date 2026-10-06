import { useMemo, useState } from "react";
import type { RunMetrics } from "@/shared/api/types";
import { Icon } from "@/shared/ui/Icon";
import { fmtClock, fmtMs, pretty, truncate } from "./format";
import { defaultCollapsed, spanDurationMs, visibleRows } from "./model";
import type { RunModel, SpanNode } from "./model";

const KIND_LABEL: Record<string, string> = {
  run: "运行",
  stage: "阶段",
  llm: "模型",
  tool: "工具",
  check: "检查",
  other: "其他",
};

/** 流程与时间线：左边是 span 树（阶段 → 工具 / 模型 / 检查），右边是同一行的瀑布条；并行的逐题子流程在时间轴上并排可见。 */
export function Timeline({ model, metrics }: { model: RunModel; metrics: RunMetrics | null }) {
  const initial = useMemo(() => defaultCollapsed(model.roots), [model.roots]);
  const [collapsed, setCollapsed] = useState<Set<string> | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [kinds, setKinds] = useState<Set<string>>(
    new Set(["run", "stage", "llm", "tool", "check", "other"]),
  );
  const col = collapsed ?? initial;

  const rows = useMemo(
    () => visibleRows(model.roots, col).filter((s) => kinds.has(s.kind)),
    [model.roots, col, kinds],
  );
  const total = Math.max(0.001, model.t1 - model.t0);
  const sel = selected ? model.spans.get(selected) : undefined;

  const toggle = (id: string) => {
    const next = new Set(col);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setCollapsed(next);
  };

  if (model.spans.size === 0) return <p className="muted">这次运行没有 span（可能还没开始）。</p>;

  return (
    <div className="tl">
      {metrics && metrics.stages.length > 0 && (
        <div className="tl-stages" aria-label="阶段耗时">
          {metrics.stages.map((s) => (
            <div key={s.name} className="tl-stage">
              <span className="tl-stage__name">{s.name}</span>
              <span className="mono">{fmtMs(s.total_ms)}</span>
              <span className="muted small">
                ×{s.count} · 最长 {fmtMs(s.max_ms)}
              </span>
            </div>
          ))}
        </div>
      )}
      <div className="tl-filter" role="group" aria-label="显示的类型">
        {Object.entries(KIND_LABEL).map(([k, l]) => (
          <label key={k} className="check small">
            <input
              type="checkbox"
              checked={kinds.has(k)}
              onChange={(e) => {
                const n = new Set(kinds);
                if (e.target.checked) n.add(k);
                else n.delete(k);
                setKinds(n);
              }}
            />
            <i className={`kdot kdot--${k}`} /> {l}
          </label>
        ))}
        <span className="dbg-spacer" />
        <button type="button" className="btn btn--sm" onClick={() => setCollapsed(new Set())}>
          全部展开
        </button>
        <button type="button" className="btn btn--sm" onClick={() => setCollapsed(initial)}>
          恢复折叠
        </button>
      </div>

      <div className="tl-body">
        <div className="tl-rows" role="tree" aria-label="span 树" data-testid="span-tree">
          {rows.map((s) => (
            <Row
              key={s.id}
              s={s}
              model={model}
              total={total}
              collapsed={col.has(s.id)}
              selected={selected === s.id}
              onToggle={() => toggle(s.id)}
              onSelect={() => setSelected(s.id)}
            />
          ))}
        </div>
        <aside className="tl-inspector" aria-label="span 详情">
          {sel ? (
            <Inspector s={sel} model={model} />
          ) : (
            <p className="muted">点击左侧任一行，查看输入 / 输出摘要、耗时与错误。</p>
          )}
        </aside>
      </div>
    </div>
  );
}

function Row(p: {
  s: SpanNode;
  model: RunModel;
  total: number;
  collapsed: boolean;
  selected: boolean;
  onToggle: () => void;
  onSelect: () => void;
}) {
  const { s, model, total } = p;
  const dur = spanDurationMs(s, model.t1);
  const left = ((s.start - model.t0) / total) * 100;
  const width = Math.max(0.3, (dur / 1000 / total) * 100);
  const hasKids = s.children.length > 0;
  return (
    <div
      className={`tl-row ${p.selected ? "tl-row--on" : ""} tl-row--${s.state}`}
      role="treeitem"
      aria-expanded={hasKids ? !p.collapsed : undefined}
      aria-selected={p.selected}
    >
      <button
        type="button"
        className="tl-row__label"
        style={{ paddingLeft: 6 + s.depth * 16 }}
        onClick={p.onSelect}
      >
        {hasKids ? (
          <span
            className="tl-caret"
            role="button"
            aria-label={p.collapsed ? "展开" : "收起"}
            onClick={(e) => {
              e.stopPropagation();
              p.onToggle();
            }}
          >
            <Icon name={p.collapsed ? "right" : "down"} size={13} />
          </span>
        ) : (
          <span className="tl-caret" />
        )}
        <i className={`kdot kdot--${s.kind}`} title={KIND_LABEL[s.kind]} />
        <span className="tl-name" title={s.name}>
          {s.name}
        </span>
        {s.state === "error" && <span className="pill pill--bad">失败</span>}
        {s.state === "running" && <span className="spinner" aria-label="进行中" />}
        {p.collapsed && hasKids && <span className="muted small">+{countDesc(s)}</span>}
      </button>
      <span className="tl-dur mono">{fmtMs(dur)}</span>
      <div className="tl-bar" aria-hidden>
        <i
          className={`tl-bar__fill kdot--${s.kind}`}
          style={{ left: `${left}%`, width: `${Math.min(width, 100 - left)}%` }}
        />
      </div>
    </div>
  );
}

function countDesc(s: SpanNode): number {
  return s.children.reduce((n, c) => n + 1 + countDesc(c), 0);
}

function Inspector({ s, model }: { s: SpanNode; model: RunModel }) {
  const attrs = Object.entries(s.attrs).filter(
    ([, v]) => v !== null && v !== undefined && v !== "",
  );
  return (
    <div className="insp" data-testid="span-inspector">
      <h3>
        <i className={`kdot kdot--${s.kind}`} /> {s.name}
      </h3>
      <dl className="kv">
        <dt>类型</dt>
        <dd>{KIND_LABEL[s.kind]}</dd>
        <dt>状态</dt>
        <dd>
          {s.state === "ok"
            ? "成功"
            : s.state === "error"
              ? "失败"
              : s.state === "cancelled"
                ? "已取消"
                : "进行中"}
        </dd>
        <dt>耗时</dt>
        <dd className="mono">{fmtMs(spanDurationMs(s, model.t1))}</dd>
        <dt>开始</dt>
        <dd className="mono">
          {fmtClock(s.start)}（运行开始后 {fmtMs((s.start - model.t0) * 1000)}）
        </dd>
        <dt>子节点</dt>
        <dd>{s.children.length}</dd>
      </dl>
      {s.error && (
        <div className="notice notice--bad">
          <div>
            <b>{s.error.code}</b>：{s.error.message}
            {s.error.user_message && (
              <div className="muted">用户看到的：{s.error.user_message}</div>
            )}
          </div>
        </div>
      )}
      {attrs.length > 0 && (
        <>
          <h4>属性</h4>
          <pre className="code">{pretty(Object.fromEntries(attrs))}</pre>
        </>
      )}
      {s.calls.length > 0 && (
        <>
          <h4>模型调用（{s.calls.length}）</h4>
          <ul className="insp-calls">
            {s.calls.map((c) => (
              <li key={c.seq}>
                <b>{c.record.role}</b> <span className="mono">{c.record.model}</span> ·{" "}
                {c.record.purpose || "—"} · {fmtMs(c.record.total_ms)}
                <div className="muted small">{truncate(c.record.response_text, 140)}</div>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

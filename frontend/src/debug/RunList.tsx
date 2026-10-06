import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "@/shared/api/client";
import type { AggregateMetrics, DebugRunItem } from "@/shared/api/types";
import { Icon } from "@/shared/ui/Icon";
import { fmtCost, fmtDateTime, fmtMs, fmtPct, fmtTokens, shortId, truncate } from "./format";
import { useAsync } from "./hooks";

const PAGE = 30;
const STATUSES = [
  ["", "全部状态"],
  ["succeeded", "成功"],
  ["failed", "失败"],
  ["running", "进行中"],
  ["awaiting_user", "等待用户"],
  ["cancelled", "已取消"],
] as const;

export function StatusPill({ status }: { status: string }) {
  const tone =
    status === "succeeded"
      ? "ok"
      : status === "failed"
        ? "bad"
        : status === "cancelled"
          ? "warn"
          : status === "running" || status === "awaiting_user"
            ? "info"
            : "";
  const label =
    {
      succeeded: "成功",
      failed: "失败",
      cancelled: "已取消",
      running: "进行中",
      awaiting_user: "等待用户",
      created: "已创建",
    }[status] ?? status;
  return <span className={`pill pill--${tone}`}>{label}</span>;
}

export function RunList() {
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [pipeline, setPipeline] = useState("");
  const [limit, setLimit] = useState(PAGE);
  const runs = useAsync(() => api.debug.runs({ status, limit }), [status, limit]);
  const agg = useAsync(() => api.debug.metrics(), []);

  const rows = useMemo(() => {
    const all = runs.data ?? [];
    const needle = q.trim().toLowerCase();
    return all.filter(
      (r) =>
        (!pipeline || r.run.pipeline === pipeline) &&
        (!needle ||
          r.input_text.toLowerCase().includes(needle) ||
          r.run.id.toLowerCase().includes(needle)),
    );
  }, [runs.data, q, pipeline]);
  const pipelines = useMemo(
    () => [...new Set((runs.data ?? []).map((r) => r.run.pipeline))],
    [runs.data],
  );

  return (
    <main className="dbg-main">
      <Summary agg={agg.data} />
      <div className="dbg-toolbar">
        <h1>运行</h1>
        <select
          className="select dbg-select"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          aria-label="状态"
        >
          {STATUSES.map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </select>
        <select
          className="select dbg-select"
          value={pipeline}
          onChange={(e) => setPipeline(e.target.value)}
          aria-label="管线"
        >
          <option value="">全部管线</option>
          {pipelines.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
        <input
          className="input dbg-search"
          placeholder="搜索输入或运行 ID"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label="搜索"
        />
        <button type="button" className="btn btn--sm" onClick={runs.reload}>
          <Icon name="refresh" size={14} /> 刷新
        </button>
      </div>

      {runs.error && <p className="notice notice--bad">{runs.error.message}</p>}
      <div className="dbg-tablewrap">
        <table className="dbg-table" data-testid="run-table">
          <thead>
            <tr>
              <th>时间</th>
              <th>输入</th>
              <th>管线</th>
              <th>状态</th>
              <th className="num">耗时</th>
              <th className="num">首反馈</th>
              <th className="num">模型调用</th>
              <th className="num">Token</th>
              <th className="num">缓存命中</th>
              <th className="num">成本</th>
              <th>Trace</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <Row key={r.run.id} r={r} />
            ))}
            {runs.loading && rows.length === 0 && (
              <tr>
                <td colSpan={11} className="muted dbg-empty">
                  加载中……
                </td>
              </tr>
            )}
            {!runs.loading && rows.length === 0 && (
              <tr>
                <td colSpan={11} className="muted dbg-empty">
                  没有符合条件的运行
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {(runs.data?.length ?? 0) >= limit && (
        <button type="button" className="btn dbg-more" onClick={() => setLimit(limit + PAGE)}>
          加载更多
        </button>
      )}
    </main>
  );
}

function Row({ r }: { r: DebugRunItem }) {
  const m = r.metrics;
  return (
    <tr>
      <td className="mono nowrap">{fmtDateTime(r.run.created_at)}</td>
      <td className="dbg-input">
        <Link to={`/debug/runs/${r.run.id}`} title={r.input_text}>
          {r.input_text ? (
            truncate(r.input_text, 60)
          ) : (
            <span className="muted">
              （{r.run.pipeline === "review" ? "手改后复核" : "无输入"}）
            </span>
          )}
        </Link>
        <span className="muted mono small"> {shortId(r.run.id)}</span>
      </td>
      <td className="mono">{r.run.pipeline}</td>
      <td>
        <StatusPill status={r.run.status} />
      </td>
      <td className="num mono">{fmtMs(m.e2e_ms)}</td>
      <td className="num mono">{fmtMs(m.first_feedback_ms)}</td>
      <td className="num mono">{m.n_llm_calls}</td>
      <td className="num mono">{fmtTokens(m.usage.prompt_tokens + m.usage.completion_tokens)}</td>
      <td className="num mono">{fmtPct(m.cache_hit_ratio)}</td>
      <td className="num mono">{fmtCost(m.cost, m.currency)}</td>
      <td>
        {m.trace_ok ? (
          <span className="pill pill--ok">完整</span>
        ) : (
          <span className="pill pill--bad" title={m.trace_problems.join("；")}>
            缺失
          </span>
        )}
      </td>
    </tr>
  );
}

function Summary({ agg }: { agg: AggregateMetrics | null }) {
  if (!agg) return <div className="dbg-cards dbg-cards--loading" aria-hidden />;
  const sr = agg.success_rate;
  const cards: { label: string; value: string; hint?: string }[] = [
    { label: "最近运行", value: String(agg.n_runs) },
    {
      label: "成功率",
      value: fmtPct(sr.p, 1),
      hint: `Wilson 区间 ${fmtPct(sr.lo)}–${fmtPct(sr.hi)}`,
    },
    { label: "端到端 p50 / p95", value: `${fmtMs(agg.e2e_ms.p50)} / ${fmtMs(agg.e2e_ms.p95)}` },
    { label: "首反馈 p50", value: fmtMs(agg.first_feedback_ms.p50), hint: "目标 ≤ 1s" },
    { label: "缓存命中", value: fmtPct(agg.cache_hit_ratio) },
    { label: "单次成本 均值", value: fmtCost(agg.cost_per_run.mean) },
    { label: "Trace 完整", value: fmtPct(agg.trace_complete.p, 1), hint: "目标 100%" },
  ];
  return (
    <section className="dbg-cards" aria-label="聚合指标">
      {cards.map((c) => (
        <div key={c.label} className="dbg-card">
          <span className="dbg-card__label">{c.label}</span>
          <strong className="dbg-card__value">{c.value}</strong>
          {c.hint && <span className="dbg-card__hint">{c.hint}</span>}
        </div>
      ))}
    </section>
  );
}

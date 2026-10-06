import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import type { RunMetrics } from "@/shared/api/types";
import { Icon } from "@/shared/ui/Icon";
import { BadcaseDialog } from "./BadcaseDialog";
import { Calls } from "./Calls";
import { EventsLog } from "./EventsLog";
import { fmtCost, fmtDateTime, fmtMs, fmtPct, fmtTokens } from "./format";
import { GraphView } from "./GraphView";
import { isForbidden, useRun } from "./hooks";
import { ItemsEvidence } from "./ItemsEvidence";
import { StatusPill } from "./RunList";
import { Timeline } from "./Timeline";

const TABS = [
  { id: "timeline", label: "流程与时间线", icon: "activity" },
  { id: "graph", label: "图检索", icon: "graph" },
  { id: "calls", label: "模型调用", icon: "chat" },
  { id: "items", label: "题目证据", icon: "shield" },
  { id: "events", label: "事件", icon: "list" },
] as const;
type TabId = (typeof TABS)[number]["id"];

export function RunDetail() {
  const { runId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const tab = (TABS.find((t) => t.id === params.get("tab"))?.id ?? "timeline") as TabId;
  const run = useRun(runId);
  const [badcaseOpen, setBadcaseOpen] = useState(false);
  const [badcaseItem, setBadcaseItem] = useState<string | null>(null);
  const [saved, setSaved] = useState("");
  const { detail, model } = run;

  if (run.error) {
    return (
      <main className="dbg-main">
        <p className="notice notice--bad">
          {isForbidden(run.error) ? "没有权限（令牌无效）。" : `加载失败：${run.error.message}`}
        </p>
        <Link to="/debug">返回列表</Link>
      </main>
    );
  }

  const setTab = (t: TabId) => setParams({ tab: t }, { replace: true });
  const m = detail?.metrics;
  const status = detail?.run.status ?? "created";
  const stageNames = [
    ...new Set([...model.spans.values()].filter((s) => s.kind === "stage").map((s) => s.name)),
  ];

  return (
    <main className="dbg-main" data-testid="run-detail">
      <div className="dbg-crumb">
        <Link to="/debug">
          <Icon name="left" size={14} /> 运行列表
        </Link>
      </div>
      <header className="dbg-head">
        <div className="dbg-head__main">
          <h1 title={detail?.input_text}>
            {detail?.input_text ||
              (detail?.run.pipeline === "review" ? "手改后复核" : "（无输入）")}
          </h1>
          <div className="dbg-head__meta">
            <StatusPill status={status} />
            {run.live && (
              <span className="pill pill--info">
                <span className="spinner" aria-hidden /> 实时
              </span>
            )}
            <span className="mono">{runId}</span>
            <span>
              管线 <b className="mono">{detail?.run.pipeline}</b>
            </span>
            {detail && <span>{fmtDateTime(detail.run.created_at)}</span>}
            {detail?.run.error && <span className="pill pill--bad">{detail.run.error.code}</span>}
          </div>
          {detail?.run.error && (
            <p className="notice notice--bad">
              {detail.run.error.user_message || detail.run.error.message}
            </p>
          )}
        </div>
        <div className="dbg-head__actions">
          <button
            type="button"
            className="btn"
            onClick={() => {
              setBadcaseItem(null);
              setBadcaseOpen(true);
            }}
            disabled={!detail}
          >
            <Icon name="flag" size={15} /> 标记为 Badcase
          </button>
        </div>
      </header>
      {saved && (
        <p className="notice" role="status">
          <Icon name="check" size={16} /> 已入库：{saved}
        </p>
      )}

      {m && <MetricsStrip m={m} />}
      {m && !m.trace_ok && (
        <p className="notice notice--bad" role="alert">
          Trace 不完整：{m.trace_problems.join("；")}
        </p>
      )}

      <nav className="dbg-tabs" role="tablist" aria-label="视图">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            className="dbg-tab"
            onClick={() => setTab(t.id)}
            data-testid={`tab-${t.id}`}
          >
            <Icon name={t.icon} size={15} /> {t.label}
            {t.id === "calls" && <span className="dbg-count">{model.calls.length}</span>}
            {t.id === "items" && <span className="dbg-count">{model.items.length}</span>}
            {t.id === "graph" && <span className="dbg-count">{model.retrievals.length}</span>}
          </button>
        ))}
      </nav>

      <section className="dbg-pane" role="tabpanel">
        {run.loading && <p className="muted">加载中……</p>}
        {!run.loading && tab === "timeline" && <Timeline model={model} metrics={m ?? null} />}
        {!run.loading && tab === "graph" && <GraphView steps={model.retrievals} />}
        {!run.loading && tab === "calls" && <Calls model={model} />}
        {!run.loading && tab === "items" && (
          <ItemsEvidence
            model={model}
            onFlag={(id) => {
              setBadcaseItem(id);
              setBadcaseOpen(true);
            }}
          />
        )}
        {!run.loading && tab === "events" && <EventsLog events={run.events} />}
      </section>

      {badcaseOpen && detail && (
        <BadcaseDialog
          runId={runId}
          input={detail.input_text}
          itemId={badcaseItem}
          stages={stageNames}
          onClose={() => setBadcaseOpen(false)}
          onSaved={(id) => {
            setBadcaseOpen(false);
            setSaved(id);
          }}
        />
      )}
    </main>
  );
}

function MetricsStrip({ m }: { m: RunMetrics }) {
  const items: { k: string; v: string; hint?: string }[] = [
    { k: "耗时", v: fmtMs(m.e2e_ms) },
    { k: "首反馈", v: fmtMs(m.first_feedback_ms), hint: "目标 ≤ 1s" },
    {
      k: "模型调用",
      v: `${m.n_llm_calls}`,
      hint: `重试 ${m.llm_retries} · 失败 ${m.llm_errors}${m.replayed_calls ? ` · 回放 ${m.replayed_calls}` : ""}`,
    },
    {
      k: "Token 入 / 出",
      v: `${fmtTokens(m.usage.prompt_tokens)} / ${fmtTokens(m.usage.completion_tokens)}`,
    },
    {
      k: "缓存命中",
      v: fmtPct(m.cache_hit_ratio),
      hint: `命中 ${fmtTokens(m.usage.cached_tokens)}`,
    },
    { k: "成本", v: fmtCost(m.cost, m.currency) },
    { k: "Span / 事件", v: `${m.n_spans} / ${m.n_events}` },
    { k: "结构化重试", v: String(m.schema_retries) },
  ];
  return (
    <section className="dbg-strip" aria-label="本次运行指标">
      {items.map((i) => (
        <div key={i.k} className="dbg-strip__item" title={i.hint}>
          <span>{i.k}</span>
          <strong className="mono">{i.v}</strong>
          {i.hint && <em>{i.hint}</em>}
        </div>
      ))}
    </section>
  );
}

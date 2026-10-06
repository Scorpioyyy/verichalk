import { useMemo, useState } from "react";
import type { LLMCallRecord } from "@/shared/api/types";
import { fmtCost, fmtMs, fmtPct, fmtTokens, pretty, truncate } from "./format";
import type { LLMCallItem, RunModel } from "./model";

const SEG_LABEL: Record<string, string> = {
  static: "静态",
  stable: "稳定",
  history: "历史",
  dynamic: "动态",
};

/** 模型调用检查器：列表看全局（谁调了谁、慢在哪、缓存命中如何），点开看提示分段、完整消息、输出与参数。 */
export function Calls({ model }: { model: RunModel }) {
  const [role, setRole] = useState("");
  const [q, setQ] = useState("");
  const [sel, setSel] = useState<number | null>(null);
  const roles = useMemo(() => [...new Set(model.calls.map((c) => c.record.role))], [model.calls]);
  const rows = useMemo(
    () =>
      model.calls.filter(
        (c) =>
          (!role || c.record.role === role) &&
          (!q.trim() ||
            `${c.record.purpose} ${c.record.model} ${c.record.prompt?.id ?? ""}`
              .toLowerCase()
              .includes(q.trim().toLowerCase())),
      ),
    [model.calls, role, q],
  );
  const cur = rows.find((c) => c.seq === sel) ?? null;

  if (model.calls.length === 0) return <p className="muted">这次运行没有模型调用。</p>;

  return (
    <div className="calls">
      <div className="dbg-toolbar">
        <select
          className="select dbg-select"
          value={role}
          onChange={(e) => setRole(e.target.value)}
          aria-label="角色"
        >
          <option value="">全部角色</option>
          {roles.map((r) => (
            <option key={r}>{r}</option>
          ))}
        </select>
        <input
          className="input dbg-search"
          placeholder="按用途 / 模型 / 提示词搜索"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label="搜索调用"
        />
        <span className="muted small">
          {rows.length} / {model.calls.length} 次
        </span>
      </div>
      <div className="calls-body">
        <div className="dbg-tablewrap calls-list">
          <table className="dbg-table dbg-table--click" data-testid="calls-table">
            <thead>
              <tr>
                <th>+时间</th>
                <th>角色</th>
                <th>模型</th>
                <th>用途</th>
                <th>提示词</th>
                <th className="num">耗时</th>
                <th className="num">TTFT</th>
                <th className="num">入 / 缓存 / 出</th>
                <th className="num">成本</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <CallRow
                  key={c.seq}
                  c={c}
                  t0={model.t0}
                  on={cur?.seq === c.seq}
                  onClick={() => setSel(c.seq)}
                />
              ))}
            </tbody>
          </table>
        </div>
        <aside className="calls-insp" aria-label="调用详情">
          {cur ? (
            <CallDetail r={cur.record} />
          ) : (
            <p className="muted">点击一行，查看这次调用的提示、输出与参数。</p>
          )}
        </aside>
      </div>
    </div>
  );
}

function CallRow({
  c,
  t0,
  on,
  onClick,
}: {
  c: LLMCallItem;
  t0: number;
  on: boolean;
  onClick: () => void;
}) {
  const r = c.record;
  return (
    <tr
      className={on ? "row--on" : ""}
      onClick={onClick}
      tabIndex={0}
      onKeyDown={(e) => e.key === "Enter" && onClick()}
    >
      <td className="mono nowrap">+{fmtMs((c.ts - t0) * 1000)}</td>
      <td>
        <span className={`role role--${r.role}`}>{r.role}</span>
      </td>
      <td className="mono small">{r.model}</td>
      <td className="small">
        {r.purpose || "—"}
        {r.error && <span className="pill pill--bad"> 失败</span>}
        {r.retries > 0 && <span className="pill pill--warn"> 重试 {r.retries}</span>}
        {r.from_cassette && <span className="pill"> 回放</span>}
      </td>
      <td className="mono small">{r.prompt ? `${r.prompt.id}@${r.prompt.version}` : "—"}</td>
      <td className="num mono">{fmtMs(r.total_ms)}</td>
      <td className="num mono">{fmtMs(r.ttft_ms)}</td>
      <td className="num mono">
        {fmtTokens(r.usage.prompt_tokens)} / {fmtTokens(r.usage.cached_tokens)} /{" "}
        {fmtTokens(r.usage.completion_tokens)}
      </td>
      <td className="num mono">{fmtCost(r.cost, r.currency)}</td>
    </tr>
  );
}

function contentText(c: unknown): string {
  if (c === null || c === undefined) return "";
  if (typeof c === "string") return c;
  if (Array.isArray(c)) {
    return c
      .map((p) => {
        const o = p as { type?: string; text?: string };
        return o.type === "text" ? (o.text ?? "") : `[${o.type ?? "内容"}]`;
      })
      .join("\n");
  }
  return pretty(c);
}

function CallDetail({ r }: { r: LLMCallRecord }) {
  const [tab, setTab] = useState<"prompt" | "output" | "params">("prompt");
  const segs = r.prompt?.segments ?? [];
  const totalChars = segs.reduce((n, s) => n + s.chars, 0) || 1;
  const hit = r.usage.prompt_tokens ? r.usage.cached_tokens / r.usage.prompt_tokens : 0;
  return (
    <div className="cd" data-testid="call-detail">
      <h3>
        <span className={`role role--${r.role}`}>{r.role}</span>{" "}
        <span className="mono">{r.model}</span>
      </h3>
      <p className="muted small">
        {r.purpose} · {r.profile} · 结束原因 {r.finish_reason ?? "—"} · 缓存命中 {fmtPct(hit)}（
        {r.usage.cached_tokens}/{r.usage.prompt_tokens}）
      </p>
      {r.error && (
        <p className="notice notice--bad">
          {r.error.code}：{r.error.message}
        </p>
      )}

      {segs.length > 0 && (
        <div className="seg-bar" aria-label="提示词分段（静态 → 稳定 → 动态）">
          <div className="seg-bar__track">
            {segs.map((s) => (
              <span
                key={s.name}
                className={`seg-bar__seg seg-bar__seg--${s.name}`}
                style={{ width: `${(s.chars / totalChars) * 100}%` }}
                title={`${SEG_LABEL[s.name] ?? s.name}：${s.chars} 字 · ${s.hash}`}
              >
                {SEG_LABEL[s.name] ?? s.name}
              </span>
            ))}
            {hit > 0 && (
              <i
                className="seg-bar__hit"
                style={{ left: `${Math.min(hit, 1) * 100}%` }}
                title={`缓存命中约 ${fmtPct(hit)}`}
              />
            )}
          </div>
          <div className="muted small">
            {segs.map((s) => `${SEG_LABEL[s.name] ?? s.name} ${s.chars} 字`).join(" · ")}
            {r.prompt && (
              <>
                {" · "}前缀哈希 <span className="mono">{r.prompt.prefix_hash.slice(0, 8)}</span>
              </>
            )}
          </div>
        </div>
      )}

      <div className="seg seg--sm" role="tablist">
        {(
          [
            ["prompt", "提示"],
            ["output", "输出"],
            ["params", "参数与用量"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            className="seg__btn"
            aria-selected={tab === id}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "prompt" && (
        <div className="cd-msgs">
          {r.messages.map((m, i) => {
            const text = contentText((m as { content?: unknown }).content);
            return (
              <details key={i} open={i === r.messages.length - 1 || text.length < 600}>
                <summary>
                  <b>{String((m as { role?: string }).role ?? "?")}</b>{" "}
                  <span className="muted small">
                    {text.length} 字 · {truncate(text, 70)}
                  </span>
                </summary>
                <pre className="code">{text}</pre>
              </details>
            );
          })}
        </div>
      )}
      {tab === "output" && (
        <div className="cd-msgs">
          {r.reasoning_text && (
            <details>
              <summary>
                <b>思考过程</b> <span className="muted small">{r.reasoning_text.length} 字</span>
              </summary>
              <pre className="code">{r.reasoning_text}</pre>
            </details>
          )}
          <pre className="code">{r.response_text || "（空）"}</pre>
          {r.tool_calls.length > 0 && <pre className="code">{pretty(r.tool_calls)}</pre>}
        </div>
      )}
      {tab === "params" && (
        <div>
          <dl className="kv">
            <dt>首字节 / 首 token</dt>
            <dd className="mono">
              {fmtMs(r.ttfb_ms)} / {fmtMs(r.ttft_ms)}
            </dd>
            <dt>总耗时</dt>
            <dd className="mono">{fmtMs(r.total_ms)}</dd>
            <dt>用量</dt>
            <dd className="mono">
              入 {r.usage.prompt_tokens}（缓存 {r.usage.cached_tokens}）· 出{" "}
              {r.usage.completion_tokens} · 推理 {r.usage.reasoning_tokens}
            </dd>
            <dt>成本</dt>
            <dd className="mono">{fmtCost(r.cost, r.currency)}</dd>
            <dt>重试</dt>
            <dd>{r.retries}</dd>
            <dt>调用 ID</dt>
            <dd className="mono small">{r.id}</dd>
          </dl>
          <pre className="code">{pretty(r.params)}</pre>
        </div>
      )}
    </div>
  );
}

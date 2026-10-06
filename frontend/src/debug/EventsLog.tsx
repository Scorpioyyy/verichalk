import { Fragment, useMemo, useState } from "react";
import type { AppEvent } from "@/shared/api/types";
import { fmtClock, pretty, truncate } from "./format";

const SHOW = 200;

function brief(e: AppEvent): string {
  switch (e.type) {
    case "progress":
      return e.label + (e.total ? ` ${e.current ?? 0}/${e.total}` : "");
    case "span.started":
    case "span.finished":
      return `${e.kind} · ${e.name}${e.type === "span.finished" ? ` · ${e.status} · ${Math.round(e.duration_ms)}ms` : ""}`;
    case "llm.call":
      return `${e.record.role} · ${e.record.purpose} · ${Math.round(e.record.total_ms)}ms`;
    case "item.status":
      return `${e.item_id.slice(-8)} → ${e.status}`;
    case "item.delivered":
      return `第 ${e.order} 道 · ${e.item.id.slice(-8)}`;
    case "paper.patch":
      return `rev ${e.rev} · ${e.summary}`;
    case "message.done":
      return truncate(e.text, 80);
    case "retrieval.result":
      return `${e.payload.step} · ${e.payload.nodes.length} 节点`;
    case "checkpoint.requested":
      return `${e.kind} · ${truncate(e.prompt, 60)}`;
    case "run.finished":
      return e.status;
    default:
      return "";
  }
}

/** 原始事件日志：与服务端落库的完全一致（包含 debug 可见性的事件），可按类型筛选、搜索并展开看 JSON。 */
export function EventsLog({ events }: { events: AppEvent[] }) {
  const [type, setType] = useState("");
  const [q, setQ] = useState("");
  const [limit, setLimit] = useState(SHOW);
  const [open, setOpen] = useState<number | null>(null);
  const types = useMemo(() => [...new Set(events.map((e) => e.type))].sort(), [events]);
  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return events.filter(
      (e) =>
        (!type || e.type === type) &&
        (!needle || `${e.type} ${brief(e)}`.toLowerCase().includes(needle)),
    );
  }, [events, type, q]);
  const t0 = events[0]?.ts ?? 0;
  return (
    <div>
      <div className="dbg-toolbar">
        <select
          className="select dbg-select"
          value={type}
          onChange={(e) => setType(e.target.value)}
          aria-label="事件类型"
        >
          <option value="">全部类型（{events.length}）</option>
          {types.map((t) => (
            <option key={t} value={t}>
              {t}（{events.filter((e) => e.type === t).length}）
            </option>
          ))}
        </select>
        <input
          className="input dbg-search"
          placeholder="搜索"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label="搜索事件"
        />
      </div>
      <div className="dbg-tablewrap">
        <table className="dbg-table dbg-table--click" data-testid="events-table">
          <thead>
            <tr>
              <th className="num">seq</th>
              <th>+时间</th>
              <th>类型</th>
              <th>可见性</th>
              <th>摘要</th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, limit).map((e) => (
              <Fragment key={e.seq}>
                <tr
                  key={e.seq}
                  onClick={() => setOpen(open === e.seq ? null : e.seq)}
                  className={open === e.seq ? "row--on" : ""}
                >
                  <td className="num mono">{e.seq}</td>
                  <td className="mono nowrap">
                    +{((e.ts - t0) * 1000).toFixed(0)}ms{" "}
                    <span className="muted small">{fmtClock(e.ts)}</span>
                  </td>
                  <td className="mono">{e.type}</td>
                  <td>{e.visibility === "user" ? "用户" : "调试"}</td>
                  <td className="small">{brief(e)}</td>
                </tr>
                {open === e.seq && (
                  <tr key={`${e.seq}-json`} className="row--json">
                    <td colSpan={5}>
                      <pre className="code">{pretty(e)}</pre>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length > limit && (
        <button type="button" className="btn dbg-more" onClick={() => setLimit(limit + SHOW)}>
          再显示 {Math.min(SHOW, rows.length - limit)} 条（共 {rows.length}）
        </button>
      )}
    </div>
  );
}

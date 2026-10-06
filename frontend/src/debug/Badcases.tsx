import { Link } from "react-router-dom";
import { api } from "@/shared/api/client";
import { fmtDateTime, truncate } from "./format";
import { useAsync } from "./hooks";
import { ROOT_CAUSES } from "./BadcaseDialog";

export function Badcases() {
  const list = useAsync(() => api.debug.badcases(), []);
  return (
    <main className="dbg-main">
      <div className="dbg-toolbar">
        <h1>Badcase</h1>
        <span className="muted small">
          按根因类别在对应层修复；记录是 YAML 文件（开发态写入
          eval/badcases/，部署态写入数据目录）。
        </span>
      </div>
      {list.error && <p className="notice notice--bad">{list.error.message}</p>}
      <div className="dbg-tablewrap">
        <table className="dbg-table" data-testid="badcase-table">
          <thead>
            <tr>
              <th>时间</th>
              <th>ID</th>
              <th>严重度</th>
              <th>根因</th>
              <th>问题</th>
              <th>运行</th>
              <th>状态</th>
            </tr>
          </thead>
          <tbody>
            {(list.data ?? []).map((b) => (
              <tr key={b.id}>
                <td className="mono nowrap">{fmtDateTime(b.created_at)}</td>
                <td className="mono small">{b.id.slice(-10)}</td>
                <td>
                  <span
                    className={`pill pill--${b.severity === "S1" ? "bad" : b.severity === "S2" ? "warn" : ""}`}
                  >
                    {b.severity}
                  </span>
                </td>
                <td title={ROOT_CAUSES.find((c) => c.id === b.root_cause)?.where}>
                  <b>{b.root_cause}</b> {ROOT_CAUSES.find((c) => c.id === b.root_cause)?.name}
                </td>
                <td className="small">{truncate(b.problem, 90)}</td>
                <td>
                  <Link to={`/debug/runs/${b.run_id}`} className="mono small">
                    {b.run_id.slice(-8)}
                  </Link>
                </td>
                <td>{b.status === "open" ? "待修复" : b.status === "fixed" ? "已修复" : "不修"}</td>
              </tr>
            ))}
            {!list.loading && (list.data ?? []).length === 0 && (
              <tr>
                <td colSpan={7} className="muted dbg-empty">
                  还没有 Badcase。在运行详情里点“标记为 Badcase”入库。
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </main>
  );
}

import { useState } from "react";
import { CHECK_STATUS_LABEL, STATUS_VIEW } from "@/shared/labels";
import { Rich } from "@/shared/math/Rich";
import { Icon } from "@/shared/ui/Icon";
import { fmtClock, pretty } from "./format";
import type { ItemEvidence, RunModel } from "./model";

/** 题目证据：每道题的核验各项（求解程序输出、盲解过程、边界特征与违规、质量判官的各项）与状态变化历史。 */
export function ItemsEvidence({
  model,
  onFlag,
}: {
  model: RunModel;
  onFlag: (itemId: string) => void;
}) {
  if (model.items.length === 0) {
    return <p className="muted">这次运行没有产出或核验题目（例如澄清、追问、导出）。</p>;
  }
  return (
    <div className="ev" data-testid="items-evidence">
      {model.items.map((x) => (
        <ItemBlock key={x.id} x={x} onFlag={() => onFlag(x.id)} />
      ))}
    </div>
  );
}

function ItemBlock({ x, onFlag }: { x: ItemEvidence; onFlag: () => void }) {
  const [open, setOpen] = useState(false);
  const last = x.statuses[x.statuses.length - 1];
  const status = last?.status ?? x.item?.verification.status ?? "pending";
  const checks = last?.checks ?? x.item?.verification.checks ?? [];
  const sv = STATUS_VIEW[status];
  return (
    <article className="ev-item">
      <header className="ev-item__head">
        <span className="mono small">{x.order !== null ? `#${x.order}` : x.id.slice(-8)}</span>
        <span className={`pill pill--${sv.tone === "neutral" ? "" : sv.tone}`}>{sv.label}</span>
        {x.item && (
          <span className="muted small">
            {x.item.kind} · 难度 {x.item.difficulty} · {x.item.tier}
            {x.item.provenance.model && ` · ${x.item.provenance.model}`}
          </span>
        )}
        <span className="dbg-spacer" />
        <button type="button" className="btn btn--sm btn--ghost" onClick={onFlag}>
          <Icon name="flag" size={13} /> Badcase
        </button>
      </header>
      {x.item ? (
        <div className="ev-item__body">
          <Rich text={x.item.stem} />
          {x.item.options.length > 0 && (
            <ol className="ev-opts" type="A">
              {x.item.options.map((o, i) => (
                <li key={i}>
                  <Rich text={o} inline />
                </li>
              ))}
            </ol>
          )}
          <p className="small">
            <b>答案</b> <Rich text={x.item.answer} inline />{" "}
            {x.item.answer_value !== null && (
              <span className="muted mono">（精确值 {JSON.stringify(x.item.answer_value)}）</span>
            )}
          </p>
          {x.item.solution && (
            <p className="small muted">
              <b>解析</b> <Rich text={x.item.solution} inline />
            </p>
          )}
        </div>
      ) : (
        <p className="muted small">（这次运行没有送达这道题的内容，只有状态变化：{x.id}）</p>
      )}

      <table className="dbg-table ev-checks">
        <thead>
          <tr>
            <th>检查</th>
            <th>结果</th>
            <th>说明</th>
            <th>证据</th>
          </tr>
        </thead>
        <tbody>
          {checks.map((c) => (
            <tr key={c.name} data-status={c.status}>
              <td className="mono">{c.name}</td>
              <td>
                <span
                  className={`pill pill--${c.status === "pass" ? "ok" : c.status === "warn" ? "warn" : c.status === "fail" ? "bad" : ""}`}
                >
                  {CHECK_STATUS_LABEL[c.status]}
                </span>
              </td>
              <td className="small">{c.detail || <span className="muted">—</span>}</td>
              <td>
                {Object.keys(c.evidence).length > 0 ? (
                  <details>
                    <summary className="small">
                      {Object.keys(c.evidence).slice(0, 4).join(" · ")}
                    </summary>
                    <pre className="code">{pretty(c.evidence)}</pre>
                  </details>
                ) : (
                  <span className="muted">—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {x.statuses.length > 1 && (
        <>
          <button type="button" className="link-btn" onClick={() => setOpen(!open)}>
            {open ? "收起" : "展开"}状态变化（{x.statuses.length} 次）
          </button>
          {open && (
            <ol className="ev-history small">
              {x.statuses.map((s) => (
                <li key={s.seq}>
                  <span className="mono">{fmtClock(s.ts)}</span> → {STATUS_VIEW[s.status].label}（
                  {s.checks
                    .filter((c) => c.status === "warn" || c.status === "fail")
                    .map((c) => c.name)
                    .join("、") || "全部通过"}
                  ）
                </li>
              ))}
            </ol>
          )}
        </>
      )}
    </article>
  );
}

import { useEffect, useState } from "react";
import type { RunState } from "@/shared/events/reducer";
import { fmtDuration } from "@/shared/labels";
import { Icon } from "@/shared/ui/Icon";

/** 任务进行中的进度卡：当前步骤、轨迹、已用时间（每秒更新，让教师知道没有卡住），可停止。 */
export function RunProgress({ run, onStop }: { run: RunState; onStop: () => void }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const cur = run.progress;
  const steps = run.steps.slice(-5);
  const elapsed = run.startedAt ? now - run.startedAt : 0;
  const pct = cur?.total ? Math.min(100, Math.round(((cur.current ?? 0) / cur.total) * 100)) : null;

  return (
    <div className="progress" role="status" aria-live="polite" data-testid="run-progress">
      <div className="progress__head">
        <span className="spinner" aria-hidden />
        <span className="progress__now">{cur?.label ?? "正在开始"}</span>
      </div>
      {pct !== null && (
        <div
          className="progress__bar"
          role="progressbar"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="完成进度"
        >
          <span style={{ width: `${pct}%` }} />
        </div>
      )}
      {steps.length > 1 && (
        <ol className="progress__trail">
          {steps.slice(0, -1).map((s, i) => (
            <li key={`${s.label}-${i}`}>
              <Icon name="check" size={13} /> {s.label}
            </li>
          ))}
        </ol>
      )}
      <div className="progress__foot">
        <span className="progress__time">已用 {fmtDuration(elapsed)}</span>
        <button type="button" className="btn btn--ghost btn--sm" onClick={onStop}>
          <Icon name="stop" size={14} /> 停止
        </button>
      </div>
    </div>
  );
}

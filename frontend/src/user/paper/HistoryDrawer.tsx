import { useEffect, useState } from "react";
import { api } from "@/shared/api/client";
import type { ItemChange, Paper, PaperDiff, PaperHistory, Revision } from "@/shared/api/types";
import { Rich } from "@/shared/math/Rich";
import { Icon } from "@/shared/ui/Icon";
import { userMessageOf } from "../controller";

const FIELD_LABEL: Record<string, string> = {
  kind: "题型",
  stem: "题干",
  options: "选项",
  answer: "答案",
  solution: "解析",
  kp_ids: "涉及知识点",
  difficulty: "难度",
  tier: "档位",
  score: "分值",
  figures: "插图",
};
const SHOWN_AS_TEXT = ["stem", "answer"] as const;

function ago(ts: number): string {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "刚刚";
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  return new Date(ts * 1000).toLocaleDateString("zh-CN");
}

const KIND_TEXT: Record<Revision["kind"], string> = {
  edit: "修改",
  undo: "撤销",
  redo: "重做",
  restore: "回退",
  review: "核验",
};

export interface DiffTarget {
  from: number;
  to: number;
}

function itemOf(paper: Paper | null, no: number | null) {
  if (!paper || !no) return null;
  return paper.sections.flatMap((s) => s.items)[no - 1] ?? null;
}

/** 两个版本之间的差异：哪些题新增 / 删除 / 修改，修改的题给出前后对比。 */
export function DiffView({ sessionId, target }: { sessionId: string; target: DiffTarget }) {
  const [state, setState] = useState<{ diff: PaperDiff; a: Paper; b: Paper } | "loading" | "error">(
    "loading",
  );
  useEffect(() => {
    let live = true;
    setState("loading");
    Promise.all([
      api.diff(sessionId, target.from, target.to),
      api.paperAt(sessionId, target.from),
      api.paperAt(sessionId, target.to),
    ])
      .then(([diff, a, b]) => live && setState({ diff, a, b }))
      .catch(() => live && setState("error"));
    return () => {
      live = false;
    };
  }, [sessionId, target.from, target.to]);

  if (state === "loading") return <p className="muted">正在对比……</p>;
  if (state === "error") return <p className="muted">暂时看不了这次的改动。</p>;
  const { diff, a, b } = state;
  if (diff.items.length === 0 && !diff.title_changed && !diff.sections_changed) {
    return <p className="muted">没有题目内容的变化（可能只更新了核验状态）。</p>;
  }
  return (
    <ul className="diff" data-testid="diff">
      {diff.title_changed && <li className="diff__row">试卷标题已修改</li>}
      {diff.items.map((c) => (
        <DiffRow key={c.item_id} change={c} a={a} b={b} />
      ))}
    </ul>
  );
}

function DiffRow({ change, a, b }: { change: ItemChange; a: Paper; b: Paper }) {
  const before = itemOf(a, change.number_before);
  const after = itemOf(b, change.number_after);
  if (change.change === "added") {
    return (
      <li className="diff__row">
        <span className="tag tag--ok">新增</span> 第 {change.number_after} 题
        {after && (
          <div className="diff__after">
            <Rich text={after.stem} />
          </div>
        )}
      </li>
    );
  }
  if (change.change === "removed") {
    return (
      <li className="diff__row">
        <span className="tag tag--bad">删除</span> 原第 {change.number_before} 题
        {before && (
          <div className="diff__before">
            <Rich text={before.stem} />
          </div>
        )}
      </li>
    );
  }
  if (change.change === "moved") {
    return (
      <li className="diff__row">
        <span className="tag">移动</span> 第 {change.number_before} 题 → 第 {change.number_after} 题
      </li>
    );
  }
  return (
    <li className="diff__row">
      <span className="tag tag--info">修改</span> 第 {change.number_after} 题：
      {change.fields.map((f) => FIELD_LABEL[f] ?? f).join("、")}
      {SHOWN_AS_TEXT.filter((f) => change.fields.includes(f) && before && after).map((f) => (
        <div key={f} className="diff__pair">
          <div className="diff__before">
            <span className="diff__label">原{FIELD_LABEL[f]}</span>
            <Rich text={before![f]} inline />
          </div>
          <div className="diff__after">
            <span className="diff__label">现{FIELD_LABEL[f]}</span>
            <Rich text={after![f]} inline />
          </div>
        </div>
      ))}
    </li>
  );
}

interface Props {
  sessionId: string;
  initialDiff?: DiffTarget | null;
  onRestore: (rev: number) => Promise<void>;
  onClose: () => void;
}

/** 版本历史抽屉：看每一版改了什么，一键回到某一版（回退本身也是一条新记录，随时可撤销）。 */
export function HistoryDrawer({ sessionId, initialDiff, onRestore, onClose }: Props) {
  const [hist, setHist] = useState<PaperHistory | null>(null);
  const [error, setError] = useState("");
  const [diff, setDiff] = useState<DiffTarget | null>(initialDiff ?? null);

  useEffect(() => {
    let live = true;
    api
      .history(sessionId)
      .then((h) => live && setHist(h))
      .catch((e) => live && setError(userMessageOf(e)));
    return () => {
      live = false;
    };
  }, [sessionId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const head = hist?.revisions[hist.revisions.length - 1];
  const currentLogical = head ? (head.logical ?? head.rev) : 0;
  // 只列"版本"（修改 / 回退）；撤销、重做与后台核验不是新的内容版本
  const rows = (hist?.revisions ?? [])
    .filter((r) => r.kind === "edit" || r.kind === "restore")
    .reverse();

  return (
    <aside className="drawer" aria-label="版本历史" data-testid="history-drawer">
      <header className="drawer__head">
        <h2>版本历史</h2>
        <button
          type="button"
          className="btn btn--ghost btn--icon"
          onClick={onClose}
          aria-label="关闭"
        >
          <Icon name="x" />
        </button>
      </header>
      <div className="drawer__body">
        {error && <p className="notice notice--bad">{error}</p>}
        {!hist && !error && <div className="shimmer" style={{ height: 64 }} />}
        <ol className="versions">
          {rows.map((r, i) => {
            const prev = rows[i + 1];
            const isCurrent = r.rev === currentLogical;
            const open = diff?.to === r.rev;
            return (
              <li key={r.rev} className={`version ${isCurrent ? "version--current" : ""}`}>
                <div className="version__main">
                  <span className="version__dot" aria-hidden />
                  <div className="version__text">
                    <strong>
                      {KIND_TEXT[r.kind]}
                      {r.author === "agent" ? "（助手）" : "（您）"}
                    </strong>
                    <span>{r.summary || "—"}</span>
                    <time className="muted">{ago(r.ts)}</time>
                  </div>
                </div>
                <div className="version__actions">
                  {prev && (
                    <button
                      type="button"
                      className="link-btn"
                      onClick={() => setDiff(open ? null : { from: prev.rev, to: r.rev })}
                      aria-expanded={open}
                    >
                      {open ? "收起改动" : "看改动"}
                    </button>
                  )}
                  {isCurrent ? (
                    <span className="tag">当前</span>
                  ) : (
                    <button
                      type="button"
                      className="link-btn"
                      onClick={() => void onRestore(r.rev).then(onClose)}
                    >
                      回到这一版
                    </button>
                  )}
                </div>
                {open && diff && <DiffView sessionId={sessionId} target={diff} />}
              </li>
            );
          })}
        </ol>
        {hist && rows.length === 0 && <p className="muted">还没有修改记录。</p>}
        {!initialDiff?.to
          ? null
          : diff &&
            !rows.some((r) => r.rev === diff.to) && (
              <section className="version-extra">
                <h3>这次的改动</h3>
                <DiffView sessionId={sessionId} target={diff} />
              </section>
            )}
      </div>
    </aside>
  );
}

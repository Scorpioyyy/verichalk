import { useEffect, useMemo, useState } from "react";
import { api } from "@/shared/api/client";
import { isActive } from "@/shared/events/reducer";
import { fmtScore } from "@/shared/labels";
import { Icon } from "@/shared/ui/Icon";
import type { SessionController, ViewState } from "../controller";
import { ChipsBar } from "./ChipsBar";
import { ExportDialog } from "./ExportDialog";
import { HistoryDrawer } from "./HistoryDrawer";
import { ItemCard } from "./ItemCard";
import { ItemEditor } from "./ItemEditor";
import { PaperPreview } from "./PaperPreview";
import { numbered, sectionHeading, statusCounts, totalScore } from "./model";

interface Props {
  view: ViewState;
  ctl: SessionController;
  onAskItem: (no: number) => void;
  onApply: (text: string) => void;
  onPrefill: (text: string) => void;
}

const LS_AUDIENCE = "verichalk.audience";

function loadAudience(): "teacher" | "student" {
  try {
    return localStorage.getItem(LS_AUDIENCE) === "student" ? "student" : "teacher";
  } catch {
    return "teacher";
  }
}

export function Workspace({ view, ctl, onAskItem, onApply, onPrefill }: Props) {
  const { paper, run, sessionId } = view;
  const [mode, setMode] = useState<"work" | "paper">("work");
  const [audience, setAudience] = useState<"teacher" | "student">(loadAudience);
  const [editing, setEditing] = useState<string | null>(null);
  const [exportOpen, setExportOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyDiff, setHistoryDiff] = useState<{ from: number; to: number } | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const active = isActive(run);
  const teacher = audience === "teacher";

  useEffect(() => {
    try {
      localStorage.setItem(LS_AUDIENCE, audience);
    } catch {
      /* 记不住也没关系 */
    }
  }, [audience]);

  // 刚修改的题高亮 2.5 秒后自动取消
  useEffect(() => {
    const ids = Object.keys(view.changed);
    if (!ids.length) return;
    const t = setTimeout(() => ids.forEach((id) => ctl.clearChanged(id)), 2500);
    return () => clearTimeout(t);
  }, [view.changed, ctl]);

  const figureUrl = useMemo(
    () => (id: string) => (sessionId ? api.figureUrl(sessionId, id) : ""),
    [sessionId],
  );

  const items = useMemo(() => (paper ? numbered(paper) : []), [paper]);
  const inPaper = useMemo(() => new Set(items.map((x) => x.item.id)), [items]);
  // 草稿只在这次运行进行中才显示；运行结束后试卷才是唯一的事实（教师删掉的题不能又以草稿的身份回来）
  const drafts = active ? (run?.delivered ?? []).filter((d) => !inPaper.has(d.id)) : [];
  const counts = paper ? statusCounts(paper) : null;
  const total = paper ? totalScore(paper) : null;
  const minutes = paper?.meta?.duration_minutes as number | undefined;

  async function remove(no: number, id: string) {
    if (await ctl.editOps([{ op: "remove_item", item_id: id }])) {
      ctl.toast(`已删除第 ${no} 题`, "info", { label: "撤销", run: () => void ctl.undo() });
    }
  }

  async function move(sectionId: string, id: string, index: number) {
    await ctl.editOps([{ op: "move_item", item_id: id, section_id: sectionId, index }]);
  }

  async function saveTitle() {
    setRenaming(false);
    const t = titleDraft.trim();
    if (paper && t && t !== paper.title) await ctl.editOps([{ op: "set_title", title: t }]);
  }

  if (!paper && drafts.length === 0) {
    return (
      <section className="ws ws--empty" aria-label="试卷" data-testid="workspace">
        {active ? <Skeleton /> : <EmptyWorkspace />}
      </section>
    );
  }

  const bySection = paper?.sections ?? [];
  let runningNo = items.length;

  return (
    <section className="ws" aria-label="试卷" data-testid="workspace">
      <header className="ws__bar">
        <div className="ws__titlebox">
          {renaming ? (
            <input
              className="input ws__title-input"
              autoFocus
              value={titleDraft}
              aria-label="试卷标题"
              onChange={(e) => setTitleDraft(e.target.value)}
              onBlur={() => void saveTitle()}
              onKeyDown={(e) => {
                if (e.key === "Enter") void saveTitle();
                if (e.key === "Escape") setRenaming(false);
              }}
            />
          ) : (
            <h1 className="ws__title">
              <button
                type="button"
                className="ws__title-btn"
                title="点击修改标题"
                disabled={!paper}
                onClick={() => {
                  setTitleDraft(paper?.title ?? "");
                  setRenaming(true);
                }}
              >
                {paper?.title || "试卷"}
                <Icon name="edit" size={14} />
              </button>
            </h1>
          )}
          <p className="ws__stats">
            <span>
              {drafts.length > 0 && active
                ? `已出 ${items.length + drafts.length} 题，还在继续`
                : `共 ${items.length + drafts.length} 题`}
              {total !== null && ` · 满分 ${fmtScore(total)} 分`}
              {minutes ? ` · 建议用时 ${minutes} 分钟` : ""}
            </span>
            {counts && items.length > 0 && (
              <span className="ws__summary" aria-label="核验情况">
                {counts.verified > 0 && (
                  <span className="badge badge--ok">已核验 {counts.verified}</span>
                )}
                {counts.checked > 0 && (
                  <span className="badge badge--info">已校对 {counts.checked}</span>
                )}
                {counts.needs_review > 0 && (
                  <span className="badge badge--warn">需复核 {counts.needs_review}</span>
                )}
                {counts.pending > 0 && <span className="badge">待核验 {counts.pending}</span>}
              </span>
            )}
          </p>
        </div>
        <div className="ws__tools">
          <div className="ws__undo" role="group" aria-label="撤销与历史">
            <button
              type="button"
              className="btn btn--ghost btn--icon"
              onClick={() => void ctl.undo()}
              disabled={!view.history.canUndo || active}
              aria-label="撤销"
              title="撤销"
            >
              <Icon name="undo" />
            </button>
            <button
              type="button"
              className="btn btn--ghost btn--icon"
              onClick={() => void ctl.redo()}
              disabled={!view.history.canRedo || active}
              aria-label="恢复"
              title="恢复"
            >
              <Icon name="redo" />
            </button>
            <button
              type="button"
              className="btn btn--ghost btn--icon"
              onClick={() => {
                setHistoryDiff(null);
                setHistoryOpen(true);
              }}
              disabled={!paper}
              aria-label="版本历史"
              title="版本历史"
            >
              <Icon name="history" />
            </button>
          </div>
          <div className="seg" role="group" aria-label="查看对象">
            <button
              type="button"
              className="seg__btn"
              aria-pressed={teacher}
              onClick={() => setAudience("teacher")}
            >
              教师版
            </button>
            <button
              type="button"
              className="seg__btn"
              aria-pressed={!teacher}
              onClick={() => setAudience("student")}
            >
              学生版
            </button>
          </div>
          <div className="seg" role="group" aria-label="视图">
            <button
              type="button"
              className="seg__btn"
              aria-pressed={mode === "work"}
              onClick={() => setMode("work")}
            >
              题目
            </button>
            <button
              type="button"
              className="seg__btn"
              aria-pressed={mode === "paper"}
              onClick={() => setMode("paper")}
              disabled={!paper}
            >
              试卷
            </button>
          </div>
          <button
            type="button"
            className="btn btn--primary"
            onClick={() => setExportOpen(true)}
            disabled={!paper || active}
            data-testid="export-open"
          >
            <Icon name="download" size={16} /> 导出
          </button>
        </div>
      </header>

      {view.understanding && (
        <ChipsBar
          understanding={view.understanding}
          disabled={active}
          onApply={onApply}
          onPrefill={onPrefill}
        />
      )}

      {view.lastEdit && (
        <div className="banner" role="status" data-testid="last-edit">
          <Icon name="sparkle" size={16} />
          <span>已按您的要求修改：{view.lastEdit.summary}</span>
          <button
            type="button"
            className="link-btn"
            onClick={() => {
              setHistoryDiff({ from: view.lastEdit!.fromRev, to: view.lastEdit!.toRev });
              setHistoryOpen(true);
            }}
          >
            查看改动
          </button>
          <button type="button" className="link-btn" onClick={() => void ctl.undo()}>
            撤销
          </button>
          <button
            type="button"
            className="btn btn--ghost btn--icon btn--sm"
            onClick={() => ctl.dismissLastEdit()}
            aria-label="关闭提示"
          >
            <Icon name="x" size={14} />
          </button>
        </div>
      )}

      <div className="ws__scroll">
        {mode === "paper" && paper && sessionId ? (
          <PaperPreview sessionId={sessionId} rev={paper.rev} teacher={teacher} />
        ) : (
          <div className="ws__sheet">
            {bySection.map((section, si) => {
              const heading = paper ? sectionHeading(paper, section, si) : null;
              return (
                <div key={section.id} className="section">
                  {heading && <h2 className="section__title">{heading}</h2>}
                  {section.items.map((item, idx) => {
                    const entry = items.find((x) => x.item.id === item.id)!;
                    if (editing === item.id) {
                      return (
                        <ItemEditor
                          key={item.id}
                          item={item}
                          no={entry.no}
                          figureUrl={figureUrl}
                          onSave={(ops) => ctl.editOps(ops)}
                          onCancel={() => setEditing(null)}
                        />
                      );
                    }
                    return (
                      <ItemCard
                        key={item.id}
                        item={item}
                        no={entry.no}
                        teacher={teacher}
                        figureUrl={figureUrl}
                        reviewing={view.reviewing.includes(item.id)}
                        flash={item.id in view.changed}
                        canMoveUp={idx > 0}
                        canMoveDown={idx < section.items.length - 1}
                        onEdit={() => setEditing(item.id)}
                        onAsk={() => onAskItem(entry.no)}
                        onMove={(d) => void move(section.id, item.id, idx + d)}
                        onDelete={() => void remove(entry.no, item.id)}
                      />
                    );
                  })}
                </div>
              );
            })}

            {drafts.length > 0 && (
              <div className="section section--draft" data-testid="drafts">
                {paper && <h2 className="section__title">新的题目（正在核验与整理）</h2>}
                {drafts.map((it) => {
                  runningNo += 1;
                  return (
                    <ItemCard
                      key={it.id}
                      item={it}
                      no={runningNo}
                      teacher={teacher}
                      figureUrl={figureUrl}
                      draft
                    />
                  );
                })}
              </div>
            )}
            {active && (
              <div className="skeleton-card" aria-hidden>
                <div className="shimmer" style={{ height: 14, width: "40%" }} />
                <div className="shimmer" style={{ height: 14, width: "92%" }} />
                <div className="shimmer" style={{ height: 14, width: "70%" }} />
              </div>
            )}
          </div>
        )}
      </div>

      {exportOpen && sessionId && (
        <ExportDialog
          sessionId={sessionId}
          hasReview={(counts?.needs_review ?? 0) > 0}
          onClose={() => setExportOpen(false)}
          onDone={(name, warnings) => {
            setExportOpen(false);
            ctl.toast(`已导出：${name}`);
            for (const w of warnings) ctl.toast(w);
          }}
        />
      )}
      {historyOpen && sessionId && (
        <HistoryDrawer
          sessionId={sessionId}
          initialDiff={historyDiff}
          onRestore={(rev) => ctl.restore(rev)}
          onClose={() => setHistoryOpen(false)}
        />
      )}
    </section>
  );
}

function Skeleton() {
  return (
    <div className="ws__empty" aria-hidden data-testid="workspace-loading">
      <div className="skeleton-card">
        <div className="shimmer" style={{ height: 16, width: "30%" }} />
        <div className="shimmer" style={{ height: 14, width: "90%" }} />
        <div className="shimmer" style={{ height: 14, width: "75%" }} />
      </div>
      <div className="skeleton-card">
        <div className="shimmer" style={{ height: 16, width: "30%" }} />
        <div className="shimmer" style={{ height: 14, width: "85%" }} />
        <div className="shimmer" style={{ height: 14, width: "60%" }} />
      </div>
    </div>
  );
}

function EmptyWorkspace() {
  return (
    <div className="ws__empty ws__empty--text">
      <Icon name="file" size={32} />
      <p>试卷会出现在这里</p>
      <p className="muted">在左边说说您想出什么题，题目会一道一道出现。</p>
    </div>
  );
}

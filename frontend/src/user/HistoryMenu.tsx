import { useEffect, useRef, useState } from "react";
import { Icon } from "@/shared/ui/Icon";
import type { RecentSession } from "./controller";

interface Props {
  recent: RecentSession[];
  currentId: string | null;
  onOpen: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
}

/** 历史对话列表：点一行打开；每行可以改名、删除（删除要再点一次确认，因为不可恢复）。 */
export function HistoryMenu({ recent, currentId, onOpen, onRename, onDelete }: Props) {
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [confirming, setConfirming] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) input.current?.select();
  }, [editing]);

  function commit(r: RecentSession) {
    const t = draft.trim();
    setEditing(null);
    if (t && t !== r.title) onRename(r.id, t);
  }

  if (recent.length === 0) return <p className="menu__empty muted">还没有历史对话</p>;
  return (
    <>
      {recent.map((r) => {
        const on = r.id === currentId ? "menu__row--on" : "";
        if (editing === r.id) {
          return (
            <div key={r.id} className={`menu__row ${on}`}>
              <input
                ref={input}
                className="input menu__rename"
                value={draft}
                maxLength={60}
                aria-label="对话标题"
                onChange={(e) => setDraft(e.target.value)}
                onBlur={() => commit(r)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") commit(r);
                  if (e.key === "Escape") setEditing(null);
                }}
              />
            </div>
          );
        }
        if (confirming === r.id) {
          return (
            <div key={r.id} className={`menu__row menu__row--confirm ${on}`}>
              <span className="menu__ask">删除“{r.title || "新对话"}”？不可恢复。</span>
              <button
                type="button"
                className="btn btn--sm btn--danger-solid"
                onClick={() => {
                  setConfirming(null);
                  onDelete(r.id);
                }}
              >
                删除
              </button>
              <button type="button" className="btn btn--sm" onClick={() => setConfirming(null)}>
                取消
              </button>
            </div>
          );
        }
        return (
          <div key={r.id} className={`menu__row ${on}`}>
            <button
              type="button"
              role="menuitem"
              className="menu__item"
              onClick={() => onOpen(r.id)}
            >
              <span className="menu__title">{r.title || "新对话"}</span>
              <time className="muted">
                {new Date(r.ts).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" })}
              </time>
            </button>
            <span className="menu__actions">
              <button
                type="button"
                className="menu__act"
                aria-label={`重命名“${r.title || "新对话"}”`}
                title="重命名"
                onClick={() => {
                  setDraft(r.title);
                  setEditing(r.id);
                }}
              >
                <Icon name="edit" size={14} />
              </button>
              <button
                type="button"
                className="menu__act menu__act--danger"
                aria-label={`删除“${r.title || "新对话"}”`}
                title="删除"
                onClick={() => setConfirming(r.id)}
              >
                <Icon name="trash" size={14} />
              </button>
            </span>
          </div>
        );
      })}
    </>
  );
}

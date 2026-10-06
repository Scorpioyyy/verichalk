import { useMemo, useState } from "react";
import type { PerceivedItem, ReferenceSet } from "@/shared/api/types";
import { Rich } from "@/shared/math/Rich";
import { Icon } from "@/shared/ui/Icon";

const PREVIEW_N = 4;

function itemsOf(rs: ReferenceSet): PerceivedItem[] {
  return rs.pages.filter((p) => p.verdict === "worksheet").flatMap((p) => p.items);
}

/** 题面前的"题号"：同一大题只在第一道前显示。 */
function labelOf(items: PerceivedItem[], i: number): string {
  const it = items[i]!;
  const prev = items[i - 1];
  const same = prev && prev.no === it.no && prev.instruction === it.instruction;
  return !same && it.no ? it.no : "";
}

/** 照片识别结果卡片：读到了几道题、学生学到哪、每道题怎么读的（看不清的会标出来）。放在聊天里，随时可展开核对。 */
export function PerceptionCard({ refs }: { refs: ReferenceSet }) {
  const [open, setOpen] = useState(false);
  const items = useMemo(() => itemsOf(refs), [refs]);
  if (!refs.usable || items.length === 0) return null;
  const doubtful = items.filter((it) => it.uncertain).length;
  const shown = open ? items : items.slice(0, PREVIEW_N);
  const skipped = refs.pages.filter((p) => p.verdict !== "worksheet");
  const hints = refs.pages.flatMap((p) => (p.quality?.poor ? p.quality.hints : []));
  return (
    <section className="percept" aria-label="照片识别结果" data-testid="perception-card">
      <header className="percept__head">
        <span className="percept__icon" aria-hidden>
          <Icon name="image" size={16} />
        </span>
        <div>
          <p className="percept__title">
            读到了 <strong>{items.length}</strong> 道题
            {doubtful > 0 && <span className="percept__flag">{doubtful} 道需核对</span>}
          </p>
          {refs.context.summary && <p className="percept__sub">{refs.context.summary}</p>}
        </div>
      </header>
      <ol className="percept__list">
        {shown.map((it, i) => (
          <li key={it.id} className={`percept__item ${it.uncertain ? "percept__item--doubt" : ""}`}>
            <span className="percept__no">{labelOf(items, i)}</span>
            <span className="percept__text">
              <Rich text={it.text} inline />
              {it.has_figure && <span className="percept__tag">含图</span>}
              {it.uncertain && <span className="percept__note">{it.uncertain}</span>}
            </span>
          </li>
        ))}
      </ol>
      {items.length > PREVIEW_N && (
        <button type="button" className="percept__more" onClick={() => setOpen(!open)}>
          {open ? "收起" : `展开全部 ${items.length} 道`}
          <Icon name={open ? "up" : "down"} size={14} />
        </button>
      )}
      {(skipped.length > 0 || hints.length > 0) && (
        <p className="percept__warn">
          <Icon name="alert" size={14} />
          <span>
            {skipped.length > 0 && `有 ${skipped.length} 张图没能识别。`}
            {hints.length > 0 && [...new Set(hints)].join("；") + "。"}
          </span>
        </p>
      )}
    </section>
  );
}

interface Edit {
  text: string;
  remove: boolean;
}

/** 确认检查点：有字迹没看清（或照片画质一般）时请教师核对；只展示需要核对的题，其余可展开。 */
export function PerceptionConfirm({
  prompt,
  refs,
  onAnswer,
}: {
  prompt: string;
  refs: ReferenceSet;
  onAnswer: (answer: Record<string, unknown>) => void;
}) {
  const items = useMemo(() => itemsOf(refs), [refs]);
  const [edits, setEdits] = useState<Record<string, Edit>>({});
  const flagged = items.filter((it) => it.uncertain);
  const [all, setAll] = useState(flagged.length === 0 && items.length <= 8);
  const rows = all ? items : flagged.length > 0 ? flagged : items.slice(0, 6);
  const get = (it: PerceivedItem): Edit => edits[it.id] ?? { text: it.text, remove: false };
  const patch = (id: string, p: Partial<Edit>, it: PerceivedItem) =>
    setEdits({ ...edits, [id]: { ...get(it), ...p } });
  const changed = items.flatMap<{ id: string; text?: string; remove?: boolean }>((it) => {
    const e = edits[it.id];
    if (!e) return [];
    if (e.remove) return [{ id: it.id, remove: true }];
    return e.text.trim() !== it.text ? [{ id: it.id, text: e.text.trim() }] : [];
  });
  const kept = items.filter((it) => !get(it).remove).length;
  return (
    <section
      className="cp percept-confirm"
      aria-label="核对识别结果"
      data-testid="checkpoint-perception"
    >
      <p className="cp__prompt">{prompt}</p>
      <ul className="pc-list">
        {rows.map((it) => {
          const e = get(it);
          return (
            <li
              key={it.id}
              className={`pc-row ${it.uncertain ? "pc-row--doubt" : ""} ${e.remove ? "pc-row--gone" : ""}`}
            >
              <div className="pc-row__main">
                <input
                  className="input"
                  value={e.text}
                  disabled={e.remove}
                  aria-label={`第 ${it.no || "?"} 题的题面`}
                  onChange={(ev) => patch(it.id, { text: ev.target.value }, it)}
                />
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  aria-label={e.remove ? "恢复这道题" : "去掉这道题"}
                  title={e.remove ? "恢复" : "去掉：不作为参考"}
                  onClick={() => patch(it.id, { remove: !e.remove }, it)}
                >
                  <Icon name={e.remove ? "undo" : "trash"} size={15} />
                </button>
              </div>
              {it.uncertain && <p className="pc-row__note">{it.uncertain}</p>}
            </li>
          );
        })}
      </ul>
      {!all && items.length > rows.length && (
        <button type="button" className="percept__more" onClick={() => setAll(true)}>
          查看并修改全部 {items.length} 道 <Icon name="down" size={14} />
        </button>
      )}
      <div className="cp__actions">
        <button
          type="button"
          className="btn btn--primary"
          disabled={kept === 0}
          onClick={() => onAnswer({ option: "confirm", items: changed })}
        >
          {changed.length > 0 ? "按修改后的开始出题" : "没问题，开始出题"}
        </button>
        {kept === 0 && <span className="muted">至少保留一道题</span>}
      </div>
    </section>
  );
}

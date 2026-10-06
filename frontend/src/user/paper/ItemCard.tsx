import { useState } from "react";
import type { Item } from "@/shared/api/types";
import {
  CHECK_STATUS_LABEL,
  DIFFICULTY_LABEL,
  KIND_LABEL,
  TIER_LABEL,
  checkLabel,
  fmtScore,
} from "@/shared/labels";
import { Rich } from "@/shared/math/Rich";
import { Icon } from "@/shared/ui/Icon";
import { StatusBadge } from "@/shared/ui/StatusBadge";
import { optionLetter } from "./model";
import { useKpNames } from "./useKpNames";

interface Props {
  item: Item;
  no: number;
  teacher: boolean;
  figureUrl: (id: string) => string;
  /** 后台正在核验 */
  reviewing?: boolean;
  /** 刚被修改：短暂高亮 */
  flash?: boolean;
  /** 草稿（逐题送达、整份试卷还没装配好）：不能编辑 */
  draft?: boolean;
  canMoveUp?: boolean;
  canMoveDown?: boolean;
  onEdit?: () => void;
  onAsk?: () => void;
  onMove?: (delta: -1 | 1) => void;
  onDelete?: () => void;
}

export function DifficultyDots({ value }: { value: number }) {
  return (
    <span className="dots" role="img" aria-label={`难度：${DIFFICULTY_LABEL[value] ?? value}`}>
      {[1, 2, 3, 4, 5].map((i) => (
        <i key={i} className={i <= value ? "on" : ""} />
      ))}
    </span>
  );
}

export function ItemCard(p: Props) {
  const { item, no, teacher } = p;
  const [open, setOpen] = useState(false);
  const [showSolution, setShowSolution] = useState(false);
  const kpNames = useKpNames(item.kp_ids);
  const status = item.verification.status;
  const issues = item.verification.checks.filter((c) => c.status === "warn" || c.status === "fail");
  const letter = item.kind === "choice" ? item.answer.trim().toUpperCase() : "";
  const answerOption =
    letter.length === 1 && letter >= "A" ? item.options[letter.charCodeAt(0) - 65] : undefined;

  return (
    <article
      className={`item ${p.flash ? "item--flash" : ""} ${p.draft ? "item--draft" : ""}`}
      data-testid="item"
      data-item-id={item.id}
      data-status={status}
      aria-label={`第 ${no} 题`}
    >
      <header className="item__head">
        <span className="item__no">{no}</span>
        <span className="item__kind">{KIND_LABEL[item.kind]}</span>
        <span className="item__meta">
          <DifficultyDots value={item.difficulty} />
          {item.score !== null && item.score !== undefined && (
            <span>{fmtScore(item.score)} 分</span>
          )}
          {item.tier === "integrated" && <span>{TIER_LABEL[item.tier]}</span>}
        </span>
        <span className="item__spacer" />
        <StatusBadge
          status={status}
          busy={p.reviewing}
          onClick={item.verification.checks.length ? () => setOpen((o) => !o) : undefined}
          expanded={open}
        />
        {!p.draft && (
          <span className="item__tools" role="group" aria-label={`第 ${no} 题的操作`}>
            <button
              type="button"
              className="btn btn--ghost btn--icon btn--sm"
              onClick={p.onEdit}
              aria-label="编辑"
              title="编辑"
            >
              <Icon name="edit" size={16} />
            </button>
            <button
              type="button"
              className="btn btn--ghost btn--icon btn--sm"
              onClick={p.onAsk}
              aria-label="让助手修改"
              title="让助手修改"
            >
              <Icon name="sparkle" size={16} />
            </button>
            <button
              type="button"
              className="btn btn--ghost btn--icon btn--sm"
              onClick={() => p.onMove?.(-1)}
              disabled={!p.canMoveUp}
              aria-label="上移"
              title="上移"
            >
              <Icon name="arrowUp" size={16} />
            </button>
            <button
              type="button"
              className="btn btn--ghost btn--icon btn--sm"
              onClick={() => p.onMove?.(1)}
              disabled={!p.canMoveDown}
              aria-label="下移"
              title="下移"
            >
              <Icon name="arrowDown" size={16} />
            </button>
            <button
              type="button"
              className="btn btn--ghost btn--icon btn--sm btn--danger"
              onClick={p.onDelete}
              aria-label="删除"
              title="删除"
            >
              <Icon name="trash" size={16} />
            </button>
          </span>
        )}
      </header>

      {open && (
        <ul className="item__checks" aria-label="核验明细">
          {item.verification.checks.map((c) => (
            <li key={c.name} data-status={c.status}>
              <span className={`dot dot--${c.status}`} aria-hidden />
              <span className="item__check-name">{checkLabel(c.name)}</span>
              <span className="item__check-status">{CHECK_STATUS_LABEL[c.status]}</span>
              {(c.status === "warn" || c.status === "fail") && c.detail && (
                <span className="item__check-detail">{c.detail}</span>
              )}
            </li>
          ))}
        </ul>
      )}

      <Rich className="item__stem" text={item.stem} figureUrl={p.figureUrl} />
      {item.options.length > 0 && (
        <ol className="item__opts" type="A">
          {item.options.map((o, i) => (
            <li key={i} data-letter={optionLetter(i)}>
              <span className="item__opt-letter">{optionLetter(i)}.</span>
              <Rich text={o} inline />
            </li>
          ))}
        </ol>
      )}

      {status === "needs_review" && issues.length > 0 && !open && (
        <p className="notice notice--warn">
          <Icon name="alert" size={16} />
          <span>
            需要您确认：{issues[0]!.detail || `${checkLabel(issues[0]!.name)}有疑点`}
            {issues.length > 1 && `（另有 ${issues.length - 1} 项）`}
          </span>
        </p>
      )}

      {teacher && (
        <div className="item__answer">
          <p>
            <strong>答案</strong>
            <span className="item__answer-text">
              {item.answer ? <Rich text={item.answer} inline /> : "（未填写）"}
              {answerOption !== undefined && (
                <span className="muted">
                  {" "}
                  = <Rich text={answerOption} inline />
                </span>
              )}
            </span>
          </p>
          {item.solution && (
            <>
              <button
                type="button"
                className="link-btn"
                onClick={() => setShowSolution((s) => !s)}
                aria-expanded={showSolution}
              >
                {showSolution ? "收起解析" : "查看解析"}
              </button>
              {showSolution && (
                <Rich className="item__solution" text={item.solution} figureUrl={p.figureUrl} />
              )}
            </>
          )}
        </div>
      )}

      {kpNames.length > 0 && (
        <footer className="item__foot">
          <span className="muted">涉及：{kpNames.join("、")}</span>
        </footer>
      )}
    </article>
  );
}

import { STATUS_VIEW } from "../labels";
import type { VerifyStatus } from "../api/types";
import { Icon } from "./Icon";
import type { IconName } from "./Icon";

const ICON: Record<VerifyStatus, IconName> = {
  verified: "shield",
  checked: "check",
  needs_review: "alert",
  pending: "clock",
  rejected: "x",
};

const TONE_CLASS = {
  ok: "badge--ok",
  info: "badge--info",
  warn: "badge--warn",
  bad: "badge--bad",
  neutral: "",
};

interface Props {
  status: VerifyStatus;
  /** 后台正在核验（手改之后）：显示转圈而不是静止的"待核验" */
  busy?: boolean;
  onClick?: () => void;
  expanded?: boolean;
}

/** 核验状态徽标：状态靠文字 + 图标表达，不只靠颜色。可点击展开核验明细。 */
export function StatusBadge({ status, busy, onClick, expanded }: Props) {
  const v = STATUS_VIEW[status];
  const inner = (
    <>
      {busy ? <span className="spinner" aria-hidden /> : <Icon name={ICON[status]} size={14} />}
      {busy ? "正在核验" : v.label}
    </>
  );
  const cls = `badge ${TONE_CLASS[v.tone]}`;
  if (!onClick) {
    return (
      <span className={cls} title={v.hint}>
        {inner}
      </span>
    );
  }
  return (
    <button type="button" className={cls} title={v.hint} onClick={onClick} aria-expanded={expanded}>
      {inner}
      <Icon name={expanded ? "up" : "down"} size={13} />
    </button>
  );
}

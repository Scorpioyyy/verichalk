import { useEffect, useRef, useState } from "react";
import type { Chip, Understanding } from "@/shared/api/types";
import { DIFFICULTY_LABEL, TIER_LABEL } from "@/shared/labels";
import { Icon } from "@/shared/ui/Icon";

interface Props {
  understanding: Understanding;
  disabled?: boolean;
  /** 直接发出一轮"调整并重出"的话 */
  onApply: (text: string) => void;
  /** 其余假设：把一句话放进输入框，让教师补全 */
  onPrefill: (text: string) => void;
}

const ORIGIN_HINT = {
  user: "您说的",
  inferred: "我根据上下文推断的，点击可调整",
  default: "我按常规做的假设，点击可调整",
} as const;

/** "本次假设"：把系统的默认与推断亮出来，教师一键改并重出（PRD FR-3）。 */
export function ChipsBar({ understanding, disabled, onApply, onPrefill }: Props) {
  const [openKey, setOpenKey] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);
  // 窄屏默认收起（节省纵向空间）；桌面宽度下始终展开，这个状态不起作用
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!openKey) return;
    const onDoc = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpenKey(null);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpenKey(null);
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [openKey]);

  if (understanding.chips.length === 0) return null;
  return (
    <div className="chips" ref={root} aria-label="本次假设" data-testid="chips">
      <span className="chips__title">本次假设</span>
      <button
        type="button"
        className="chips__toggle"
        aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}
      >
        本次假设（{understanding.chips.length}）
        <Icon name={expanded ? "up" : "down"} size={14} />
      </button>
      <div className={`chips__list ${expanded ? "chips__list--open" : ""}`}>
        {understanding.chips.map((c) => (
          <div key={c.key} className="chip-wrap">
            <button
              type="button"
              className={`chip chip--${c.origin}`}
              aria-expanded={openKey === c.key}
              aria-haspopup="true"
              title={ORIGIN_HINT[c.origin]}
              disabled={disabled}
              onClick={() => setOpenKey(openKey === c.key ? null : c.key)}
            >
              <span className="chip__label">{c.label}</span>
              <span className="chip__value">{c.value}</span>
            </button>
            {openKey === c.key && (
              <ChipEditor
                chip={c}
                onApply={(t) => {
                  setOpenKey(null);
                  onApply(t);
                }}
                onPrefill={(t) => {
                  setOpenKey(null);
                  onPrefill(t);
                }}
              />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function ChipEditor({
  chip,
  onApply,
  onPrefill,
}: {
  chip: Chip;
  onApply: (t: string) => void;
  onPrefill: (t: string) => void;
}) {
  const initialCount = Number(/\d+/.exec(chip.value)?.[0] ?? 5);
  const [count, setCount] = useState(initialCount);

  if (chip.key === "count") {
    return (
      <div className="chip-pop" role="dialog" aria-label="调整题量">
        <div className="stepper">
          <button
            type="button"
            className="btn btn--icon"
            onClick={() => setCount(Math.max(1, count - 1))}
            aria-label="减一道"
          >
            −
          </button>
          <output aria-live="polite">{count} 道</output>
          <button
            type="button"
            className="btn btn--icon"
            onClick={() => setCount(Math.min(30, count + 1))}
            aria-label="加一道"
          >
            +
          </button>
        </div>
        <button
          type="button"
          className="btn btn--primary btn--sm"
          onClick={() => onApply(`题量改成 ${count} 道，重新出`)}
        >
          按此重出
        </button>
      </div>
    );
  }
  if (chip.key === "difficulty") {
    return (
      <div className="chip-pop" role="dialog" aria-label="调整难度">
        <div className="chip-pop__list">
          {[1, 2, 3, 4, 5].map((d) => (
            <button
              key={d}
              type="button"
              className="chip-btn"
              onClick={() => onApply(`难度改成${DIFFICULTY_LABEL[d]}，重新出`)}
            >
              {d} · {DIFFICULTY_LABEL[d]}
            </button>
          ))}
        </div>
      </div>
    );
  }
  if (chip.key === "tier") {
    return (
      <div className="chip-pop" role="dialog" aria-label="调整题目类型">
        <div className="chip-pop__list">
          {(Object.keys(TIER_LABEL) as (keyof typeof TIER_LABEL)[]).map((t) => (
            <button
              key={t}
              type="button"
              className="chip-btn"
              onClick={() => onApply(`改成${TIER_LABEL[t]}题，重新出`)}
            >
              {TIER_LABEL[t]}
            </button>
          ))}
        </div>
      </div>
    );
  }
  return (
    <div className="chip-pop" role="dialog" aria-label={`调整${chip.label}`}>
      <p className="muted">想把“{chip.label}”改成什么？</p>
      <button type="button" className="btn btn--sm" onClick={() => onPrefill(`${chip.label}改成`)}>
        在输入框里告诉我
      </button>
    </div>
  );
}

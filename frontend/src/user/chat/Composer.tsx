import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import { Icon } from "@/shared/ui/Icon";

export interface ComposerHandle {
  focus: () => void;
}

interface Props {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  onStop?: () => void;
  busy?: boolean;
  large?: boolean;
  placeholder?: string;
  suggestions?: string[];
  onSuggest?: (s: string) => void;
}

/** 输入框：Enter 发送、Shift+Enter 换行（输入法组字时不发送）；随内容长高；任务进行中把"发送"换成"停止"。 */
export const Composer = forwardRef<ComposerHandle, Props>(function Composer(
  { value, onChange, onSend, onStop, busy, large, placeholder, suggestions, onSuggest },
  ref,
) {
  const ta = useRef<HTMLTextAreaElement>(null);
  useImperativeHandle(ref, () => ({ focus: () => ta.current?.focus() }));

  useEffect(() => {
    const el = ta.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, large ? 220 : 160)}px`;
  }, [value, large]);

  const canSend = value.trim().length > 0 && !busy;
  return (
    <div className={`composer ${large ? "composer--large" : ""}`}>
      {suggestions && suggestions.length > 0 && !value && !busy && (
        <div className="composer__suggest" aria-label="可以这样说">
          {suggestions.map((s) => (
            <button key={s} type="button" className="chip-btn" onClick={() => onSuggest?.(s)}>
              {s}
            </button>
          ))}
        </div>
      )}
      <div className="composer__box">
        <textarea
          ref={ta}
          className="composer__input"
          rows={large ? 2 : 1}
          value={value}
          placeholder={placeholder ?? "告诉我您想出什么题，或想怎么改……"}
          aria-label="输入您的需求"
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              if (canSend) onSend();
            }
          }}
        />
        {busy ? (
          <button
            type="button"
            className="composer__btn composer__btn--stop"
            onClick={onStop}
            aria-label="停止"
          >
            <Icon name="stop" size={16} />
          </button>
        ) : (
          <button
            type="button"
            className="composer__btn"
            onClick={onSend}
            disabled={!canSend}
            aria-label="发送"
          >
            <Icon name="send" size={18} />
          </button>
        )}
      </div>
    </div>
  );
});

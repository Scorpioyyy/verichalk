import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { Icon } from "@/shared/ui/Icon";
import { MAX_PHOTOS, PHOTO_TYPES, imagesFrom } from "./photos";

export interface ComposerHandle {
  focus: () => void;
  openPicker: () => void;
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
  /** 待发送的照片；不传表示这个输入框不支持上传 */
  photos?: File[];
  onAddPhotos?: (files: File[]) => void;
  onRemovePhoto?: (index: number) => void;
}

/** 输入框：Enter 发送、Shift+Enter 换行（输入法组字时不发送）；随内容长高；任务进行中把"发送"换成"停止"。
 *  支持拍照上传：点按钮选图、把图拖进来、或直接粘贴截图；照片显示为缩略图，可逐张移除。 */
export const Composer = forwardRef<ComposerHandle, Props>(function Composer(
  {
    value,
    onChange,
    onSend,
    onStop,
    busy,
    large,
    placeholder,
    suggestions,
    onSuggest,
    photos,
    onAddPhotos,
    onRemovePhoto,
  },
  ref,
) {
  const ta = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const canUpload = !!onAddPhotos;
  const list = useMemo(() => photos ?? [], [photos]);
  useImperativeHandle(ref, () => ({
    focus: () => ta.current?.focus(),
    openPicker: () => picker.current?.click(),
  }));

  useEffect(() => {
    const el = ta.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, large ? 220 : 160)}px`;
  }, [value, large]);

  const urls = useMemo(() => list.map((f) => URL.createObjectURL(f)), [list]);
  useEffect(() => () => urls.forEach((u) => URL.revokeObjectURL(u)), [urls]);

  const canSend = (value.trim().length > 0 || list.length > 0) && !busy;
  const hint =
    placeholder ??
    (list.length > 0
      ? "可以补充一句要求，比如：照这页再出 5 道，再来 2 道拔高的"
      : "告诉我您想出什么题，或想怎么改……");

  function take(files: File[]) {
    if (canUpload && files.length > 0) onAddPhotos?.(files);
  }

  return (
    <div
      className={`composer ${large ? "composer--large" : ""} ${dragging ? "composer--drop" : ""}`}
      onDragOver={(e) => {
        if (!canUpload || busy) return;
        if (Array.from(e.dataTransfer.types).includes("Files")) {
          e.preventDefault();
          setDragging(true);
        }
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragging(false);
      }}
      onDrop={(e) => {
        setDragging(false);
        if (!canUpload || busy) return;
        const files = Array.from(e.dataTransfer.files);
        if (files.length > 0) {
          e.preventDefault();
          take(files);
        }
      }}
    >
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
        {list.length > 0 && (
          <ul className="composer__photos" aria-label="待发送的照片">
            {list.map((f, i) => (
              <li key={`${f.name}-${f.size}-${i}`} className="photo-chip">
                <img src={urls[i]} alt={`第 ${i + 1} 张照片：${f.name}`} />
                <button
                  type="button"
                  className="photo-chip__x"
                  aria-label={`移除第 ${i + 1} 张照片`}
                  onClick={() => onRemovePhoto?.(i)}
                >
                  <Icon name="x" size={12} />
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="composer__row">
          {canUpload && (
            <>
              <input
                ref={picker}
                type="file"
                accept={PHOTO_TYPES.join(",")}
                multiple
                hidden
                data-testid="photo-input"
                onChange={(e) => {
                  take(Array.from(e.target.files ?? [])); // 非图片由 acceptPhotos 说明原因
                  e.target.value = ""; // 允许再选同一张
                }}
              />
              <button
                type="button"
                className="composer__attach"
                onClick={() => picker.current?.click()}
                disabled={busy || list.length >= MAX_PHOTOS}
                aria-label="上传练习册照片"
                title="上传练习册 / 作业 / 试卷的照片"
              >
                <Icon name="image" size={18} />
              </button>
            </>
          )}
          <textarea
            ref={ta}
            className="composer__input"
            rows={large ? 2 : 1}
            value={value}
            placeholder={hint}
            aria-label="输入您的需求"
            onChange={(e) => onChange(e.target.value)}
            onPaste={(e) => {
              const files = imagesFrom(e.clipboardData.files);
              if (canUpload && files.length > 0) {
                e.preventDefault();
                take(files);
              }
            }}
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
      {dragging && <div className="composer__dropnote">松开即可上传照片</div>}
    </div>
  );
});

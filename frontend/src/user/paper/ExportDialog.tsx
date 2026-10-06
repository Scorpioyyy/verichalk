import { useEffect, useRef, useState } from "react";
import { api } from "@/shared/api/client";
import type { ExportFormat, ExportOptions } from "@/shared/api/types";
import { Icon } from "@/shared/ui/Icon";
import { userMessageOf } from "../controller";

const LS_KEY = "verichalk.exportOptions";

export const DEFAULT_EXPORT: ExportOptions = {
  format: "pdf",
  version: "student",
  include_solution: true,
  answers: "appendix",
  mark_review: true,
  header: { school: "", class_name: "", date: "", duration_minutes: null, name_line: true },
};

export function loadExportOptions(): ExportOptions {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (raw) {
      const o = JSON.parse(raw) as Partial<ExportOptions>;
      return { ...DEFAULT_EXPORT, ...o, header: { ...DEFAULT_EXPORT.header, ...o.header } };
    }
  } catch {
    /* 用默认值 */
  }
  return DEFAULT_EXPORT;
}

const FORMATS: { id: ExportFormat; name: string; hint: string }[] = [
  { id: "pdf", name: "PDF", hint: "直接打印、发给家长" },
  { id: "docx", name: "Word", hint: "公式可继续编辑" },
  { id: "md", name: "Markdown", hint: "纯文本，便于粘贴" },
  { id: "tex", name: "LaTeX", hint: "排版源码" },
];

interface Props {
  sessionId: string;
  hasReview: boolean;
  onClose: () => void;
  onDone: (filename: string, warnings: string[]) => void;
}

/** 导出对话框：选格式与版本，必要时填页眉；记住上次的选择。 */
export function ExportDialog({ sessionId, hasReview, onClose, onDone }: Props) {
  const [opts, setOpts] = useState<ExportOptions>(loadExportOptions);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const first = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    first.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !busy && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  const set = (p: Partial<ExportOptions>) => setOpts((o) => ({ ...o, ...p }));
  const setHeader = (p: Partial<ExportOptions["header"]>) =>
    setOpts((o) => ({ ...o, header: { ...o.header, ...p } }));
  const teacher = opts.version === "teacher";

  async function run() {
    setBusy(true);
    setError("");
    try {
      const file = await api.exportPaper(sessionId, opts);
      try {
        localStorage.setItem(LS_KEY, JSON.stringify(opts));
      } catch {
        /* 记不住也不影响导出 */
      }
      const url = URL.createObjectURL(file.blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = file.filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
      onDone(file.filename, file.warnings);
    } catch (e) {
      setError(userMessageOf(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="modal"
      role="presentation"
      onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}
    >
      <div
        className="modal__panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="export-title"
        data-testid="export-dialog"
      >
        <header className="modal__head">
          <h2 id="export-title">导出试卷</h2>
          <button
            type="button"
            className="btn btn--ghost btn--icon"
            onClick={onClose}
            aria-label="关闭"
            disabled={busy}
          >
            <Icon name="x" />
          </button>
        </header>

        <div className="modal__body">
          <fieldset className="opt-group">
            <legend>格式</legend>
            <div className="opt-cards">
              {FORMATS.map((f, i) => (
                <button
                  key={f.id}
                  ref={i === 0 ? first : undefined}
                  type="button"
                  className="opt-card"
                  aria-pressed={opts.format === f.id}
                  onClick={() => set({ format: f.id })}
                >
                  <strong>{f.name}</strong>
                  <span>{f.hint}</span>
                </button>
              ))}
            </div>
          </fieldset>

          <fieldset className="opt-group">
            <legend>版本</legend>
            <div className="opt-cards opt-cards--2">
              <button
                type="button"
                className="opt-card"
                aria-pressed={!teacher}
                onClick={() => set({ version: "student" })}
              >
                <strong>学生版</strong>
                <span>空白试卷，不含答案，留出作答位置</span>
              </button>
              <button
                type="button"
                className="opt-card"
                aria-pressed={teacher}
                onClick={() => set({ version: "teacher" })}
              >
                <strong>教师版</strong>
                <span>含答案，可选附解析</span>
              </button>
            </div>
          </fieldset>

          {teacher && (
            <div className="opt-group opt-inline">
              <label className="check">
                <input
                  type="checkbox"
                  checked={opts.include_solution}
                  onChange={(e) => set({ include_solution: e.target.checked })}
                />
                附解析
              </label>
              <label className="check">
                <input
                  type="checkbox"
                  checked={opts.answers === "inline"}
                  onChange={(e) => set({ answers: e.target.checked ? "inline" : "appendix" })}
                />
                答案紧跟在每道题后面
              </label>
              {hasReview && (
                <label className="check">
                  <input
                    type="checkbox"
                    checked={opts.mark_review}
                    onChange={(e) => set({ mark_review: e.target.checked })}
                  />
                  标出“需复核”的题
                </label>
              )}
            </div>
          )}

          <details className="opt-group opt-details">
            <summary>试卷页眉（可选）</summary>
            <div className="opt-header">
              <div className="field">
                <label className="field__label" htmlFor="h-school">
                  学校
                </label>
                <input
                  id="h-school"
                  className="input"
                  value={opts.header.school}
                  onChange={(e) => setHeader({ school: e.target.value })}
                />
              </div>
              <div className="field">
                <label className="field__label" htmlFor="h-class">
                  班级
                </label>
                <input
                  id="h-class"
                  className="input"
                  value={opts.header.class_name}
                  onChange={(e) => setHeader({ class_name: e.target.value })}
                />
              </div>
              <div className="field">
                <label className="field__label" htmlFor="h-date">
                  日期
                </label>
                <input
                  id="h-date"
                  className="input"
                  value={opts.header.date}
                  placeholder="如 2026 年 10 月 8 日"
                  onChange={(e) => setHeader({ date: e.target.value })}
                />
              </div>
              <div className="field">
                <label className="field__label" htmlFor="h-min">
                  用时（分钟）
                </label>
                <input
                  id="h-min"
                  className="input"
                  inputMode="numeric"
                  value={opts.header.duration_minutes ?? ""}
                  onChange={(e) =>
                    setHeader({
                      duration_minutes:
                        e.target.value.trim() === "" ? null : Number(e.target.value) || null,
                    })
                  }
                />
              </div>
              {!teacher && (
                <label className="check opt-header__wide">
                  <input
                    type="checkbox"
                    checked={opts.header.name_line}
                    onChange={(e) => setHeader({ name_line: e.target.checked })}
                  />
                  显示“姓名 / 班级 / 得分”填写线
                </label>
              )}
            </div>
          </details>

          {error && (
            <p className="notice notice--bad" role="alert">
              <Icon name="alert" size={16} />
              <span>{error}</span>
            </p>
          )}
        </div>

        <footer className="modal__foot">
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            取消
          </button>
          <button
            type="button"
            className="btn btn--primary"
            onClick={() => void run()}
            disabled={busy}
            data-testid="export-run"
          >
            {busy ? <span className="spinner" aria-hidden /> : <Icon name="download" size={16} />}
            {busy ? "正在生成……" : "导出"}
          </button>
        </footer>
      </div>
    </div>
  );
}

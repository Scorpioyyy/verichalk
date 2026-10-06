import { useEffect, useState } from "react";
import { api } from "@/shared/api/client";
import type { RootCause, Severity } from "@/shared/api/types";
import { Icon } from "@/shared/ui/Icon";

/** 根因类别（docs/evaluation.md §6）：决定修在哪一层，所以每一项都写清"修哪里"。 */
export const ROOT_CAUSES: { id: RootCause; name: string; where: string }[] = [
  { id: "U", name: "理解", where: "误解 / 漏字段 / 该问没问 / 过度澄清 → understand 提示与评测集" },
  { id: "P", name: "感知", where: "转写错 / 映射错 / 把手写当题面 → perceive、预处理" },
  { id: "R", name: "规划", where: "范围错 / 组合不自然 / 覆盖不全 → knowledge、ComboMiner、plan" },
  {
    id: "W",
    name: "生成",
    where: "歧义 / 数据荒谬 / 不新颖 / 难度偏离 → produce.write 提示与上下文",
  },
  { id: "V", name: "核验", where: "漏检（坏题放行）/ 误杀（好题被拒）→ verify 检查本身" },
  { id: "B", name: "边界", where: "特征抽取漏项 / 边界库宽松 → 特征抽取；上游反馈到 chalkbase" },
  { id: "E", name: "编辑", where: "没改到 / 附带破坏 → edit" },
  { id: "X", name: "导出", where: "公式 / 版面 → render" },
  { id: "S", name: "系统", where: "超时 / 限流 / 崩溃 / 缓存失效 → 网关、编排" },
  { id: "K", name: "知识库", where: "数据问题 → 向 chalkbase 提 issue" },
  { id: "UX", name: "交互", where: "交互 / 文案 / 进度提示 → 前端" },
];

const SEVERITY: { id: Severity; label: string; hint: string }[] = [
  { id: "S1", label: "S1 致命", hint: "答案错 / 超纲 / 崩溃" },
  { id: "S2", label: "S2 明显", hint: "不符需求、体验差" },
  { id: "S3", label: "S3 轻微", hint: "" },
];

interface Props {
  runId: string;
  input: string;
  itemId: string | null;
  stages: string[];
  onClose: () => void;
  onSaved: (id: string) => void;
}

export function BadcaseDialog({ runId, input, itemId, stages, onClose, onSaved }: Props) {
  const [cause, setCause] = useState<RootCause | "">("");
  const [severity, setSeverity] = useState<Severity>("S2");
  const [stage, setStage] = useState("");
  const [problem, setProblem] = useState("");
  const [expected, setExpected] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !busy && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  async function save() {
    if (!cause || !problem.trim()) return;
    setBusy(true);
    setError("");
    try {
      const bc = await api.debug.addBadcase({
        run_id: runId,
        item_id: itemId,
        input,
        problem: problem.trim(),
        expected: expected.trim(),
        root_cause: cause,
        severity,
        stage,
      });
      onSaved(bc.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const picked = ROOT_CAUSES.find((c) => c.id === cause);
  return (
    <div
      className="modal"
      role="presentation"
      onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}
    >
      <div
        className="modal__panel dbg-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="bc-title"
        data-testid="badcase-dialog"
      >
        <header className="modal__head">
          <h2 id="bc-title">标记为 Badcase</h2>
          <button
            type="button"
            className="btn btn--ghost btn--icon"
            onClick={onClose}
            aria-label="关闭"
          >
            <Icon name="x" />
          </button>
        </header>
        <div className="modal__body">
          <p className="muted small">
            运行 <span className="mono">{runId}</span>
            {itemId && (
              <>
                {" "}
                · 题目 <span className="mono">{itemId.slice(-8)}</span>
              </>
            )}
          </p>
          <label className="field">
            <span className="field__label">观察到的问题（必填）</span>
            <textarea
              className="textarea"
              rows={3}
              value={problem}
              onChange={(e) => setProblem(e.target.value)}
              autoFocus
            />
          </label>
          <label className="field">
            <span className="field__label">期望的表现</span>
            <textarea
              className="textarea"
              rows={2}
              value={expected}
              onChange={(e) => setExpected(e.target.value)}
            />
          </label>
          <fieldset className="opt-group">
            <legend>根因类别（必选，决定修在哪一层）</legend>
            <div className="bc-causes">
              {ROOT_CAUSES.map((c) => (
                <button
                  key={c.id}
                  type="button"
                  className="chip-btn"
                  aria-pressed={cause === c.id}
                  data-on={cause === c.id}
                  onClick={() => setCause(c.id)}
                >
                  <b>{c.id}</b> {c.name}
                </button>
              ))}
            </div>
            {picked && <p className="muted small">{picked.where}</p>}
          </fieldset>
          <div className="bc-row">
            <fieldset className="opt-group">
              <legend>严重度</legend>
              <div className="seg">
                {SEVERITY.map((s) => (
                  <button
                    key={s.id}
                    type="button"
                    className="seg__btn"
                    aria-pressed={severity === s.id}
                    title={s.hint}
                    onClick={() => setSeverity(s.id)}
                  >
                    {s.label}
                  </button>
                ))}
              </div>
            </fieldset>
            <label className="field">
              <span className="field__label">涉及阶段</span>
              <select className="select" value={stage} onChange={(e) => setStage(e.target.value)}>
                <option value="">（不确定）</option>
                {stages.map((s) => (
                  <option key={s}>{s}</option>
                ))}
              </select>
            </label>
          </div>
          {error && <p className="notice notice--bad">{error}</p>}
        </div>
        <footer className="modal__foot">
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            取消
          </button>
          <button
            type="button"
            className="btn btn--primary"
            onClick={() => void save()}
            disabled={busy || !cause || !problem.trim()}
            data-testid="badcase-save"
          >
            入库
          </button>
        </footer>
      </div>
    </div>
  );
}

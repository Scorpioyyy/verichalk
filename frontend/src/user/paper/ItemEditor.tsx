import { useRef, useState } from "react";
import type { Item, Op } from "@/shared/api/types";
import { DIFFICULTY_LABEL, KIND_LABEL } from "@/shared/labels";
import { Rich } from "@/shared/math/Rich";
import { Icon } from "@/shared/ui/Icon";
import { optionLetter } from "./model";

interface Props {
  item: Item;
  no: number;
  figureUrl: (id: string) => string;
  onSave: (ops: Op[]) => Promise<boolean>;
  onCancel: () => void;
}

/** 常用符号：教师不必手写 TeX。点击在光标处插入。 */
const SNIPPETS: { label: string; text: string; cursorBack?: number; title: string }[] = [
  { label: "分数", text: "$\\frac{}{}$", cursorBack: 4, title: "分数" },
  { label: "×", text: "$\\times$", title: "乘号" },
  { label: "÷", text: "$\\div$", title: "除号" },
  { label: "°", text: "$^\\circ$", title: "度" },
  { label: "x²", text: "$^{2}$", title: "平方" },
  { label: "填空线", text: "____", title: "填空横线" },
];

type Field = "stem" | "answer" | "solution";

/** 就地编辑一道题：题干 / 选项 / 答案 / 解析 / 分值 / 难度，边改边看效果。保存时只提交真正改动的字段。 */
export function ItemEditor({ item, no, figureUrl, onSave, onCancel }: Props) {
  const [stem, setStem] = useState(item.stem);
  const [options, setOptions] = useState<string[]>(item.options);
  const [answer, setAnswer] = useState(item.answer);
  const [solution, setSolution] = useState(item.solution);
  const [score, setScore] = useState(
    item.score === null || item.score === undefined ? "" : String(item.score),
  );
  const [difficulty, setDifficulty] = useState(item.difficulty);
  const [saving, setSaving] = useState(false);
  const refs = {
    stem: useRef<HTMLTextAreaElement>(null),
    solution: useRef<HTMLTextAreaElement>(null),
  };
  const [focused, setFocused] = useState<Field>("stem");

  const setters: Record<Field, (v: string) => void> = {
    stem: setStem,
    answer: setAnswer,
    solution: setSolution,
  };
  const values: Record<Field, string> = { stem, answer, solution };

  function insert(text: string, back = 0) {
    const field = focused === "answer" ? "stem" : focused;
    const el = refs[field as "stem" | "solution"].current;
    const cur = values[field];
    const start = el?.selectionStart ?? cur.length;
    const end = el?.selectionEnd ?? cur.length;
    setters[field](cur.slice(0, start) + text + cur.slice(end));
    requestAnimationFrame(() => {
      if (!el) return;
      el.focus();
      const pos = start + text.length - back;
      el.setSelectionRange(pos, pos);
    });
  }

  const scoreNum = score.trim() === "" ? null : Number(score);
  const scoreBad = scoreNum !== null && (Number.isNaN(scoreNum) || scoreNum < 0 || scoreNum > 100);
  const stemEmpty = stem.trim() === "";

  function buildOps(): Op[] {
    const ops: Op[] = [];
    const rf = (field: string, value: unknown) =>
      ops.push({ op: "replace_field", item_id: item.id, field, value } as Op);
    if (stem !== item.stem) rf("stem", stem);
    if (JSON.stringify(options) !== JSON.stringify(item.options)) rf("options", options);
    if (answer !== item.answer) {
      rf("answer", answer);
      rf("answer_value", null); // 旧的精确答案已失效，由复核重新求解
    }
    if (solution !== item.solution) rf("solution", solution);
    if (scoreNum !== (item.score ?? null) && !scoreBad) rf("score", scoreNum);
    if (difficulty !== item.difficulty) rf("difficulty", difficulty);
    return ops;
  }

  const ops = buildOps();
  const dirty = ops.length > 0;

  async function save() {
    if (!dirty) return onCancel();
    setSaving(true);
    const ok = await onSave(ops);
    setSaving(false);
    if (ok) onCancel();
  }

  return (
    <article
      className="item item--editing"
      data-testid="item-editor"
      aria-label={`编辑第 ${no} 题`}
    >
      <header className="item__head">
        <span className="item__no">{no}</span>
        <span className="item__kind">{KIND_LABEL[item.kind]}</span>
        <span className="item__spacer" />
        <span className="muted">保存后会自动重新核验</span>
      </header>

      <div className="editor__tools" role="toolbar" aria-label="插入符号">
        <span className="muted">插入：</span>
        {SNIPPETS.map((s) => (
          <button
            key={s.label}
            type="button"
            className="chip-btn"
            title={s.title}
            onClick={() => insert(s.text, s.cursorBack)}
          >
            {s.label}
          </button>
        ))}
      </div>

      <div className="editor__grid">
        <div className="field">
          <label className="field__label" htmlFor={`stem-${item.id}`}>
            题干
          </label>
          <textarea
            id={`stem-${item.id}`}
            ref={refs.stem}
            className="textarea"
            rows={4}
            value={stem}
            onFocus={() => setFocused("stem")}
            onChange={(e) => setStem(e.target.value)}
          />
        </div>
        <div className="editor__preview" aria-label="效果预览">
          <span className="field__label">效果预览</span>
          {stemEmpty ? (
            <p className="muted">题干不能为空</p>
          ) : (
            <Rich text={stem} figureUrl={figureUrl} />
          )}
        </div>
      </div>

      {options.length > 0 && (
        <fieldset className="editor__opts">
          <legend className="field__label">选项</legend>
          {options.map((o, i) => (
            <div key={i} className="editor__opt">
              <span className="item__opt-letter">{optionLetter(i)}.</span>
              <input
                className="input"
                value={o}
                aria-label={`选项 ${optionLetter(i)}`}
                onChange={(e) => setOptions(options.map((x, j) => (j === i ? e.target.value : x)))}
              />
            </div>
          ))}
        </fieldset>
      )}

      <div className="editor__row">
        <div className="field editor__answer">
          <label className="field__label" htmlFor={`ans-${item.id}`}>
            答案{item.kind === "choice" ? "（填选项字母）" : ""}
          </label>
          <input
            id={`ans-${item.id}`}
            className="input"
            value={answer}
            onFocus={() => setFocused("answer")}
            onChange={(e) => setAnswer(e.target.value)}
          />
        </div>
        <div className="field editor__small">
          <label className="field__label" htmlFor={`score-${item.id}`}>
            分值
          </label>
          <input
            id={`score-${item.id}`}
            className="input"
            inputMode="decimal"
            value={score}
            aria-invalid={scoreBad}
            onChange={(e) => setScore(e.target.value)}
          />
        </div>
        <div className="field editor__small">
          <label className="field__label" htmlFor={`diff-${item.id}`}>
            难度
          </label>
          <select
            id={`diff-${item.id}`}
            className="select"
            value={difficulty}
            onChange={(e) => setDifficulty(Number(e.target.value))}
          >
            {[1, 2, 3, 4, 5].map((d) => (
              <option key={d} value={d}>
                {d} · {DIFFICULTY_LABEL[d]}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="field">
        <label className="field__label" htmlFor={`sol-${item.id}`}>
          解析
        </label>
        <textarea
          id={`sol-${item.id}`}
          ref={refs.solution}
          className="textarea"
          rows={3}
          value={solution}
          onFocus={() => setFocused("solution")}
          onChange={(e) => setSolution(e.target.value)}
        />
      </div>

      <div className="editor__actions">
        <button
          type="button"
          className="btn btn--primary"
          onClick={() => void save()}
          disabled={saving || stemEmpty || scoreBad || !dirty}
        >
          {saving ? <span className="spinner" /> : <Icon name="check" size={16} />} 保存
        </button>
        <button type="button" className="btn" onClick={onCancel} disabled={saving}>
          取消
        </button>
        {scoreBad && <span className="field-error">分值请填 0～100 的数字</span>}
      </div>
    </article>
  );
}

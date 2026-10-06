import { useState } from "react";
import type { CheckpointInfo } from "@/shared/events/reducer";
import { KIND_LABEL } from "@/shared/labels";
import type { ItemKind, ReferenceSet } from "@/shared/api/types";
import { Rich } from "@/shared/math/Rich";
import { optionLetter } from "../paper/model";
import { PerceptionConfirm } from "./PerceptionCards";

interface Props {
  checkpoint: CheckpointInfo;
  onAnswer: (answer: Record<string, unknown>) => void;
}

/** 检查点：运行暂停，等教师选择。四种形态——澄清（点选项）、蓝图（细目表）、样题（先看几道）、识别结果核对（照片）。 */
export function CheckpointCard({ checkpoint, onAnswer }: Props) {
  switch (checkpoint.kind) {
    case "blueprint":
      return <Blueprint cp={checkpoint} onAnswer={onAnswer} />;
    case "samples":
      return <Samples cp={checkpoint} onAnswer={onAnswer} />;
    case "perception":
      return (
        <PerceptionConfirm
          prompt={checkpoint.prompt}
          refs={checkpoint.payload.references as ReferenceSet}
          onAnswer={onAnswer}
        />
      );
    default:
      return <Clarify cp={checkpoint} onAnswer={onAnswer} />;
  }
}

function Clarify({ cp, onAnswer }: { cp: CheckpointInfo; onAnswer: Props["onAnswer"] }) {
  const [text, setText] = useState("");
  return (
    <section className="cp" aria-label="需要您确认" data-testid="checkpoint-clarify">
      <p className="cp__prompt">{cp.prompt}</p>
      <div className="cp__options">
        {cp.options.map((o) => (
          <button
            key={o.id}
            type="button"
            className="chip-btn chip-btn--lg"
            onClick={() => onAnswer({ option: o.id })}
          >
            {o.label}
          </button>
        ))}
        <button
          type="button"
          className="chip-btn chip-btn--lg chip-btn--quiet"
          onClick={() => onAnswer({ text: "你来定" })}
        >
          你来定
        </button>
      </div>
      <form
        className="cp__free"
        onSubmit={(e) => {
          e.preventDefault();
          if (text.trim()) onAnswer({ text: text.trim() });
        }}
      >
        <input
          className="input"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="或者直接告诉我……"
          aria-label="补充说明"
        />
        <button type="submit" className="btn" disabled={!text.trim()}>
          确定
        </button>
      </form>
    </section>
  );
}

interface TableRow {
  title: string;
  kind: string;
  count: number;
  score_each: number;
  subtotal: number;
}

function Blueprint({ cp, onAnswer }: { cp: CheckpointInfo; onAnswer: Props["onAnswer"] }) {
  const [adjust, setAdjust] = useState(false);
  const [text, setText] = useState("");
  const plan = (cp.payload.plan ?? {}) as {
    total_score?: number;
    duration_min?: number;
    notes?: string[];
  };
  const table = ((cp.payload.table ?? []) as TableRow[]).filter(
    (r) => r && typeof r.count === "number",
  );
  const total = table.reduce((a, r) => a + r.subtotal, 0);
  const count = table.reduce((a, r) => a + r.count, 0);
  // 提示语的第一行是问句，最后一行是怎么调整的说明；中间的细目表（"- 选择题：…"）由下面的表格代替，不重复显示
  const lines = cp.prompt
    .split("\n")
    .filter((l) => l.trim() && !l.startsWith("- ") && !l.startsWith("共 "));
  const intro = (lines[0] ?? "").replace(/[：:]\s*$/, "");
  const howTo = lines.slice(1).join(" ");
  return (
    <section className="cp" aria-label="确认细目表" data-testid="checkpoint-blueprint">
      <p className="cp__prompt">{intro}</p>
      <table className="plan">
        <thead>
          <tr>
            <th scope="col">题型</th>
            <th scope="col">题数</th>
            <th scope="col">每题</th>
            <th scope="col">小计</th>
          </tr>
        </thead>
        <tbody>
          {table.map((r) => (
            <tr key={r.title}>
              <th scope="row">{r.title}</th>
              <td>{r.count}</td>
              <td>{r.score_each} 分</td>
              <td>{r.subtotal} 分</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <th scope="row">合计</th>
            <td>{count}</td>
            <td />
            <td>{plan.total_score ?? total} 分</td>
          </tr>
        </tfoot>
      </table>
      <p className="cp__meta">
        建议用时 {plan.duration_min ?? 40} 分钟{howTo && `。${howTo}`}
      </p>
      {(plan.notes ?? []).map((n) => (
        <p key={n} className="cp__note">
          {n}
        </p>
      ))}
      {adjust ? (
        <form
          className="cp__free"
          onSubmit={(e) => {
            e.preventDefault();
            if (text.trim()) onAnswer({ option: "adjust", text: text.trim() });
          }}
        >
          <input
            className="input"
            autoFocus
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="比如：选择题多两道、满分改成 120"
            aria-label="怎么调整"
          />
          <button type="submit" className="btn btn--primary" disabled={!text.trim()}>
            调整
          </button>
        </form>
      ) : (
        <div className="cp__actions">
          <button
            type="button"
            className="btn btn--primary"
            onClick={() => onAnswer({ option: "confirm" })}
          >
            就这样，开始出题
          </button>
          <button type="button" className="btn" onClick={() => setAdjust(true)}>
            我想调整
          </button>
        </div>
      )}
    </section>
  );
}

interface SampleItem {
  id: string;
  kind: ItemKind;
  stem: string;
  options: string[];
  answer: string;
}

function Samples({ cp, onAnswer }: { cp: CheckpointInfo; onAnswer: Props["onAnswer"] }) {
  const items = (cp.payload.items ?? []) as SampleItem[];
  return (
    <section className="cp" aria-label="确认样题" data-testid="checkpoint-samples">
      <p className="cp__prompt">{cp.prompt}</p>
      <ol className="samples">
        {items.map((it, i) => (
          <li key={it.id} className="sample">
            <span className="sample__kind">
              {i + 1}. {KIND_LABEL[it.kind] ?? "题目"}
            </span>
            <Rich text={it.stem} />
            {it.options.length > 0 && (
              <ul className="sample__opts">
                {it.options.map((o, j) => (
                  <li key={j}>
                    {optionLetter(j)}. <Rich text={o} inline />
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ol>
      <div className="cp__actions">
        <button
          type="button"
          className="btn btn--primary"
          onClick={() => onAnswer({ option: "confirm" })}
        >
          合适，继续出全卷
        </button>
        <button type="button" className="btn" onClick={() => onAnswer({ option: "redo" })}>
          风格不对，重出样题
        </button>
      </div>
    </section>
  );
}

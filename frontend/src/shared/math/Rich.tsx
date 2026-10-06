import katex from "katex";
import "katex/dist/katex.min.css";
import { Fragment, memo } from "react";
import type { ReactNode } from "react";
import { parseBlocks, parseInline } from "./parse";
import type { Block, Inline } from "./parse";

const cache = new Map<string, { html: string; error: string | null }>();

/** KaTeX 渲染（带缓存）。`strict=true` 时失败会抛出，供"公式渲染"评测（K2）统计。 */
export function renderMath(src: string, display: boolean, strict = false) {
  const key = `${display ? "D" : "I"}\u0000${strict ? "S" : "L"}\u0000${src}`;
  const hit = cache.get(key);
  if (hit) return hit;
  let res: { html: string; error: string | null };
  try {
    res = {
      html: katex.renderToString(src, {
        displayMode: display,
        throwOnError: strict,
        strict: strict ? "warn" : "ignore",
        trust: false,
        output: "htmlAndMathml",
      }),
      error: null,
    };
  } catch (e) {
    res = { html: "", error: e instanceof Error ? e.message : String(e) };
  }
  if (cache.size > 2000) cache.clear();
  cache.set(key, res);
  return res;
}

export interface RichOptions {
  /** 把 `fig:<id>` 解析为图片地址；不给则显示占位 */
  figureUrl?: (id: string) => string;
}

function renderInline(nodes: Inline[], opts: RichOptions, keyPrefix = ""): ReactNode {
  return nodes.map((n, i) => {
    const key = `${keyPrefix}${i}`;
    switch (n.t) {
      case "text":
        return <Fragment key={key}>{n.v}</Fragment>;
      case "math": {
        const r = renderMath(n.src, n.display);
        if (r.error)
          return (
            <code key={key} className="math-fallback" title="公式无法显示">
              {n.src}
            </code>
          );
        return (
          <span
            key={key}
            className={n.display ? "math-display" : "math-inline"}
            dangerouslySetInnerHTML={{ __html: r.html }}
          />
        );
      }
      case "blank":
        return <span key={key} className="blank" role="img" aria-label="空格，填写答案" />;
      case "fig":
        return opts.figureUrl ? (
          <img key={key} className="fig" src={opts.figureUrl(n.id)} alt={n.alt || "题目附图"} />
        ) : (
          <span key={key} className="fig-missing">
            [图]
          </span>
        );
      case "strong":
        return <strong key={key}>{renderInline(n.c, opts, `${key}.`)}</strong>;
      case "em":
        return <em key={key}>{renderInline(n.c, opts, `${key}.`)}</em>;
      case "code":
        return <code key={key}>{n.v}</code>;
    }
  });
}

function renderBlock(b: Block, i: number, opts: RichOptions): ReactNode {
  switch (b.t) {
    case "p":
      return <p key={i}>{renderInline(b.c, opts)}</p>;
    case "ul":
      return (
        <ul key={i}>
          {b.items.map((it, j) => (
            <li key={j}>{renderInline(it, opts)}</li>
          ))}
        </ul>
      );
    case "ol":
      return (
        <ol key={i}>
          {b.items.map((it, j) => (
            <li key={j}>{renderInline(it, opts)}</li>
          ))}
        </ol>
      );
    case "table":
      return (
        <table key={i} className="rich-table">
          <thead>
            <tr>
              {b.head.map((c, j) => (
                <th key={j}>{renderInline(c, opts)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {b.rows.map((r, j) => (
              <tr key={j}>
                {r.map((c, k) => (
                  <td key={k}>{renderInline(c, opts)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      );
  }
}

interface RichProps extends RichOptions {
  text: string;
  /** 只渲染行内（选项、芯片等短文本）：不产生 <p> */
  inline?: boolean;
  className?: string;
}

/** 渲染题目文本：公式用 KaTeX，填空横线、插图、表格与试卷一致。 */
export const Rich = memo(function Rich({ text, inline, className, figureUrl }: RichProps) {
  const opts: RichOptions = { figureUrl };
  if (inline) {
    return <span className={className}>{renderInline(parseInline(text), opts)}</span>;
  }
  return (
    <div className={`rich ${className ?? ""}`}>
      {parseBlocks(text).map((b, i) => renderBlock(b, i, opts))}
    </div>
  );
});

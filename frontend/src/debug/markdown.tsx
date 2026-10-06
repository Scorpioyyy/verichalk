/** 分析助手回答用的极简 Markdown：标题、列表、代码块、表格、粗体、行内代码。输出 React 节点，不碰 innerHTML。 */
import type { ReactNode } from "react";

type Block =
  | { t: "h"; level: number; text: string }
  | { t: "p"; text: string }
  | { t: "ul" | "ol"; items: string[] }
  | { t: "code"; text: string }
  | { t: "table"; head: string[]; rows: string[][] };

const cells = (line: string) =>
  line
    .trim()
    .replace(/^\||\|$/g, "")
    .split("|")
    .map((c) => c.trim());

const isDivider = (line: string) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line);

export function parseBlocks(src: string): Block[] {
  const lines = src.replace(/\r\n/g, "\n").split("\n");
  const at = (n: number) => lines[n] ?? "";
  const out: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = at(i);
    if (!line.trim()) {
      i += 1;
      continue;
    }
    if (line.trim().startsWith("```")) {
      const body: string[] = [];
      i += 1;
      while (i < lines.length && !at(i).trim().startsWith("```")) body.push(at(i++));
      i += 1; // 结束围栏（流式输出时可能还没到，也不影响）
      out.push({ t: "code", text: body.join("\n") });
      continue;
    }
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) {
      out.push({ t: "h", level: (h[1] ?? "#").length, text: h[2] ?? "" });
      i += 1;
      continue;
    }
    if (line.includes("|") && i + 1 < lines.length && isDivider(at(i + 1))) {
      const head = cells(line);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && at(i).includes("|") && at(i).trim()) rows.push(cells(at(i++)));
      out.push({ t: "table", head, rows });
      continue;
    }
    const li = /^\s*(?:([-*•])|(\d+)[.)])\s+(.*)$/.exec(line);
    if (li) {
      const ordered = li[2] !== undefined;
      const items: string[] = [];
      while (i < lines.length) {
        const m = /^\s*(?:([-*•])|(\d+)[.)])\s+(.*)$/.exec(at(i));
        if (!m || (m[2] !== undefined) !== ordered) break;
        items.push(m[3] ?? "");
        i += 1;
      }
      out.push({ t: ordered ? "ol" : "ul", items });
      continue;
    }
    const para: string[] = [];
    while (
      i < lines.length &&
      at(i).trim() &&
      !at(i).trim().startsWith("```") &&
      !/^#{1,4}\s/.test(at(i)) &&
      !/^\s*(?:[-*•]|\d+[.)])\s+/.test(at(i))
    )
      para.push(at(i++));
    out.push({ t: "p", text: para.join("\n") });
  }
  return out;
}

export function inline(text: string): ReactNode[] {
  const parts: ReactNode[] = [];
  const re = /(`[^`\n]+`|\*\*[^*\n]+\*\*)/g;
  let last = 0;
  let k = 0;
  for (let m = re.exec(text); m; m = re.exec(text)) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    const tok = m[0];
    parts.push(
      tok.startsWith("`") ? (
        <code key={k++}>{tok.slice(1, -1)}</code>
      ) : (
        <strong key={k++}>{tok.slice(2, -2)}</strong>
      ),
    );
    last = m.index + tok.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

export function Markdown({ source }: { source: string }) {
  return (
    <div className="md">
      {parseBlocks(source).map((b, i) => {
        switch (b.t) {
          case "h":
            return (
              <p key={i} className={`md__h md__h--${Math.min(b.level, 3)}`}>
                {inline(b.text)}
              </p>
            );
          case "p":
            return <p key={i}>{inline(b.text)}</p>;
          case "ul":
          case "ol": {
            const List = b.t;
            return (
              <List key={i}>
                {b.items.map((it, j) => (
                  <li key={j}>{inline(it)}</li>
                ))}
              </List>
            );
          }
          case "code":
            return (
              <pre key={i} className="md__code">
                {b.text}
              </pre>
            );
          case "table":
            return (
              <div key={i} className="md__table">
                <table>
                  <thead>
                    <tr>
                      {b.head.map((c, j) => (
                        <th key={j}>{inline(c)}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {b.rows.map((r, j) => (
                      <tr key={j}>
                        {r.map((c, k) => (
                          <td key={k}>{inline(c)}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
        }
      })}
    </div>
  );
}

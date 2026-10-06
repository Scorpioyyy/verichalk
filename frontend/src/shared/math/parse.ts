/**
 * 题目文本（Pandoc Markdown + TeX 数学，D7）的解析：纯函数，输出一棵很小的语法树，供 React 渲染与测试。
 *
 * 只支持题目里真正出现的子集：段落、列表、竖线表格、行内 / 独立公式、填空横线、插图引用、粗体 / 斜体 / 行内代码。
 * 单个换行与 Pandoc 一致，视为空格；空行分段（预览与导出同口径，D42）。
 */

export type Inline =
  | { t: "text"; v: string }
  | { t: "math"; src: string; display: boolean }
  | { t: "blank" }
  | { t: "fig"; id: string; alt: string }
  | { t: "strong"; c: Inline[] }
  | { t: "em"; c: Inline[] }
  | { t: "code"; v: string };

export type Block =
  | { t: "p"; c: Inline[] }
  | { t: "ul"; items: Inline[][] }
  | { t: "ol"; items: Inline[][] }
  | { t: "table"; head: Inline[][]; rows: Inline[][][] };

const isSpace = (ch: string | undefined) => ch === undefined || /\s/.test(ch);

/** 找行内公式 `$…$` 的结尾：结尾 `$` 前不能是空白或反斜杠，后面不能紧跟数字（Pandoc 规则，避免把"$5 和 $6"当公式）。 */
function findInlineClose(s: string, from: number): number {
  for (let j = from; j < s.length; j++) {
    const ch = s[j];
    if (ch === "\\") {
      j++;
      continue;
    }
    if (ch === "$") {
      if (isSpace(s[j - 1])) return -1;
      if (/\d/.test(s[j + 1] ?? "")) continue;
      return j;
    }
  }
  return -1;
}

export function parseInline(s: string): Inline[] {
  const out: Inline[] = [];
  let buf = "";
  const flush = () => {
    if (buf) {
      out.push({ t: "text", v: buf });
      buf = "";
    }
  };
  let i = 0;
  while (i < s.length) {
    const ch = s[i]!;
    // 转义
    if (ch === "\\" && i + 1 < s.length && "$*_`\\[]()!|".includes(s[i + 1]!)) {
      buf += s[i + 1];
      i += 2;
      continue;
    }
    // 独立公式 $$…$$
    if (ch === "$" && s[i + 1] === "$") {
      const end = s.indexOf("$$", i + 2);
      if (end > i + 2) {
        flush();
        out.push({ t: "math", src: s.slice(i + 2, end).trim(), display: true });
        i = end + 2;
        continue;
      }
    }
    // 行内公式 $…$
    if (ch === "$" && !isSpace(s[i + 1])) {
      const end = findInlineClose(s, i + 1);
      if (end > i + 1) {
        flush();
        out.push({ t: "math", src: s.slice(i + 1, end), display: false });
        i = end + 1;
        continue;
      }
    }
    // 填空横线：3 个及以上下划线
    if (ch === "_") {
      let j = i;
      while (s[j] === "_") j++;
      if (j - i >= 3) {
        flush();
        out.push({ t: "blank" });
        i = j;
        continue;
      }
    }
    // 插图 ![alt](fig:id)
    if (ch === "!" && s[i + 1] === "[") {
      const m = /^!\[([^\]]*)\]\(fig:([^)\s]+)\)/.exec(s.slice(i));
      if (m) {
        flush();
        out.push({ t: "fig", id: m[2]!, alt: m[1]! });
        i += m[0].length;
        continue;
      }
    }
    // 行内代码
    if (ch === "`") {
      const end = s.indexOf("`", i + 1);
      if (end > i + 1) {
        flush();
        out.push({ t: "code", v: s.slice(i + 1, end) });
        i = end + 1;
        continue;
      }
    }
    // 粗体 / 斜体：开头后面不是空白，结尾前面不是空白
    if (ch === "*") {
      const strong = s[i + 1] === "*";
      const mark = strong ? "**" : "*";
      const start = i + mark.length;
      if (!isSpace(s[start])) {
        const end = s.indexOf(mark, start);
        if (end > start && !isSpace(s[end - 1])) {
          flush();
          const inner = parseInline(s.slice(start, end));
          out.push(strong ? { t: "strong", c: inner } : { t: "em", c: inner });
          i = end + mark.length;
          continue;
        }
      }
    }
    buf += ch;
    i++;
  }
  flush();
  return out;
}

const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

function splitRow(line: string): string[] {
  let t = line.trim();
  if (t.startsWith("|")) t = t.slice(1);
  if (t.endsWith("|")) t = t.slice(0, -1);
  // \| 与公式 $…$ 里的 | 不是列分隔
  const cells: string[] = [];
  let cur = "";
  let inMath = false;
  for (let i = 0; i < t.length; i++) {
    if (t[i] === "\\" && t[i + 1] === "|") {
      cur += "\\|";
      i++;
    } else if (t[i] === "\\" && i + 1 < t.length) {
      cur += t[i]! + t[i + 1]!;
      i++;
    } else if (t[i] === "$") {
      inMath = !inMath;
      cur += "$";
    } else if (t[i] === "|" && !inMath) {
      cells.push(cur.trim());
      cur = "";
    } else cur += t[i];
  }
  cells.push(cur.trim());
  return cells;
}

export function parseBlocks(src: string): Block[] {
  const lines = src.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];
  let para: string[] = [];
  const flushPara = () => {
    if (para.length) {
      blocks.push({ t: "p", c: parseInline(para.join(" ").trim()) });
      para = [];
    }
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]!;
    if (!line.trim()) {
      flushPara();
      continue;
    }
    // 竖线表格：当前行含 | 且下一行是分隔行
    if (line.includes("|") && TABLE_SEP.test(lines[i + 1] ?? "")) {
      flushPara();
      const head = splitRow(line).map(parseInline);
      const rows: Inline[][][] = [];
      i += 2;
      while (i < lines.length && lines[i]!.includes("|") && lines[i]!.trim()) {
        rows.push(splitRow(lines[i]!).map(parseInline));
        i++;
      }
      i--;
      blocks.push({ t: "table", head, rows });
      continue;
    }
    const ul = /^\s*[-*+]\s+(.*)$/.exec(line);
    const ol = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (ul || ol) {
      flushPara();
      const items: Inline[][] = [];
      const re = ul ? /^\s*[-*+]\s+(.*)$/ : /^\s*\d+[.)]\s+(.*)$/;
      while (i < lines.length) {
        const m = re.exec(lines[i]!);
        if (!m) break;
        items.push(parseInline(m[1]!));
        i++;
      }
      i--;
      blocks.push(ul ? { t: "ul", items } : { t: "ol", items });
      continue;
    }
    para.push(line.trim());
  }
  flushPara();
  return blocks;
}

/** 纯文本摘要（用于列表标题、对话里的引用等）：去掉公式定界符与标记。 */
export function plainText(src: string, max = 60): string {
  const t = src
    .replace(/!\[[^\]]*\]\(fig:[^)]*\)/g, "（图）")
    .replace(/\$\$?([^$]+)\$\$?/g, "$1")
    .replace(/_{3,}/g, "＿＿")
    .replace(/[*`]/g, "")
    .replace(/\s+/g, " ")
    .trim();
  return t.length > max ? `${t.slice(0, max)}…` : t;
}

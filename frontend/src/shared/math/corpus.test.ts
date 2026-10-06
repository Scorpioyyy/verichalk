/**
 * K2：公式渲染评测（eval/specs/frontend.md）。把评测数据里所有题目文本逐条过前端渲染器，
 * 公式一律用 KaTeX 的 throwOnError 渲染，统计失败条数。阈值：0。
 *
 * 失败有两种根因，修在对应的层：①前端解析把非公式当成了公式（修 parse.ts）；②后端内容里有 KaTeX 不支持的写法（修内容规范化）。
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { parse } from "yaml";
import { renderMath } from "./Rich";
import { parseBlocks, parseInline } from "./parse";
import type { Block, Inline } from "./parse";

const ROOT = resolve(__dirname, "../../../..");

function load(rel: string): unknown {
  return parse(readFileSync(resolve(ROOT, rel), "utf-8"));
}

function texts(node: unknown, out: string[] = []): string[] {
  if (Array.isArray(node)) node.forEach((x) => texts(x, out));
  else if (node && typeof node === "object") {
    for (const [k, v] of Object.entries(node as Record<string, unknown>)) {
      if (["stem", "solution", "answer", "text"].includes(k) && typeof v === "string") out.push(v);
      else if (k === "options" && Array.isArray(v))
        out.push(...v.filter((o): o is string => typeof o === "string"));
      else texts(v, out);
    }
  }
  return out;
}

function mathOf(nodes: Inline[], out: { src: string; display: boolean }[]): void {
  for (const n of nodes) {
    if (n.t === "math") out.push({ src: n.src, display: n.display });
    else if (n.t === "strong" || n.t === "em") mathOf(n.c, out);
  }
}

function collectMath(src: string): { src: string; display: boolean }[] {
  const out: { src: string; display: boolean }[] = [];
  const walk = (b: Block) => {
    if (b.t === "p") mathOf(b.c, out);
    else if (b.t === "ul" || b.t === "ol") b.items.forEach((i) => mathOf(i, out));
    else {
      b.head.forEach((c) => mathOf(c, out));
      b.rows.forEach((r) => r.forEach((c) => mathOf(c, out)));
    }
  };
  parseBlocks(src).forEach(walk);
  return out;
}

const SOURCES = [
  "eval/datasets/export/papers.yaml",
  "eval/datasets/edit/papers.yaml",
  "eval/datasets/verify/bank.yaml",
];

describe("K2 公式渲染语料", () => {
  for (const rel of SOURCES) {
    it(`${rel}：每个公式都能渲染，解析不抛异常`, () => {
      const all = texts(load(rel));
      expect(all.length).toBeGreaterThan(5);
      let nMath = 0;
      const failures: string[] = [];
      for (const t of all) {
        parseInline(t); // 不得抛异常
        for (const m of collectMath(t)) {
          nMath += 1;
          const r = renderMath(m.src, m.display, true);
          if (r.error) failures.push(`${m.src}  →  ${r.error.slice(0, 80)}`);
        }
      }
      expect(failures, failures.slice(0, 10).join("\n")).toEqual([]);
      if (rel.includes("export")) expect(nMath).toBeGreaterThan(20); // 导出评测的试卷专门含有大量公式
    });
  }
});

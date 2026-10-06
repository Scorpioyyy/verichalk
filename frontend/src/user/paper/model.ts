/** 试卷的展示派生量：连续题号、分区标题、统计。纯函数。 */
import type { Item, Paper, Section, VerifyStatus } from "@/shared/api/types";
import { fmtScore } from "@/shared/labels";

const CN = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十"];

export function cnNumeral(n: number): string {
  if (n <= 10) return CN[n] ?? String(n);
  if (n < 20) return `十${CN[n - 10]}`;
  if (n < 100) return `${CN[Math.floor(n / 10)]}十${n % 10 ? CN[n % 10] : ""}`;
  return String(n);
}

export interface NumberedItem {
  item: Item;
  no: number;
  section: Section;
  indexInSection: number;
}

export function numbered(paper: Paper): NumberedItem[] {
  const out: NumberedItem[] = [];
  let no = 0;
  for (const section of paper.sections) {
    section.items.forEach((item, indexInSection) => {
      no += 1;
      out.push({ item, no, section, indexInSection });
    });
  }
  return out;
}

export function sectionHeading(paper: Paper, section: Section, index: number): string | null {
  // 单个"综合练习"分区不需要标题；有多个分区（整卷）才显示"一、选择题"
  if (paper.sections.length <= 1 && (section.kind === "mixed" || !section.title)) return null;
  const items = section.items;
  const scores = items
    .map((i) => i.score)
    .filter((s): s is number => s !== null && s !== undefined);
  let detail = `共 ${items.length} 题`;
  if (scores.length === items.length && items.length > 0) {
    const total = scores.reduce((a, b) => a + b, 0);
    const first = scores[0];
    detail += scores.every((s) => s === first) ? `，每题 ${fmtScore(first)} 分` : "";
    detail += `，共 ${fmtScore(total)} 分`;
  }
  return `${cnNumeral(index + 1)}、${section.title || "练习题"}（${detail}）`;
}

export function statusCounts(paper: Paper): Record<VerifyStatus, number> {
  const c: Record<VerifyStatus, number> = {
    verified: 0,
    checked: 0,
    needs_review: 0,
    pending: 0,
    rejected: 0,
  };
  for (const it of paper.sections.flatMap((s) => s.items)) c[it.verification.status] += 1;
  return c;
}

export function totalScore(paper: Paper): number | null {
  const items = paper.sections.flatMap((s) => s.items);
  if (items.length === 0 || items.some((i) => i.score === null || i.score === undefined))
    return null;
  return items.reduce((a, i) => a + (i.score ?? 0), 0);
}

export function optionLetter(i: number): string {
  return String.fromCharCode(65 + i);
}

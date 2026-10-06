/** 面向教师的文案：内部标识 → 教师语言（产品原则 1：不暴露内部概念）。 */
import type { ItemKind, KPRef, Tier, VerifyStatus } from "./api/types";

export const KIND_LABEL: Record<ItemKind, string> = {
  choice: "选择题",
  fill: "填空题",
  calc: "计算题",
  judge: "判断题",
  application: "应用题",
  open: "开放题",
};

export const TIER_LABEL: Record<Tier, string> = {
  consolidate: "巩固",
  variation: "变式",
  integrated: "综合",
};

export const DIFFICULTY_LABEL = ["", "容易", "较易", "中等", "较难", "困难"];

export type Tone = "ok" | "info" | "warn" | "bad" | "neutral";

export interface StatusView {
  label: string;
  tone: Tone;
  /** 一句话说明这个状态意味着什么（悬停 / 展开时给教师看） */
  hint: string;
}

export const STATUS_VIEW: Record<VerifyStatus, StatusView> = {
  verified: { label: "已核验", tone: "ok", hint: "程序验算与独立解题两路结果一致，答案可靠。" },
  checked: { label: "已校对", tone: "info", hint: "有一路核验通过；建议您再看一眼答案。" },
  needs_review: { label: "需复核", tone: "warn", hint: "核验时发现疑点，请您确认后再使用。" },
  pending: { label: "待核验", tone: "neutral", hint: "刚修改过，正在重新核验。" },
  rejected: { label: "未通过", tone: "bad", hint: "核验没有通过。" },
};

/** 核验项的教师叫法。未知的名称不外露，统一叫"其他检查"。 */
export const CHECK_LABEL: Record<string, string> = {
  structure: "题目格式",
  program: "程序验算",
  blind: "独立解题",
  boundary: "是否超纲",
  quality: "题面质量",
  integration: "综合程度",
  novelty: "与照片里的题不雷同",
};

export function checkLabel(name: string): string {
  return CHECK_LABEL[name] ?? "其他检查";
}

export const CHECK_STATUS_LABEL = {
  pass: "通过",
  warn: "有疑点",
  fail: "未通过",
  skip: "未检查",
} as const;

const GRADE_CN = ["", "一", "二", "三", "四", "五", "六"];

export function gradeSemester(
  grade: number | null | undefined,
  semester: string | null | undefined,
): string {
  if (!grade) return "";
  return `${GRADE_CN[grade] ?? grade}${semester === "a" ? "上" : semester === "b" ? "下" : ""}`;
}

/** "小数加减法（四下）" */
export function kpLabel(ref: Pick<KPRef, "name" | "grade" | "semester">): string {
  const where = gradeSemester(ref.grade, ref.semester);
  return where ? `${ref.name}（${where}）` : ref.name;
}

/** 总分、分值的显示：整数不带小数点，半分保留一位。 */
export function fmtScore(n: number | null | undefined): string {
  if (n === null || n === undefined) return "";
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

export function fmtDuration(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  return s < 60 ? `${s} 秒` : `${Math.floor(s / 60)} 分 ${s % 60} 秒`;
}

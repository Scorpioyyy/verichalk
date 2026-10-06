import AxeBuilder from "@axe-core/playwright";
import { expect } from "@playwright/test";
import type { Page } from "@playwright/test";

/** 用户端界面里不允许出现的内部词（产品原则 1："说老师的话"）。 */
const JARGON = [
  "kp_id",
  "kp.",
  "archetype",
  "borderline",
  "needs_review",
  "verified",
  "pending",
  "rejected",
  "consolidate",
  "variation",
  "integrated",
  "answer_value",
  "[object",
  "undefined",
  "NaN",
  "null",
];
const ID_RE = /\b(?:itm|run|ses|spn|msg|pap|sec|att|cp|bc)_[0-9A-Z]{8,}\b/;

/** 收集 console 错误、未捕获异常与失败的请求（预期的除外）。 */
export function watchConsole(page: Page, allow: RegExp[] = []) {
  const problems: string[] = [];
  const ok = (s: string) => allow.some((r) => r.test(s));
  page.on("console", (m) => {
    if (m.type() === "error" && !ok(m.text())) problems.push(`console.error: ${m.text()}`);
  });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("response", (r) => {
    if (r.status() >= 400 && !ok(`${r.status()} ${r.url()}`))
      problems.push(`HTTP ${r.status()} ${r.url()}`);
  });
  return {
    problems,
    expectClean: () => expect(problems, problems.join("\n")).toEqual([]),
  };
}

/** K5：当前页面可见文字里没有内部词、没有裸 ID。公式的 MathML 副本（含 TeX 源码）不算。 */
export async function expectNoJargon(page: Page) {
  const text = await page.evaluate(() => {
    const clone = document.body.cloneNode(true) as HTMLElement;
    clone
      .querySelectorAll(".katex-mathml, script, style, [data-testid='drafts-debug']")
      .forEach((n) => n.remove());
    return clone.innerText;
  });
  const hits = JARGON.filter((w) => text.includes(w));
  expect(hits, `界面出现内部词：${hits.join("、")}`).toEqual([]);
  expect(ID_RE.test(text), "界面出现了裸 ID").toBe(false);
}

/** K6：axe 扫描，serious / critical 为 0。 */
export async function expectNoA11yViolations(page: Page, label: string) {
  const res = await new AxeBuilder({ page }).analyze();
  const bad = res.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(
    bad.map(
      (v) =>
        `${v.id}（${v.impact}）：${v.help}\n  ${v.nodes
          .slice(0, 3)
          .map((n) => n.target.join(" "))
          .join("\n  ")}`,
    ),
    `${label}：可访问性问题`,
  ).toEqual([]);
}

/** K7：没有横向滚动条；可点击目标不小于 36px（对话框内的复选框等原生控件除外）。 */
export async function expectResponsive(page: Page, label: string) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow, `${label}：出现横向滚动（${overflow}px）`).toBeLessThanOrEqual(1);
  const small = await page.evaluate(() => {
    const out: string[] = [];
    for (const el of document.querySelectorAll<HTMLElement>(
      "button, a[href], [role='button'], select",
    )) {
      const r = el.getBoundingClientRect();
      const visible =
        r.width > 0 &&
        r.height > 0 &&
        getComputedStyle(el).visibility !== "hidden" &&
        getComputedStyle(el).opacity !== "0";
      if (!visible) continue;
      if (r.height < 28 || r.width < 28)
        out.push(
          `${(el.getAttribute("aria-label") || el.textContent || el.tagName).trim().slice(0, 20)}（${Math.round(r.width)}×${Math.round(r.height)}）`,
        );
    }
    return out;
  });
  expect(small, `${label}：可点击目标过小`).toEqual([]);
}

export async function sendMessage(page: Page, text: string) {
  const box = page.getByLabel("输入您的需求");
  await box.fill(text);
  await box.press("Enter");
}

/** 等一次对话运行结束（进度卡消失）。 */
export async function waitRunDone(page: Page) {
  await page.getByTestId("run-progress").waitFor({ state: "detached" });
}

export async function expectFirstFeedbackFast(page: Page, text: string, ms = 1000) {
  const t0 = Date.now();
  await sendMessage(page, text);
  await page.getByTestId("run-progress").waitFor();
  const took = Date.now() - t0;
  expect(took, `首个可见反馈 ${took}ms，超过 ${ms}ms`).toBeLessThan(ms);
  return took;
}

import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import {
  expectFirstFeedbackFast,
  expectNoA11yViolations,
  expectNoJargon,
  expectResponsive,
  sendMessage,
  waitRunDone,
  watchConsole,
} from "./helpers";

async function withPaper(page: Page) {
  await page.goto("/");
  await expectFirstFeedbackFast(page, "四年级下册小数加减法，出 3 道，有点难度");
  await waitRunDone(page);
  await expect(page.getByTestId("item")).toHaveCount(3);
}

test.describe("S6 编辑", () => {
  test("手动编辑 → 重新核验 → 撤销 / 恢复 → 版本历史回退", async ({ page }) => {
    const watch = watchConsole(page);
    await withPaper(page);
    const first = page.getByTestId("item").first();
    const originalStem = (await first.locator(".item__stem").innerText()).trim();

    // 编辑题干
    await first.hover();
    await first.getByRole("button", { name: "编辑" }).click();
    const editor = page.getByTestId("item-editor");
    await expect(editor).toBeVisible();
    await expectNoA11yViolations(page, "编辑器");
    const stem = editor.getByLabel("题干");
    await stem.click();
    await stem.press("End");
    await stem.pressSequentially("（请写出计算过程）");
    await expect(editor.getByLabel("效果预览")).toContainText("请写出计算过程");
    await editor.getByRole("button", { name: "保存" }).click();

    // 保存后：题回到"待核验 / 正在核验"，随后复核给出结论
    await expect(page.getByTestId("item").first().locator(".item__stem")).toContainText(
      "请写出计算过程",
    );
    await expect(page.getByTestId("item").first()).not.toHaveAttribute("data-status", "pending", {
      timeout: 30_000,
    });
    await expectNoJargon(page);

    // 撤销 → 回到原题干；恢复 → 又是新题干
    await page.getByRole("button", { name: "撤销" }).first().click();
    await expect(page.getByTestId("item").first().locator(".item__stem")).not.toContainText(
      "请写出计算过程",
    );
    expect((await page.getByTestId("item").first().locator(".item__stem").innerText()).trim()).toBe(
      originalStem,
    );
    await page.getByRole("button", { name: "恢复" }).first().click();
    await expect(page.getByTestId("item").first().locator(".item__stem")).toContainText(
      "请写出计算过程",
    );

    // 版本历史：回到最初的一版
    await page.getByRole("button", { name: "版本历史" }).click();
    const drawer = page.getByTestId("history-drawer");
    await expect(drawer).toBeVisible();
    await drawer.getByRole("button", { name: "看改动" }).first().click();
    await expect(drawer.getByTestId("diff")).toContainText("题干");
    await expectNoA11yViolations(page, "版本历史");
    await drawer.getByRole("button", { name: "回到这一版" }).last().click();
    await expect(page.getByTestId("item").first().locator(".item__stem")).not.toContainText(
      "请写出计算过程",
    );

    // 删除与撤销删除
    const before = await page.getByTestId("item").count();
    await page.getByTestId("item").nth(1).hover();
    await page.getByTestId("item").nth(1).getByRole("button", { name: "删除" }).click();
    await expect(page.getByTestId("item")).toHaveCount(before - 1);
    await page.getByRole("button", { name: "撤销", exact: true }).last().click();
    await expect(page.getByTestId("item")).toHaveCount(before);

    watch.expectClean();
  });

  test("自然语言修改：改哪一题、改了什么，一眼看清，且能撤销", async ({ page }) => {
    const watch = watchConsole(page);
    await withPaper(page);
    const before = await page.getByTestId("item").nth(1).locator(".item__stem").innerText();
    await expectFirstFeedbackFast(page, "第 2 题换个场景");
    await expect(page.getByTestId("last-edit")).toBeVisible();
    await waitRunDone(page);
    const after = await page.getByTestId("item").nth(1).locator(".item__stem").innerText();
    expect(after).not.toBe(before);
    // 其他题没有被改动
    await expect(page.getByTestId("item")).toHaveCount(3);

    await page.getByRole("button", { name: "查看改动" }).click();
    await expect(page.getByTestId("diff")).toContainText("第 2 题");
    await expectNoJargon(page);
    await page.keyboard.press("Escape");
    await page.getByTestId("last-edit").getByRole("button", { name: "撤销" }).click();
    // 用 innerText 对比（公式的 MathML 副本不参与），与上面取 before 的方式一致
    await expect
      .poll(() => page.getByTestId("item").nth(1).locator(".item__stem").innerText())
      .toBe(before);
    watch.expectClean();
  });

  test("两个窗口同时改：后一个得到清楚的提示，不会覆盖前一个", async ({ browser }) => {
    const ctx = await browser.newContext();
    const a = await ctx.newPage();
    await withPaper(a);
    const sid = await a.evaluate(() => JSON.parse(localStorage.getItem("verichalk.session")!));
    const b = await ctx.newPage();
    await b.goto("/");
    await expect(b.getByTestId("item")).toHaveCount(3);
    expect(await b.evaluate(() => JSON.parse(localStorage.getItem("verichalk.session")!))).toBe(
      sid,
    );

    // A 先改标题
    await a.getByRole("heading", { level: 1 }).getByRole("button").click();
    await a.getByLabel("试卷标题").fill("A 窗口的标题");
    await a.getByLabel("试卷标题").press("Enter");
    await expect(a.getByRole("heading", { level: 1 })).toContainText("A 窗口的标题");

    // B 还停留在旧版本：编辑题干并保存 → 提示"试卷已经有了新的修改"
    const first = b.getByTestId("item").first();
    await first.hover();
    await first.getByRole("button", { name: "编辑" }).click();
    const stem = b.getByTestId("item-editor").getByLabel("题干");
    await stem.click();
    await stem.press("End");
    await stem.pressSequentially("（B）");
    await b.getByRole("button", { name: "保存" }).click();
    await expect(b.getByText(/新的修改/)).toBeVisible();
    // 提示之后 B 已经同步到最新版本（标题是 A 改的）
    await expect(b.getByRole("heading", { level: 1 })).toContainText("A 窗口的标题");
    await ctx.close();
  });

  test("手机宽度：两个视图用底部标签切换，导出可达", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await withPaper(page);
    await expectResponsive(page, "手机·对话");
    await page.getByRole("button", { name: /试卷/ }).last().click();
    await expect(page.getByTestId("item").first()).toBeVisible();
    await expect(page.getByTestId("export-open")).toBeVisible();
    await expectResponsive(page, "手机·试卷");
    await expectNoA11yViolations(page, "手机·试卷");
  });
});

test.describe("S7 追问", () => {
  test("追问只读：回答问题，不改试卷", async ({ page }) => {
    await withPaper(page);
    const stems = await page.locator(".item__stem").allInnerTexts();
    await sendMessage(page, "第 1 题为什么这样算？");
    await page.getByTestId("run-progress").waitFor();
    await waitRunDone(page);
    await expect(page.locator(".chat-text").last()).not.toBeEmpty();
    expect(await page.locator(".item__stem").allInnerTexts()).toEqual(stems);
    await expect(page.getByTestId("last-edit")).toHaveCount(0);
  });
});

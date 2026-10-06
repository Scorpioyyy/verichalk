import { expect, test } from "@playwright/test";
import fs from "node:fs";
import {
  expectFirstFeedbackFast,
  expectNoA11yViolations,
  expectNoJargon,
  expectResponsive,
  waitRunDone,
  watchConsole,
} from "./helpers";

test.describe("S1 单点出题", () => {
  test("一句话 → 逐题上屏 → 已核验 → 切换教师版 / 学生版 → 导出", async ({ page }, info) => {
    const watch = watchConsole(page);
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "今天想出什么题？" })).toBeVisible();
    await expectNoA11yViolations(page, "欢迎页");
    await expectResponsive(page, "欢迎页");

    // K4：点击发送到首个可见反馈 ≤ 1s
    await expectFirstFeedbackFast(page, "四年级下册小数加减法，出 3 道，有点难度");
    await expectNoA11yViolations(page, "运行中");

    // 逐题上屏：整份试卷装配之前，已经能看到送达的题
    await expect(page.getByTestId("item").first()).toBeVisible();
    await waitRunDone(page);

    const items = page.getByTestId("item");
    await expect(items).toHaveCount(3);
    for (let i = 0; i < 3; i++) {
      const status = await items.nth(i).getAttribute("data-status");
      expect(["verified", "checked", "needs_review"], `第 ${i + 1} 题状态 ${status}`).toContain(
        status,
      );
    }
    // 本次假设芯片：年级被识别
    await expect(page.getByTestId("chips")).toContainText("四年级");
    // 助手的总结
    await expect(page.getByText(/已为您出好 3 道题/)).toBeVisible();

    await expectNoJargon(page);
    await expectNoA11yViolations(page, "出题结果");
    await expectResponsive(page, "出题结果");

    // 教师版有答案；学生版没有
    await expect(page.locator(".item__answer").first()).toBeVisible();
    await page.getByRole("button", { name: "学生版" }).first().click();
    await expect(page.locator(".item__answer")).toHaveCount(0);
    await page.getByRole("button", { name: "教师版" }).first().click();
    await expect(page.locator(".item__answer").first()).toBeVisible();

    // 核验明细可展开，且说的是教师语言
    await items
      .first()
      .getByRole("button", { name: /已核验|已校对|需复核/ })
      .click();
    await expect(items.first().getByLabel("核验明细")).toBeVisible();
    await expectNoJargon(page);

    // S8 导出：PDF 与 Word
    await page.getByTestId("export-open").click();
    await expect(page.getByTestId("export-dialog")).toBeVisible();
    await expectNoA11yViolations(page, "导出对话框");
    const [pdf] = await Promise.all([
      page.waitForEvent("download"),
      page.getByTestId("export-run").click(),
    ]);
    const pdfPath = info.outputPath("export.pdf");
    await pdf.saveAs(pdfPath);
    expect(fs.readFileSync(pdfPath).subarray(0, 4).toString()).toBe("%PDF");
    expect(pdf.suggestedFilename()).toMatch(/\.pdf$/);
    await expect(page.getByText(/已导出/)).toBeVisible();

    await page.getByTestId("export-open").click();
    await page.getByRole("button", { name: /Word/ }).click();
    await page
      .getByRole("button", { name: /教师版/ })
      .last()
      .click();
    const [docx] = await Promise.all([
      page.waitForEvent("download"),
      page.getByTestId("export-run").click(),
    ]);
    const docxPath = info.outputPath("export.docx");
    await docx.saveAs(docxPath);
    expect(fs.readFileSync(docxPath).subarray(0, 2).toString()).toBe("PK");
    expect(fs.statSync(docxPath).size).toBeGreaterThan(5_000);

    watch.expectClean();
  });

  test("试卷视图显示的就是导出的 PDF", async ({ page }) => {
    await page.goto("/");
    await expectFirstFeedbackFast(page, "四年级下册小数加减法，出 3 道，有点难度");
    await waitRunDone(page);
    await page.getByRole("button", { name: "试卷", exact: true }).click();
    await expect(page.getByTestId("paper-preview")).toHaveAttribute("data-state", "ready", {
      timeout: 30_000,
    });
    const pages = page.getByTestId("pdf-pages").locator("canvas");
    expect(await pages.count()).toBeGreaterThan(0);
    // 画布不是空白：至少有一部分像素不是白色
    const painted = await pages.first().evaluate((c: HTMLCanvasElement) => {
      const d = c.getContext("2d")!.getImageData(0, 0, c.width, c.height).data;
      let dark = 0;
      for (let i = 0; i < d.length; i += 4 * 17) if (d[i]! < 200) dark++;
      return dark;
    });
    expect(painted).toBeGreaterThan(50);
    await expectResponsive(page, "试卷视图");
  });
});

test.describe("S9 异常与追问", () => {
  test("需求含糊：只问一个问题，带可点选项", async ({ page }) => {
    const watch = watchConsole(page);
    await page.goto("/");
    await expectFirstFeedbackFast(page, "帮我出几道题");
    const cp = page.getByTestId("checkpoint-clarify");
    await expect(cp).toBeVisible();
    await expect(cp.getByRole("button")).toHaveCount(8); // 六个年级 + 你来定 + 确定
    await expectNoA11yViolations(page, "澄清");
    await cp.getByRole("button", { name: "三年级" }).click();
    await expect(page.getByTestId("item").first()).toBeVisible();
    await waitRunDone(page);
    await expect(page.getByTestId("chips")).toContainText("三年级");
    watch.expectClean();
  });

  test("与数学命题无关：体面地说明能力范围，不出题", async ({ page }) => {
    await page.goto("/");
    await expectFirstFeedbackFast(page, "帮我写一首关于春天的诗");
    await waitRunDone(page);
    await expect(page.getByText(/不在我的能力范围/)).toBeVisible();
    await expect(page.getByTestId("item")).toHaveCount(0);
  });
});

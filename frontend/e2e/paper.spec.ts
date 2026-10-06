import { expect, test } from "@playwright/test";
import {
  expectFirstFeedbackFast,
  expectNoA11yViolations,
  expectNoJargon,
  waitRunDone,
  watchConsole,
} from "./helpers";

test.describe("S4 整卷：蓝图 → 样题 → 全卷", () => {
  test("先确认细目表，再看样题，最后成卷；分值合计等于满分", async ({ page }) => {
    const watch = watchConsole(page);
    await page.goto("/");
    await expectFirstFeedbackFast(page, "出一份四年级下册第三单元的单元测试，40 分钟，满分 100");

    // ① 蓝图：细目表可见，可以调整
    const bp = page.getByTestId("checkpoint-blueprint");
    await expect(bp).toBeVisible();
    await expect(bp.getByRole("columnheader", { name: "题型" })).toBeVisible();
    await expectNoA11yViolations(page, "蓝图检查点");
    await bp.getByRole("button", { name: "我想调整" }).click();
    await bp.getByLabel("怎么调整").fill("满分改成 120");
    await bp.getByRole("button", { name: "调整", exact: true }).click();
    await expect(
      page.getByTestId("checkpoint-blueprint").getByRole("row", { name: /合计/ }),
    ).toContainText("120 分");
    await page.getByRole("button", { name: "就这样，开始出题" }).click();

    // ② 样题
    const samples = page.getByTestId("checkpoint-samples");
    await expect(samples).toBeVisible();
    await expect(samples.locator(".sample")).not.toHaveCount(0);
    await expectNoJargon(page);
    await samples.getByRole("button", { name: "合适，继续出全卷" }).click();

    // ③ 全卷
    await waitRunDone(page);
    const total = page.getByText(/满分 120 分/).first();
    await expect(total).toBeVisible();
    const n = await page.getByTestId("item").count();
    expect(n).toBeGreaterThanOrEqual(10);
    // 分区标题带"共 x 题，每题 y 分"，且各题分值之和 = 满分
    const scores = await page.locator(".item__meta").allInnerTexts();
    const sum = scores
      .map((s) => Number(/(\d+(?:\.\d+)?) 分/.exec(s)?.[1] ?? 0))
      .reduce((a, b) => a + b, 0);
    expect(sum).toBe(120);
    await expect(page.locator(".section__title").first()).toContainText("一、");
    await expectNoJargon(page);
    watch.expectClean();
  });
});

import { expect, test } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  expectNoA11yViolations,
  expectNoJargon,
  expectResponsive,
  waitRunDone,
  watchConsole,
} from "./helpers";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIX = (name: string) => path.join(HERE, "fixtures", name);

test.describe("S3 拍照出题（M4）", () => {
  test("上传练习页 + 一句话 → 识别卡片 → 照着出新题，不照抄", async ({ page }) => {
    const watch = watchConsole(page);
    await page.goto("/");
    await page.getByTestId("photo-input").setInputFiles(FIX("worksheet.png"));
    await expect(page.getByRole("list", { name: "待发送的照片" }).getByRole("img")).toBeVisible();
    await expectNoA11yViolations(page, "带照片的输入框");
    await expectResponsive(page, "带照片的输入框");

    const box = page.getByLabel("输入您的需求");
    await box.fill("照这页再出 3 道");
    await box.press("Enter");

    // 照片出现在对话里，随后是识别卡片
    await expect(page.locator(".msg__photo img").first()).toBeVisible();
    const card = page.getByTestId("perception-card");
    await expect(card).toBeVisible({ timeout: 60_000 });
    await expect(card).toContainText("读到了");
    await expect(card).toContainText("0.70");
    await expectNoA11yViolations(page, "识别卡片");

    await expect(page.getByTestId("item").first()).toBeVisible();
    await waitRunDone(page);
    const items = page.getByTestId("item");
    await expect(items).toHaveCount(3);
    // 范围来自照片：年级与知识点芯片
    await expect(page.getByTestId("chips")).toContainText("四年级");
    // 防雷同：新题不是照片里那道应用题的翻版
    const stems = await items.allInnerTexts();
    expect(stems.some((t) => t.includes("食堂运来大米10.7吨"))).toBe(false);

    await expectNoJargon(page);
    await expectNoA11yViolations(page, "拍照出题结果");
    await expectResponsive(page, "拍照出题结果");
    watch.expectClean();

    // 刷新后识别卡片还在（从事件流恢复）
    await page.reload();
    await expect(page.getByTestId("perception-card")).toBeVisible();
  });

  test("画质很差的照片：先请教师核对，改一处再继续", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("photo-input").setInputFiles(FIX("worksheet_blur.png"));
    await page.getByLabel("输入您的需求").press("Enter"); // 只发照片，不写要求

    const cp = page.getByTestId("checkpoint-perception");
    await expect(cp).toBeVisible({ timeout: 60_000 });
    await expect(cp).toContainText("画质");
    await expectNoA11yViolations(page, "识别结果核对");
    await expectResponsive(page, "识别结果核对");

    const first = cp.getByRole("textbox").first();
    await first.fill("0.70=（已核对）");
    await cp.getByRole("button", { name: "按修改后的开始出题" }).click();

    await expect(page.getByTestId("item").first()).toBeVisible({ timeout: 90_000 });
    await waitRunDone(page);
    await expect(page.getByTestId("perception-card")).toContainText("已核对");
    await expectNoJargon(page);
  });

  test("不是数学的图：友好说明，不出题", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("photo-input").setInputFiles(FIX("english.png"));
    await page.getByLabel("输入您的需求").press("Enter");
    await expect(page.getByText(/不是数学/)).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText(/重新拍/)).toBeVisible();
    await expect(page.getByTestId("item")).toHaveCount(0);
    await expect(page.getByTestId("checkpoint-perception")).toHaveCount(0);
    await expectNoJargon(page);
  });

  test("上传校验：最多 4 张、只收图片；移除后可重选", async ({ page }) => {
    await page.goto("/");
    const input = page.getByTestId("photo-input");
    await input.setInputFiles(Array.from({ length: 5 }, () => FIX("worksheet.png")));
    await expect(page.getByText("一次最多上传 4 张照片。")).toBeVisible();
    const chips = page.getByRole("list", { name: "待发送的照片" }).getByRole("listitem");
    await expect(chips).toHaveCount(4);
    await page.getByRole("button", { name: "移除第 1 张照片" }).click();
    await expect(chips).toHaveCount(3);
    await input.setInputFiles({
      name: "notes.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("hi"),
    });
    await expect(page.getByText("暂只支持 JPG / PNG / WebP 图片。")).toBeVisible();
    await expect(chips).toHaveCount(3);
    // 发送按钮：有照片即可发送，不必写字
    await expect(page.getByRole("button", { name: "发送" })).toBeEnabled();
  });
});

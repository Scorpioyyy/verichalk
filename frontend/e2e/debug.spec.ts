import { expect, test } from "@playwright/test";
import {
  expectFirstFeedbackFast,
  expectNoA11yViolations,
  waitRunDone,
  watchConsole,
} from "./helpers";

/** K10：调试台能复现并定位一次运行的去向。先用用户端产生一次真实运行，再到调试台检查。 */
test.describe("D1 调试台", () => {
  test("运行 → 流程 → 图检索 → 模型调用 → 题目证据 → Badcase", async ({ page, request }) => {
    const watch = watchConsole(page);
    await page.goto("/");
    await expectFirstFeedbackFast(page, "四年级下册小数加减法，出 3 道，有点难度");
    await waitRunDone(page);

    // 运行列表：刚才那次在最上面，指标齐全
    await page.goto("/debug");
    const table = page.getByTestId("run-table");
    await expect(table.locator("tbody tr").first()).toContainText("四年级下册小数加减法");
    await expect(page.getByLabel("聚合指标")).toContainText("成功率");
    await expectNoA11yViolations(page, "调试台·运行列表");
    await table.locator("tbody tr").first().getByRole("link").click();

    // 概览：指标条 + trace 完整
    const detail = page.getByTestId("run-detail");
    await expect(detail.getByLabel("本次运行指标")).toContainText("模型调用");
    await expect(detail).not.toContainText("Trace 不完整");

    // 流程与时间线：阶段齐全，点击一行看详情
    const tree = page.getByTestId("span-tree");
    for (const stage of ["understand", "plan", "produce"]) {
      await expect(tree.getByText(stage, { exact: true }).first()).toBeVisible();
    }
    await tree.getByRole("treeitem").nth(1).getByRole("button").first().click();
    await expect(page.getByTestId("span-inspector")).toContainText("耗时");

    // 图检索：节点数 = retrieval.result 去重后的节点数
    const events = (await (
      await request.get(
        `/api/debug/runs/${(await page.url()).split("/").pop()!.split("?")[0]}/events`,
      )
    ).json()) as {
      type: string;
      payload?: { nodes: { id: string }[] };
    }[];
    const expected = new Set(
      events
        .filter((e) => e.type === "retrieval.result")
        .flatMap((e) => e.payload!.nodes.map((n) => n.id)),
    );
    await page.getByTestId("tab-graph").click();
    await expect(page.locator(".gv-node")).toHaveCount(expected.size);
    await page.locator(".gv-node").first().click();
    await expect(page.getByTestId("node-panel")).toBeVisible();
    await expect(page.getByTestId("node-panel")).toContainText("角色");

    // 模型调用：列表 + 提示分段 + 输出
    await page.getByTestId("tab-calls").click();
    const rows = page.getByTestId("calls-table").locator("tbody tr");
    expect(await rows.count()).toBeGreaterThan(3);
    await rows.nth(1).click();
    const cd = page.getByTestId("call-detail");
    await expect(cd).toBeVisible();
    await expect(cd.getByLabel(/提示词分段/)).toBeVisible();
    await cd.getByRole("tab", { name: "输出" }).click();
    await expect(cd.locator("pre").first()).not.toBeEmpty();

    // 题目证据：每道题的全部核验项
    await page.getByTestId("tab-items").click();
    const ev = page.getByTestId("items-evidence");
    await expect(ev.locator(".ev-item")).toHaveCount(3);
    for (const name of ["structure", "program", "blind", "boundary", "quality"]) {
      await expect(ev.locator(".ev-item").first()).toContainText(name);
    }

    // 事件日志
    await page.getByTestId("tab-events").click();
    await page.getByTestId("events-table").locator("tbody tr").nth(2).click();
    await expect(page.locator(".row--json pre")).toContainText("seq");

    // Badcase 入库
    await page.getByRole("button", { name: /标记为 Badcase/ }).click();
    const dlg = page.getByTestId("badcase-dialog");
    await expect(dlg).toBeVisible();
    await expect(page.getByTestId("badcase-save")).toBeDisabled();
    await dlg.getByLabel("观察到的问题（必填）").fill("端到端测试：验证 badcase 入库");
    await dlg.getByRole("button", { name: /W\s*生成/ }).click();
    await page.getByTestId("badcase-save").click();
    await expect(page.getByText(/已入库：bc_/)).toBeVisible();
    await page.goto("/debug/badcases");
    await expect(page.getByTestId("badcase-table")).toContainText("端到端测试：验证 badcase 入库");

    // 点左上角的"调试台"回到运行列表
    await page.getByRole("link", { name: /VeriChalk 调试台/ }).click();
    await expect(page.getByTestId("run-table")).toBeVisible();
    watch.expectClean();
  });

  test("配置了令牌时：没有令牌看到的是令牌输入，不是数据", async ({ page }) => {
    // 本测试服务未配置令牌（本机开发态直接可进）；这里只验证令牌输入的前端流程可达
    await page.route("**/api/debug/runs?**", (r) =>
      r.fulfill({ status: 403, json: { detail: "调试台需要有效令牌" } }),
    );
    await page.goto("/debug");
    await expect(page.getByRole("heading", { name: "调试台" })).toBeVisible();
    await expect(page.getByLabel("令牌")).toBeVisible();
    await expectNoA11yViolations(page, "调试台·令牌");
  });

  test("AI 分析：围绕这一次运行提问，流式回答并展示查询过程", async ({ page }) => {
    // 分析助手用的是实时模型，回放环境里没有录制：这里只验证界面与接口的衔接
    await page.route("**/api/debug/runs/*/chat", (r) => {
      const frames = [
        { type: "tool", name: "get_stage_output", label: "查看阶段快照", arguments: {} },
        { type: "delta", text: "这次运行**没有异常**：\n\n- 共 " },
        { type: "delta", text: "若干次模型调用\n" },
        { type: "done" },
      ];
      return r.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: frames.map((f) => `data: ${JSON.stringify(f)}\n\n`).join(""),
      });
    });
    await page.goto("/");
    await expectFirstFeedbackFast(page, "帮我写一首关于春天的诗");
    await waitRunDone(page);
    await page.goto("/debug");
    await page.getByTestId("run-table").locator("tbody tr").first().getByRole("link").click();
    await page.getByTestId("tab-ai").click();
    const panel = page.getByTestId("ai-panel");
    await panel.getByRole("button", { name: /有没有异常/ }).click();
    await expect(panel.locator("strong", { hasText: "没有异常" })).toBeVisible();
    await expect(panel).toContainText("查看阶段快照");
    await expect(panel.getByRole("listitem").filter({ hasText: "若干次模型调用" })).toBeVisible();
    // 切到别的视图再回来，对话还在
    await page.getByTestId("tab-events").click();
    await page.getByTestId("tab-ai").click();
    await expect(panel.locator("strong", { hasText: "没有异常" })).toBeVisible();
    await expectNoA11yViolations(page, "调试台·AI 分析");
  });
});

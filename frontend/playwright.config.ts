import { defineConfig, devices } from "@playwright/test";

/**
 * 端到端评测（eval/specs/frontend.md §2 的 K3–K10）：真实后端 + 真实浏览器。
 *
 * - 模型调用默认从录制回放（`E2E_MODE=replay`，确定、零成本）；`E2E_MODE=replay_or_record` 补录缺失的部分（有录制就回放，保持一组录制互相一致）；整体重录先删掉 eval/cassettes/e2e（需要 DASHSCOPE_* 环境变量）。
 * - 本机没有 Playwright 自带的 Chromium 时，用系统 Edge：`PW_CHANNEL=msedge`。
 * - 后端用 `VERICHALK_PYTHON` 指定的解释器启动（默认 conda 环境 verichalk 之外的 `python`）。
 */
const PORT = Number(process.env.E2E_PORT ?? 8123);
const MODE = process.env.E2E_MODE ?? "replay";
const RECORDING = MODE === "record" || MODE === "replay_or_record";
const python = process.env.VERICHALK_PYTHON ?? "python";

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results",
  timeout: RECORDING ? 600_000 : 90_000,
  expect: { timeout: Number(process.env.E2E_EXPECT_MS) || (RECORDING ? 300_000 : 15_000) },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    channel: process.env.PW_CHANNEL || undefined,
    locale: "zh-CN",
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
    acceptDownloads: true,
  },
  projects: [
    {
      name: "desktop",
      use: { ...devices["Desktop Chrome"], channel: process.env.PW_CHANNEL || undefined },
    },
  ],
  webServer: {
    // 先清空上次的数据目录，再启动服务：保证每次从干净状态开始
    command: `${python} -c "import shutil; shutil.rmtree('tmp/e2e-data', ignore_errors=True)" && ${python} -m verichalk serve --port ${PORT}`,
    cwd: "..",
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: {
      PYTHONUTF8: "1",
      VERICHALK_LLM_MODE: MODE,
      VERICHALK_CASSETTE_NAMESPACE: "e2e",
      VERICHALK_DATA_DIR: "tmp/e2e-data",
      VERICHALK_BADCASE_DIR: "tmp/e2e-data/badcases", // 测试里入库的 Badcase 不能进仓库
      VERICHALK_DEBUG_TOKEN: "",
    },
  },
});

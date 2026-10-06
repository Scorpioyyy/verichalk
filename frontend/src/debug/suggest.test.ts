import { describe, expect, it } from "vitest";
import type { RunModel, SpanNode } from "./model";
import { suggestForRun } from "./suggest";

const span = (name: string, ms: number): SpanNode => ({
  id: `spn_${name}`,
  parentId: null,
  kind: "stage",
  name,
  start: 0,
  end: ms / 1000,
  durationMs: ms,
  state: "ok",
  attrs: {},
  error: null,
  children: [],
  depth: 1,
  calls: [],
});

function model(over: Partial<RunModel> = {}): RunModel {
  const stages = [span("understand", 500), span("produce", 8000)];
  return {
    runId: "run_x",
    t0: 0,
    t1: 10,
    finished: true,
    status: "succeeded",
    error: null,
    roots: [],
    spans: new Map(stages.map((s) => [s.id, s])),
    calls: [],
    retrievals: [],
    items: [],
    lastSeq: 1,
    ...over,
  };
}

const item = (stem: string, status: string, checks: { name: string; status: string }[]) =>
  ({
    id: "itm_1",
    order: 2,
    item: { id: "itm_1", stem },
    statuses: [{ seq: 1, ts: 1, status, checks }],
  }) as unknown as RunModel["items"][number];

const call = (purpose: string, extra: Record<string, unknown>) =>
  ({
    seq: 1,
    ts: 1,
    spanId: null,
    record: { purpose, role: "smart", retries: 0, error: null, ...extra },
  }) as unknown as RunModel["calls"][number];

describe("针对这次运行的推荐提问", () => {
  it("什么异常都没有：退回到最慢的阶段，写出耗时与占比", () => {
    const q = suggestForRun(model());
    expect(q).toHaveLength(1);
    expect(q[0]).toContain("「produce」");
    expect(q[0]).toContain("80%");
  });

  it("失败的运行排第一；没通过核验的题点出题面和没过的检查", () => {
    const q = suggestForRun(
      model({
        status: "failed",
        items: [
          item("在跳水比赛中，小明三轮的得分分别是 8.65 分、9.20 分和 8.85 分", "needs_review", [
            { name: "blind", status: "fail" },
          ]),
        ],
      }),
    );
    expect(q[0]).toContain("为什么失败");
    expect(q[1]).toContain("在跳水比赛中，小明三轮的得分…");
    expect(q[1]).toContain("需复核");
    expect(q[1]).toContain("blind");
  });

  it("模型调用出错 / 重试用清单序号（从 1 起），最多 3 条", () => {
    const q = suggestForRun(
      model({
        calls: [
          call("understand.parse", {}),
          call("produce.write", { retries: 2 }),
          call("produce.write", { error: { code: "llm_timeout" } }),
          call("plan.ideate", { error: { code: "llm_timeout" } }),
        ],
      }),
    );
    expect(q).toHaveLength(3);
    expect(q[0]).toContain("第 2 次模型调用");
    expect(q[0]).toContain("重试了 2 次");
    expect(q[1]).toContain("第 3 次模型调用");
    expect(q[1]).toContain("出错");
  });

  it("没有任何阶段信息时不给推荐（界面退回到常见问题）", () => {
    expect(suggestForRun(model({ spans: new Map() }))).toEqual([]);
  });
});

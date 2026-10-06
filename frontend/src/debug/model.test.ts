import { describe, expect, it } from "vitest";
import type { AppEvent, LLMCallRecord, RetrievalPayload } from "@/shared/api/types";
import { buildModel, defaultCollapsed, layoutGraph, visibleRows } from "./model";
import type { RetrievalStep } from "./model";

let seq = 0;
const e = (type: string, data: object = {}, ts?: number): AppEvent => {
  seq += 1;
  return {
    type,
    seq,
    run_id: "run_x",
    ts: ts ?? 100 + seq / 1000, // 未指定时间戳的事件：紧随其后、间隔 1ms
    span_id: null,
    parent_id: null,
    visibility: "debug",
    ...data,
  } as unknown as AppEvent;
};
const started = (id: string, parent: string | null, kind: string, name: string, ts: number) =>
  e("span.started", { span_id: id, parent_id: parent, kind, name, attrs: { a: 1 } }, ts);
const finished = (
  id: string,
  parent: string | null,
  kind: string,
  name: string,
  ts: number,
  ms: number,
  status = "ok",
) =>
  e(
    "span.finished",
    {
      span_id: id,
      parent_id: parent,
      kind,
      name,
      status,
      duration_ms: ms,
      attrs: { b: 2 },
      error: null,
    },
    ts,
  );

const call = (spanId: string, purpose: string): AppEvent =>
  e("llm.call", {
    span_id: spanId,
    record: {
      id: "c",
      role: "smart",
      model: "m",
      profile: "cn",
      purpose,
      messages: [],
      usage: { prompt_tokens: 1, completion_tokens: 1, cached_tokens: 0, reasoning_tokens: 0 },
      total_ms: 5,
      retries: 0,
      tool_calls: [],
      response_text: "",
      reasoning_text: "",
      from_cassette: false,
      params: {},
      currency: "CNY",
    } as unknown as LLMCallRecord,
  });

function sample(): AppEvent[] {
  seq = 0;
  return [
    e("run.started", { session_id: "s", pipeline: "main", schema_version: "1.2" }, 100),
    started("r", null, "run", "main", 100),
    started("u", "r", "stage", "understand", 100.1),
    started("l1", "u", "llm", "fast:understand.parse", 100.2),
    call("l1", "understand.parse"),
    finished("l1", "u", "llm", "fast:understand.parse", 100.9, 700),
    finished("u", "r", "stage", "understand", 101, 900),
    started("p", "r", "stage", "produce", 101),
    started("c1", "p", "check", "program", 101.1),
    started("t1", "c1", "tool", "sandbox.run", 101.2),
    finished("t1", "c1", "tool", "sandbox.run", 101.3, 100),
    finished("c1", "p", "check", "program", 101.4, 300),
    finished("p", "r", "stage", "produce", 102, 1000, "error"),
    e("item.delivered", {
      item: { id: "i1", stem: "题", verification: { status: "verified", checks: [] } },
      order: 1,
    }),
    e("item.status", {
      item_id: "i1",
      status: "verified",
      checks: [{ name: "program", status: "pass", detail: "", evidence: { program: ["3"] } }],
    }),
    finished("r", null, "run", "main", 103, 3000),
    e("run.finished", { status: "succeeded", error: null }, 103),
  ];
}

describe("buildModel", () => {
  it("由 span 事件建树，按开始时间排序并标深度", () => {
    const m = buildModel(sample());
    expect(m.roots.map((s) => s.name)).toEqual(["main"]);
    const main = m.roots[0]!;
    expect(main.children.map((s) => s.name)).toEqual(["understand", "produce"]);
    expect(main.children[1]!.children[0]!.children[0]!.name).toBe("sandbox.run");
    expect(main.children[1]!.children[0]!.children[0]!.depth).toBe(3);
    expect(m.spans.get("p")!.state).toBe("error");
    expect(m.spans.get("u")!.durationMs).toBe(900);
    expect(m.spans.get("u")!.attrs).toEqual({ a: 1, b: 2 }); // 开始与结束的属性合并
  });

  it("模型调用挂到它所在的 span 上", () => {
    const m = buildModel(sample());
    expect(m.calls).toHaveLength(1);
    expect(m.spans.get("l1")!.calls[0]!.record.purpose).toBe("understand.parse");
  });

  it("题目证据：送达的题 + 状态变化 + 各检查证据", () => {
    const m = buildModel(sample());
    expect(m.items).toHaveLength(1);
    expect(m.items[0]!.item!.id).toBe("i1");
    expect(m.items[0]!.statuses[0]!.checks[0]!.evidence).toEqual({ program: ["3"] });
  });

  it("运行结束状态与时间范围", () => {
    const m = buildModel(sample());
    expect(m.finished).toBe(true);
    expect(m.status).toBe("succeeded");
    expect(m.t1 - m.t0).toBeCloseTo(3, 5);
  });

  it("只有结束没有开始（续传片段）：用时长反推起点，不崩", () => {
    seq = 0;
    const m = buildModel([finished("z", null, "tool", "orphan", 50, 2000)]);
    expect(m.spans.get("z")!.start).toBeCloseTo(48, 5);
  });

  it("父节点缺失的 span 挂到根", () => {
    seq = 0;
    const m = buildModel([started("c", "missing", "tool", "child", 10)]);
    expect(m.roots.map((s) => s.id)).toEqual(["c"]);
    expect(m.spans.get("c")!.state).toBe("running"); // 还没结束
  });

  it("空事件列表", () => {
    expect(buildModel([]).roots).toEqual([]);
  });
});

describe("折叠", () => {
  it("默认折叠深度 ≥ 2 且有子节点的 span；折叠后子节点不可见", () => {
    const m = buildModel(sample());
    const collapsed = defaultCollapsed(m.roots);
    expect([...collapsed]).toEqual(["c1"]); // produce 下的 program 检查（深度 2）有子节点
    const rows = visibleRows(m.roots, collapsed).map((s) => s.name);
    expect(rows).toEqual(["main", "understand", "fast:understand.parse", "produce", "program"]);
    expect(visibleRows(m.roots, new Set()).map((s) => s.name)).toContain("sandbox.run");
  });
});

describe("layoutGraph", () => {
  const payload = (
    nodes: RetrievalPayload["nodes"],
    edges: RetrievalPayload["edges"] = [],
    combos: RetrievalPayload["combos"] = [],
  ): RetrievalPayload => ({
    step: "s",
    query: "",
    nodes,
    edges,
    combos,
    notes: [],
  });
  const n = (
    id: string,
    grade: number,
    semester: string,
    role: "anchor" | "candidate" | "selected" | "context",
    score = 0.5,
  ) => ({
    id,
    name: id,
    grade,
    semester,
    domain: "",
    role,
    score,
    note: "",
  });
  const step = (p: RetrievalPayload, i: number): RetrievalStep => ({
    seq: i,
    ts: i,
    spanId: null,
    payload: p,
  });

  it("按学期分层：列从早到晚；列内先选中 / 锚点，再按得分", () => {
    const steps = [
      step(
        payload([
          n("a", 4, "b", "candidate", 0.9),
          n("b", 3, "a", "candidate"),
          n("c", 4, "b", "selected", 0.1),
          n("d", 4, "a", "anchor"),
        ]),
        0,
      ),
    ];
    const g = layoutGraph(steps, 0);
    expect(g.columns.map((c) => c.label)).toEqual(["三年级上", "四年级上", "四年级下"]);
    const col2 = g.nodes.filter((x) => x.col === 2).map((x) => x.id);
    expect(col2).toEqual(["c", "a"]); // selected 在前，即使得分更低
    expect(g.nodes.find((x) => x.id === "b")!.x).toBeLessThan(g.nodes.find((x) => x.id === "a")!.x);
  });

  it("逐步累加；同一节点取角色更高的那次；边去重且只保留两端都在的", () => {
    const s1 = step(
      payload(
        [n("a", 3, "a", "candidate"), n("b", 3, "a", "candidate")],
        [{ source: "a", target: "b", type: "prerequisite", weight: 1 }],
      ),
      0,
    );
    const s2 = step(
      payload(
        [n("a", 3, "a", "selected")],
        [
          { source: "a", target: "b", type: "prerequisite", weight: 1 },
          { source: "a", target: "ghost", type: "related", weight: null },
        ],
        [{ kp_ids: ["a", "b"], score: 2, rationale: "r" }],
      ),
      1,
    );
    const g0 = layoutGraph([s1, s2], 0);
    expect(g0.nodes.find((x) => x.id === "a")!.role).toBe("candidate");
    expect(g0.combos).toEqual([]);
    const g1 = layoutGraph([s1, s2], 1);
    expect(g1.nodes.find((x) => x.id === "a")!.role).toBe("selected");
    expect(g1.edges).toHaveLength(1);
    expect(g1.combos).toHaveLength(1);
  });

  it("没有定位信息的节点放进“未定位”列，不崩", () => {
    const g = layoutGraph(
      [
        step(
          payload([
            {
              id: "x",
              name: "x",
              grade: null,
              semester: null,
              domain: "",
              role: "context",
              score: null,
              note: "",
            },
          ]),
          0,
        ),
      ],
      0,
    );
    expect(g.columns[0]!.label).toBe("未定位");
  });
});

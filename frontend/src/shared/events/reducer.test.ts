import { describe, expect, it } from "vitest";
import type { AppEvent, Item } from "../api/types";
import { foldEvents, initialRun, isActive, reduceEvent } from "./reducer";

let seq = 0;
function ev<T extends AppEvent["type"]>(
  type: T,
  data: Omit<
    Extract<AppEvent, { type: T }>,
    "type" | "seq" | "run_id" | "ts" | "span_id" | "parent_id" | "visibility"
  >,
  s?: number,
): AppEvent {
  seq = s ?? seq + 1;
  return {
    type,
    seq,
    run_id: "run_1",
    ts: 1000 + seq,
    span_id: null,
    parent_id: null,
    visibility: "user",
    ...data,
  } as unknown as AppEvent;
}

const item = (id: string, status: Item["verification"]["status"] = "verified"): Item =>
  ({
    id,
    kind: "fill",
    stem: `题 ${id}`,
    options: [],
    answer: "1",
    answer_value: null,
    solution: "",
    kp_ids: [],
    difficulty: 3,
    tier: "consolidate",
    score: null,
    figures: [],
    provenance: {
      source: "novel",
      archetype_ids: [],
      reference_ids: [],
      run_id: null,
      model: null,
    },
    verification: { status, checks: [] },
    rev: 1,
  }) as Item;

function stream(): AppEvent[] {
  seq = 0;
  return [
    ev("run.started", { session_id: "ses_1", pipeline: "classic", schema_version: "1.2" }),
    ev("progress", { label: "正在理解您的需求", current: null, total: null }),
    ev("progress", { label: "开始逐题创作并核验", current: 0, total: 3 }),
    ev("item.delivered", { item: item("a", "pending"), order: 1 }),
    ev("item.status", { item_id: "a", status: "verified", checks: [] }),
    ev("progress", { label: "已完成 1/3 道题的核验", current: 1, total: 3 }),
    ev("item.delivered", { item: item("b"), order: 2 }),
    ev("progress", { label: "已完成 2/3 道题的核验", current: 2, total: 3 }),
    ev("paper.patch", { paper_id: "p1", rev: 1, ops: [], summary: "生成 2 道题" }),
    ev("message.done", { message_id: "m1", text: "已为您出好 2 道题。" }),
    ev("run.finished", { status: "succeeded", error: null }),
  ];
}

describe("reduceEvent", () => {
  it("折叠一次完整运行", () => {
    const s = foldEvents(stream());
    expect(s.phase).toBe("succeeded");
    expect(s.pipeline).toBe("classic");
    expect(s.delivered.map((i) => i.id)).toEqual(["a", "b"]);
    expect(s.delivered[0]!.verification.status).toBe("verified"); // item.status 更新了送达的题
    expect(s.paperRev).toBe(1);
    expect(s.reply).toEqual({ messageId: "m1", text: "已为您出好 2 道题。", done: true });
    expect(isActive(s)).toBe(false);
  });

  it("进度计数变化不新增步骤：同一步的 x/y 覆盖", () => {
    const s = foldEvents(stream());
    expect(s.steps.map((x) => x.label)).toEqual([
      "正在理解您的需求",
      "开始逐题创作并核验",
      "已完成 2/3 道题的核验", // 1/3 与 2/3 是同一步（数字不同），保留最新
    ]);
  });

  it("重复事件被忽略：分段送入与一次送入结果相同（断线续传）", () => {
    const all = stream();
    const once = foldEvents(all);
    const first = foldEvents(all.slice(0, 5));
    const resumed = foldEvents(all.slice(3), first); // 续传时服务端可能多发已见过的事件
    expect(resumed).toEqual(once);
  });

  it("检查点：暂停，之后任何事件说明已继续", () => {
    seq = 0;
    let s = initialRun("run_1");
    s = reduceEvent(
      s,
      ev("run.started", { session_id: "s", pipeline: "classic", schema_version: "1.2" }),
    );
    s = reduceEvent(
      s,
      ev("checkpoint.requested", {
        checkpoint_id: "cp1",
        kind: "clarify",
        prompt: "几年级？",
        options: [{ id: "g4", label: "四年级" }],
        payload: {},
      }),
    );
    s = reduceEvent(s, ev("run.paused", { checkpoint_id: "cp1" }));
    expect(s.phase).toBe("awaiting_user");
    expect(s.checkpoint?.options).toEqual([{ id: "g4", label: "四年级" }]);
    expect(isActive(s)).toBe(true);
    s = reduceEvent(s, ev("progress", { label: "继续", current: null, total: null }));
    expect(s.phase).toBe("running");
    expect(s.checkpoint).toBeNull();
  });

  it("message.delta 累加，message.done 定稿", () => {
    seq = 0;
    let s = initialRun("run_1");
    s = reduceEvent(s, ev("message.delta", { message_id: "m", text: "你" }));
    s = reduceEvent(s, ev("message.delta", { message_id: "m", text: "好" }));
    expect(s.reply).toEqual({ messageId: "m", text: "你好", done: false });
    s = reduceEvent(s, ev("message.done", { message_id: "m", text: "你好！" }));
    expect(s.reply).toEqual({ messageId: "m", text: "你好！", done: true });
  });

  it("失败与取消", () => {
    seq = 0;
    const failed = foldEvents([
      ev("run.started", { session_id: "s", pipeline: "classic", schema_version: "1.2" }),
      ev("run.finished", {
        status: "failed",
        error: {
          code: "llm_error",
          message: "x",
          retryable: true,
          user_message: "模型暂时不可用",
          details: {},
        },
      }),
    ]);
    expect(failed.phase).toBe("failed");
    expect(failed.error?.user_message).toBe("模型暂时不可用");
    seq = 0;
    const cancelled = foldEvents([
      ev("run.started", { session_id: "s", pipeline: "classic", schema_version: "1.2" }),
      ev("run.finished", { status: "cancelled", error: null }),
    ]);
    expect(cancelled.phase).toBe("cancelled");
  });

  it("调试类事件不改变用户端状态", () => {
    seq = 0;
    const s0 = initialRun("run_1");
    const s1 = reduceEvent(
      s0,
      ev("span.started", { kind: "stage", name: "plan", attrs: {} }) as AppEvent,
    );
    expect(s1).toEqual({ ...s0, lastSeq: s1.lastSeq });
  });
});

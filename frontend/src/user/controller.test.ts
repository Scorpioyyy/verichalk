import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AppEvent, Message, Paper, SessionState } from "@/shared/api/types";

const api = vi.hoisted(() => ({
  createSession: vi.fn(),
  getSession: vi.fn(),
  postTurn: vi.fn(),
  answerCheckpoint: vi.fn(),
  cancelRun: vi.fn(),
  patchPaper: vi.fn(),
  undo: vi.fn(),
  redo: vi.fn(),
  restore: vi.fn(),
  history: vi.fn(),
  renameSession: vi.fn(),
  deleteSession: vi.fn(),
}));
const sse = vi.hoisted(() => ({
  streams: [] as { url: string; onEvent: (e: AppEvent) => void; resolve: () => void }[],
}));

vi.mock("@/shared/api/client", () => {
  class ApiError extends Error {
    constructor(
      public status: number,
      public code: string,
      message: string,
      public userMessage: string,
    ) {
      super(message);
    }
  }
  return { api, ApiError };
});
vi.mock("@/shared/api/sse", () => ({
  streamEvents: (url: string, opts: { onEvent: (e: AppEvent) => void }) =>
    new Promise<number>((resolve) => {
      sse.streams.push({ url, onEvent: opts.onEvent, resolve: () => resolve(0) });
    }),
  fetchRunEvents: vi.fn().mockResolvedValue([]),
}));

import { ApiError } from "@/shared/api/client";
import { SessionController } from "./controller";

let seq = 0;
const ev = (type: string, data: object = {}): AppEvent =>
  ({
    type,
    seq: ++seq,
    run_id: "run_1",
    ts: 1000 + seq,
    visibility: "user",
    ...data,
  }) as unknown as AppEvent;

const paperOf = (rev: number, itemRev = 1): Paper =>
  ({
    id: "p1",
    title: "练习",
    rev,
    meta: {},
    sections: [
      {
        id: "s1",
        title: "练习题",
        kind: "mixed",
        items: [
          {
            id: "i1",
            kind: "fill",
            stem: "题",
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
            verification: { status: "verified", checks: [] },
            rev: itemRev,
          },
        ],
      },
    ],
  }) as Paper;

const state = (over: Partial<SessionState> = {}): SessionState =>
  ({
    session: { id: "ses_1", created_at: 0, updated_at: 0, title: "" },
    messages: [],
    paper: null,
    active_run_id: null,
    ...over,
  }) as SessionState;

const flush = () => new Promise((r) => setTimeout(r, 0));

beforeEach(() => {
  seq = 0;
  sse.streams.length = 0;
  localStorage.clear();
  for (const f of Object.values(api)) f.mockReset();
  api.createSession.mockResolvedValue(state());
  api.getSession.mockResolvedValue(state());
  api.history.mockResolvedValue({ revisions: [], head_rev: 1, can_undo: false, can_redo: false });
  api.postTurn.mockResolvedValue({ session_id: "ses_1", run_id: "run_1", message_id: "m1" });
});

describe("历史对话：改名与删除", () => {
  const seed = () =>
    localStorage.setItem(
      "verichalk.sessions",
      JSON.stringify([
        { id: "ses_1", title: "旧标题", ts: 2 },
        { id: "ses_9", title: "别的对话", ts: 1 },
      ]),
    );

  it("改名：服务端保存，本地列表同步，顺序不变", async () => {
    seed();
    api.renameSession.mockResolvedValue({ id: "ses_9", title: "新标题" });
    const c = new SessionController();
    await c.renameSession("ses_9", "新标题");
    expect(api.renameSession).toHaveBeenCalledWith("ses_9", "新标题");
    expect(c.getSnapshot().recent.map((r) => r.title)).toEqual(["旧标题", "新标题"]);
  });

  it("删除别的对话：只从列表去掉；删除当前对话：回到主页面", async () => {
    seed();
    api.deleteSession.mockResolvedValue(undefined);
    const c = new SessionController();
    await c.openSession("ses_1");
    await c.deleteSession("ses_9");
    expect(c.getSnapshot().recent.map((r) => r.id)).toEqual(["ses_1"]);
    expect(c.getSnapshot().sessionId).toBe("ses_1");
    await c.deleteSession("ses_1");
    expect(c.getSnapshot().recent).toEqual([]);
    expect(c.getSnapshot().sessionId).toBeNull();
    expect(c.getSnapshot().messages).toEqual([]);
  });

  it("服务端拒绝（还有任务在进行）：列表保持不变并提示原因；对话已不存在（404）：照常去掉", async () => {
    seed();
    const c = new SessionController();
    api.deleteSession.mockRejectedValueOnce(
      new ApiError(409, "conflict", "busy", "请先停止，再删除。"),
    );
    await c.deleteSession("ses_9");
    expect(c.getSnapshot().recent).toHaveLength(2);
    expect(c.getSnapshot().toasts.at(-1)?.text).toBe("请先停止，再删除。");
    api.deleteSession.mockRejectedValueOnce(new ApiError(404, "not_found", "gone", "没有找到。"));
    await c.deleteSession("ses_9");
    expect(c.getSnapshot().recent.map((r) => r.id)).toEqual(["ses_1"]);
  });
});

describe("发送一轮需求", () => {
  it("立即给出反馈（不等网络），成功后订阅运行事件", async () => {
    let release!: () => void;
    api.createSession.mockReturnValue(new Promise((r) => (release = () => r(state()))));
    const c = new SessionController();
    const p = c.send("四年级小数加减法");
    // 网络还没返回：界面已经显示"已收到"
    const s = c.getSnapshot();
    expect(s.pendingUser).toBe("四年级小数加减法");
    expect(s.run?.progress?.label).toBe("已收到，正在开始");
    release();
    expect(await p).toBe(true);
    expect(api.postTurn).toHaveBeenCalledWith("ses_1", "四年级小数加减法", []);
    expect(sse.streams[0]!.url).toBe("/api/runs/run_1/events");
    expect(c.getSnapshot().run?.runId).toBe("run_1");
    expect(JSON.parse(localStorage.getItem("verichalk.session")!)).toBe("ses_1");
  });

  it("只发照片、不写字也能发送；识别结果随事件进入各轮的卡片", async () => {
    globalThis.URL.createObjectURL = vi.fn(() => "blob:preview");
    globalThis.URL.revokeObjectURL = vi.fn();
    const file = new File([new Uint8Array(4)], "page.jpg", { type: "image/jpeg" });
    const c = new SessionController();
    expect(await c.send("", [file])).toBe(true);
    expect(api.postTurn).toHaveBeenCalledWith("ses_1", "", [file]);
    const mid = c.getSnapshot();
    expect(mid.pendingUser).toBe("");
    expect(mid.pendingPhotos).toEqual(["blob:preview"]);
    const refs = { pages: [], context: {}, usable: true, message: "", needs_confirm: false };
    sse.streams[0]!.onEvent(ev("perception.ready", { references: refs }));
    expect(c.getSnapshot().perceptions["run_1"]).toBe(refs);
    expect(c.getSnapshot().run?.perception).toBe(refs);
  });

  it("没有文字也没有照片：不发送", async () => {
    const c = new SessionController();
    expect(await c.send("  ", [])).toBe(false);
    expect(api.postTurn).not.toHaveBeenCalled();
  });

  it("事件驱动状态：进度、逐题送达、完成后对账试卷与消息", async () => {
    const c = new SessionController();
    await c.send("出 2 道题");
    const { onEvent } = sse.streams[0]!;
    onEvent(ev("run.started", { session_id: "ses_1", pipeline: "classic", schema_version: "1.2" }));
    onEvent(ev("progress", { label: "正在构思", current: null, total: null }));
    expect(c.getSnapshot().run?.progress?.label).toBe("正在构思");

    const item = paperOf(1).sections[0]!.items[0]!;
    onEvent(ev("item.delivered", { item, order: 1 }));
    expect(c.getSnapshot().run?.delivered).toHaveLength(1);

    const msgs: Message[] = [
      {
        id: "m1",
        session_id: "ses_1",
        role: "user",
        content: "出 2 道题",
        attachments: [],
        run_id: "run_1",
        ts: 1,
      },
      {
        id: "m2",
        session_id: "ses_1",
        role: "assistant",
        content: "好了",
        attachments: [],
        run_id: "run_1",
        ts: 2,
      },
    ];
    api.getSession.mockResolvedValue(state({ messages: msgs, paper: paperOf(1) }));
    onEvent(ev("paper.patch", { paper_id: "p1", rev: 1, ops: [], summary: "生成" }));
    onEvent(ev("run.finished", { status: "succeeded", error: null }));
    sse.streams[0]!.resolve();
    await flush();
    await flush();
    const s = c.getSnapshot();
    expect(s.run?.phase).toBe("succeeded");
    expect(s.paper?.rev).toBe(1);
    expect(s.messages).toHaveLength(2);
    expect(s.pendingUser).toBeNull(); // 服务端回显后，临时消息撤掉
  });

  it("失败：返回 false，给出教师能懂的提示，不留半截状态", async () => {
    api.postTurn.mockRejectedValue(new ApiError(409, "conflict", "x", "上一个任务还在进行"));
    const c = new SessionController();
    expect(await c.send("出题")).toBe(false);
    const s = c.getSnapshot();
    expect(s.pendingUser).toBeNull();
    expect(s.run).toBeNull();
    expect(s.toasts[0]).toMatchObject({ kind: "error", text: "上一个任务还在进行" });
  });

  it("上一轮还在进行时不重复提交", async () => {
    const c = new SessionController();
    await c.send("第一轮");
    api.postTurn.mockClear();
    expect(await c.send("第二轮")).toBe(false);
    expect(api.postTurn).not.toHaveBeenCalled();
    expect(c.getSnapshot().toasts.some((t) => t.text.includes("还在进行"))).toBe(true);
  });

  it("空白输入不发送", async () => {
    const c = new SessionController();
    expect(await c.send("   ")).toBe(false);
    expect(api.createSession).not.toHaveBeenCalled();
  });
});

describe("检查点", () => {
  it("回答检查点：立即恢复为进行中，并把回答交给后端", async () => {
    api.answerCheckpoint.mockResolvedValue({ accepted: true });
    const c = new SessionController();
    await c.send("出题");
    const { onEvent } = sse.streams[0]!;
    onEvent(
      ev("checkpoint.requested", {
        checkpoint_id: "cp1",
        kind: "clarify",
        prompt: "几年级？",
        options: [{ id: "g4", label: "四年级" }],
        payload: {},
      }),
    );
    onEvent(ev("run.paused", { checkpoint_id: "cp1" }));
    expect(c.getSnapshot().run?.phase).toBe("awaiting_user");
    await c.answer({ option: "g4" });
    expect(c.getSnapshot().run?.phase).toBe("running");
    expect(api.answerCheckpoint).toHaveBeenCalledWith("run_1", { option: "g4" });
  });
});

describe("手动编辑", () => {
  async function withPaper() {
    api.getSession.mockResolvedValue(state({ paper: paperOf(1), messages: [] }));
    localStorage.setItem("verichalk.session", JSON.stringify("ses_1"));
    const c = new SessionController();
    await c.init();
    return c;
  }

  it("保存成功：更新试卷与撤销状态，并订阅后台复核", async () => {
    const c = await withPaper();
    api.patchPaper.mockResolvedValue({
      paper: paperOf(2, 2),
      revision: { rev: 2 },
      warnings: [],
      review_run_id: "run_review",
      can_undo: true,
      can_redo: false,
    });
    const ok = await c.editOps([
      { op: "replace_field", item_id: "i1", field: "stem", value: "新题干" },
    ]);
    expect(ok).toBe(true);
    expect(api.patchPaper).toHaveBeenCalledWith("ses_1", expect.any(Array), 1);
    const s = c.getSnapshot();
    expect(s.paper?.rev).toBe(2);
    expect(s.history).toMatchObject({ canUndo: true, canRedo: false });
    expect(s.reviewing).toEqual(["i1"]);
    expect("i1" in s.changed).toBe(true); // 题的版本变了：界面高亮
    // 复核结果到达：不再显示"正在核验"
    const review = sse.streams.find((x) => x.url.includes("run_review"))!;
    review.onEvent(ev("item.status", { item_id: "i1", status: "verified", checks: [] }));
    expect(c.getSnapshot().reviewing).toEqual([]);
  });

  it("版本冲突：提示并重新取最新试卷", async () => {
    const c = await withPaper();
    api.patchPaper.mockRejectedValue(
      new ApiError(409, "conflict", "x", "试卷已经有了新的修改，请刷新后再改。"),
    );
    api.getSession.mockResolvedValue(state({ paper: paperOf(5) }));
    expect(await c.editOps([{ op: "set_title", title: "新标题" }])).toBe(false);
    await flush();
    expect(c.getSnapshot().toasts[0]!.text).toContain("新的修改");
    expect(c.getSnapshot().paper?.rev).toBe(5);
  });

  it("撤销：更新试卷并提示", async () => {
    const c = await withPaper();
    api.undo.mockResolvedValue({
      paper: paperOf(3),
      revision: { rev: 3 },
      warnings: [],
      review_run_id: null,
      can_undo: false,
      can_redo: true,
    });
    await c.undo();
    expect(c.getSnapshot().history.canRedo).toBe(true);
    expect(c.getSnapshot().toasts.some((t) => t.text === "已撤销")).toBe(true);
  });
});

describe("会话", () => {
  it("刷新页面：恢复上次的会话；有进行中的运行就接着订阅", async () => {
    localStorage.setItem("verichalk.session", JSON.stringify("ses_1"));
    api.getSession.mockResolvedValue(state({ active_run_id: "run_9" }));
    const c = new SessionController();
    await c.init();
    expect(c.getSnapshot().sessionId).toBe("ses_1");
    expect(sse.streams[0]!.url).toBe("/api/runs/run_9/events");
  });

  it("记住的会话已不存在：当作新用户，不报错", async () => {
    localStorage.setItem("verichalk.session", JSON.stringify("ses_gone"));
    api.getSession.mockRejectedValue(new ApiError(404, "not_found", "x", "没有这个会话"));
    const c = new SessionController();
    await c.init();
    expect(c.getSnapshot()).toMatchObject({ ready: true, sessionId: null });
    expect(localStorage.getItem("verichalk.session")).toBeNull();
  });

  it("新对话清空当前会话但保留历史列表", async () => {
    const c = new SessionController();
    await c.send("出题");
    await c.newSession();
    const s = c.getSnapshot();
    expect(s.sessionId).toBeNull();
    expect(s.run).toBeNull();
    expect(s.recent.map((r) => r.id)).toEqual(["ses_1"]);
  });
});

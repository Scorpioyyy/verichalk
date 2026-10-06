/**
 * 事件 → 运行状态的纯函数（D23）。实时订阅与回放同一套代码；调试台与用户端都用它。
 *
 * 约定：
 * - 对 `seq <= lastSeq` 的事件视为重复（断线续传时可能重发）并忽略，因此 `fold` 对"分段送入"与"一次送入"结果相同；
 * - 不在这里重新实现试卷补丁：试卷内容由服务端持有，`paper.patch` 只告诉界面"试卷已更新到第几版"（`paperRev`），
 *   界面据此去取最新试卷；本状态负责的是进度、检查点、回复、逐题送达与核验状态。
 */
import type {
  AppEvent,
  ErrorInfo,
  Item,
  ReferenceSet,
  Understanding,
  VerifyStatus,
  CheckResult,
} from "../api/types";

export type RunPhase = "running" | "awaiting_user" | "succeeded" | "failed" | "cancelled";

export interface ProgressStep {
  label: string;
  current: number | null;
  total: number | null;
  ts: number;
}

export interface CheckpointInfo {
  id: string;
  kind: "clarify" | "blueprint" | "samples" | "confirm" | "perception";
  prompt: string;
  options: { id: string; label: string }[];
  payload: Record<string, unknown>;
}

export interface ItemStatusInfo {
  status: VerifyStatus;
  checks: CheckResult[];
}

export interface RunState {
  runId: string;
  sessionId: string;
  pipeline: string;
  phase: RunPhase;
  startedAt: number | null; // 毫秒
  finishedAt: number | null;
  progress: ProgressStep | null;
  steps: ProgressStep[]; // 去重后的进度轨迹（同一步的 current/total 更新会覆盖，不新增）
  understanding: Understanding | null;
  perception: ReferenceSet | null; // 照片识别结果（教师确认修改后会被新的版本取代）
  checkpoint: CheckpointInfo | null;
  reply: { messageId: string; text: string; done: boolean } | null;
  delivered: Item[]; // 按送达顺序；核验状态随 item.status 更新
  itemStatus: Record<string, ItemStatusInfo>;
  paperRev: number | null; // 最近一次 paper.patch 的版本
  patches: { rev: number; summary: string }[];
  error: ErrorInfo | null;
  lastSeq: number;
}

export function initialRun(runId: string, sessionId = ""): RunState {
  return {
    runId,
    sessionId,
    pipeline: "",
    phase: "running",
    startedAt: null,
    finishedAt: null,
    progress: null,
    steps: [],
    understanding: null,
    perception: null,
    checkpoint: null,
    reply: null,
    delivered: [],
    itemStatus: {},
    paperRev: null,
    patches: [],
    error: null,
    lastSeq: 0,
  };
}

/** 进度标签里"x/y"这类计数不该成为新的一步：去掉数字后相同就视为同一步。 */
function sameStep(a: string, b: string): boolean {
  const norm = (s: string) => s.replace(/\d+/g, "#");
  return norm(a) === norm(b);
}

export function reduceEvent(state: RunState, e: AppEvent): RunState {
  if (e.seq <= state.lastSeq) return state;
  let s: RunState = { ...state, lastSeq: e.seq };
  // 检查点之后出现任何别的事件，说明用户已经回答、运行继续了
  if (
    s.phase === "awaiting_user" &&
    e.type !== "checkpoint.requested" &&
    e.type !== "run.paused" &&
    e.type !== "run.finished"
  ) {
    s = { ...s, phase: "running", checkpoint: null };
  }
  switch (e.type) {
    case "run.started":
      return { ...s, sessionId: e.session_id, pipeline: e.pipeline, startedAt: e.ts * 1000 };
    case "progress": {
      const step: ProgressStep = {
        label: e.label,
        current: e.current ?? null,
        total: e.total ?? null,
        ts: e.ts * 1000,
      };
      const last = s.steps[s.steps.length - 1];
      const steps =
        last && sameStep(last.label, step.label)
          ? [...s.steps.slice(0, -1), step]
          : [...s.steps, step];
      return { ...s, progress: step, steps };
    }
    case "understanding.ready":
      return { ...s, understanding: e.understanding };
    case "perception.ready":
      return { ...s, perception: e.references };
    case "checkpoint.requested":
      return {
        ...s,
        phase: "awaiting_user",
        checkpoint: {
          id: e.checkpoint_id,
          kind: e.kind,
          prompt: e.prompt,
          options: e.options.map((o) => ({ id: String(o.id ?? ""), label: String(o.label ?? "") })),
          payload: e.payload,
        },
      };
    case "run.paused":
      return { ...s, phase: "awaiting_user" };
    case "message.delta": {
      const cur = s.reply && s.reply.messageId === e.message_id ? s.reply : null;
      return {
        ...s,
        reply: { messageId: e.message_id, text: (cur?.text ?? "") + e.text, done: false },
      };
    }
    case "message.done":
      return { ...s, reply: { messageId: e.message_id, text: e.text, done: true } };
    case "item.delivered": {
      const rest = s.delivered.filter((x) => x.id !== e.item.id);
      return { ...s, delivered: [...rest, e.item] };
    }
    case "item.status": {
      const info: ItemStatusInfo = { status: e.status, checks: e.checks };
      return {
        ...s,
        itemStatus: { ...s.itemStatus, [e.item_id]: info },
        delivered: s.delivered.map((it) =>
          it.id === e.item_id
            ? { ...it, verification: { status: e.status, checks: e.checks } }
            : it,
        ),
      };
    }
    case "paper.patch":
      return {
        ...s,
        paperRev: e.rev,
        patches: [...s.patches, { rev: e.rev, summary: e.summary }],
      };
    case "run.finished":
      return {
        ...s,
        phase: e.status,
        finishedAt: e.ts * 1000,
        checkpoint: null,
        error: e.error,
      };
    default:
      return s; // span / llm.call / retrieval.result / usage.update：用户端不关心
  }
}

/** 恢复"检查点已回答、运行继续"的状态（回答由用户端本地触发，服务端不会再发事件说明这一点）。 */
export function resumed(state: RunState): RunState {
  return state.phase === "awaiting_user" ? { ...state, phase: "running", checkpoint: null } : state;
}

export function foldEvents(events: AppEvent[], initial?: RunState): RunState {
  const first = events[0];
  let s = initial ?? initialRun(first?.run_id ?? "");
  for (const e of events) s = reduceEvent(s, e);
  return s;
}

export function isActive(s: RunState | null | undefined): boolean {
  return !!s && (s.phase === "running" || s.phase === "awaiting_user");
}

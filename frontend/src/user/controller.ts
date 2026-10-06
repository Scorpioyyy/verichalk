/**
 * 用户端的会话控制器：一个不依赖 React 的小型状态机，持有"会话 / 试卷 / 当前运行"的全部状态。
 *
 * 为什么不放进组件：①可以脱离界面做单元测试（mock fetch 与事件流）；②实时订阅、断线续传、
 * 手改后的复核运行、撤销 / 重做都是跨组件的流程，放在一处才不会各写一遍。
 * 运行状态由 `shared/events` 的 reducer 计算（D23）；试卷内容以服务端为准，收到 `paper.patch` / `item.status` 就去取最新的。
 */
import { api, ApiError } from "@/shared/api/client";
import { fetchRunEvents, streamEvents } from "@/shared/api/sse";
import type { AppEvent, Message, Op, Paper, PaperHistory, Understanding } from "@/shared/api/types";
import { foldEvents, initialRun, isActive, reduceEvent, resumed } from "@/shared/events/reducer";
import type { RunState } from "@/shared/events/reducer";

const LS_CURRENT = "verichalk.session";
const LS_RECENT = "verichalk.sessions";

export interface RecentSession {
  id: string;
  title: string;
  ts: number;
}

export interface Toast {
  id: number;
  kind: "info" | "error";
  text: string;
  action?: { label: string; run: () => void };
}

export interface LastEdit {
  summary: string;
  fromRev: number;
  toRev: number;
}

export interface ViewState {
  ready: boolean;
  sessionId: string | null;
  messages: Message[];
  pendingUser: string | null; // 已发出、服务端还没回显的用户消息
  paper: Paper | null;
  run: RunState | null; // 当前（或最近一次）对话运行
  reviewing: string[]; // 正在后台复核的题 id
  history: { canUndo: boolean; canRedo: boolean; headRev: number };
  conn: "open" | "reconnecting";
  understanding: Understanding | null; // 最近一次出题 / 整卷的需求理解（"本次假设"芯片）
  changed: Record<string, number>; // 题 id → 刚被修改的时间戳（界面短暂高亮）
  lastEdit: LastEdit | null;
  toasts: Toast[];
  recent: RecentSession[];
}

const EMPTY_HISTORY = { canUndo: false, canRedo: false, headRev: 0 };

function readLS<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function writeLS(key: string, value: unknown): void {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* 隐私模式：本次页面内仍可用，只是刷新后不记得 */
  }
}

export function userMessageOf(e: unknown): string {
  if (e instanceof ApiError) return e.userMessage;
  return "出了点小问题，请稍后再试。";
}

export class SessionController {
  private state: ViewState;
  private listeners = new Set<() => void>();
  private streamAbort: AbortController | null = null;
  private refreshing: Promise<void> | null = null;
  private refreshAgain = false;
  private toastSeq = 0;
  private timers = new Set<ReturnType<typeof setTimeout>>();

  constructor() {
    this.state = {
      ready: false,
      sessionId: null,
      messages: [],
      pendingUser: null,
      paper: null,
      run: null,
      reviewing: [],
      history: EMPTY_HISTORY,
      conn: "open",
      understanding: null,
      changed: {},
      lastEdit: null,
      toasts: [],
      recent: readLS<RecentSession[]>(LS_RECENT, []),
    };
  }

  // ---- 订阅（给 useSyncExternalStore） ----
  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };
  getSnapshot = (): ViewState => this.state;

  private set(patch: Partial<ViewState>): void {
    this.state = { ...this.state, ...patch };
    for (const fn of this.listeners) fn();
  }

  dispose(): void {
    this.streamAbort?.abort();
    for (const t of this.timers) clearTimeout(t);
    this.timers.clear();
  }

  // ---- 提示条 ----
  toast(text: string, kind: Toast["kind"] = "info", action?: Toast["action"], ms = 6000): void {
    const id = ++this.toastSeq;
    this.set({ toasts: [...this.state.toasts, { id, kind, text, action }] });
    const t = setTimeout(() => this.dismissToast(id), ms);
    this.timers.add(t);
  }
  dismissToast = (id: number): void => {
    this.set({ toasts: this.state.toasts.filter((t) => t.id !== id) });
  };

  // ---- 会话 ----
  async init(): Promise<void> {
    const id = readLS<string | null>(LS_CURRENT, null);
    if (id) {
      try {
        await this.openSession(id);
        return;
      } catch {
        writeLS(LS_CURRENT, null); // 会话已不存在（数据被清理）：当作新用户
      }
    }
    this.set({ ready: true });
  }

  /** 打开一个已有会话：取消息与试卷；若有进行中的运行就接着订阅；恢复"本次假设"。 */
  async openSession(id: string): Promise<void> {
    this.streamAbort?.abort();
    const s = await api.getSession(id);
    writeLS(LS_CURRENT, id);
    this.set({
      ready: true,
      sessionId: id,
      messages: s.messages,
      pendingUser: null,
      paper: s.paper,
      run: null,
      reviewing: [],
      understanding: null,
      changed: {},
      lastEdit: null,
      history: EMPTY_HISTORY,
    });
    this.touchRecent(id, s.session.title);
    if (s.paper) void this.loadHistory();
    if (s.active_run_id) {
      this.attach(s.active_run_id);
    } else {
      void this.restoreUnderstanding(s.messages);
    }
  }

  async newSession(): Promise<void> {
    this.streamAbort?.abort();
    writeLS(LS_CURRENT, null);
    this.set({
      sessionId: null,
      messages: [],
      pendingUser: null,
      paper: null,
      run: null,
      reviewing: [],
      understanding: null,
      changed: {},
      lastEdit: null,
      history: EMPTY_HISTORY,
    });
  }

  private touchRecent(id: string, title: string): void {
    const rest = this.state.recent.filter((r) => r.id !== id);
    const cur = this.state.recent.find((r) => r.id === id);
    const recent = [{ id, title: title || cur?.title || "新对话", ts: Date.now() }, ...rest].slice(
      0,
      20,
    );
    writeLS(LS_RECENT, recent);
    this.set({ recent });
  }

  private async restoreUnderstanding(messages: Message[]): Promise<void> {
    const runIds = [
      ...new Set(messages.map((m) => m.run_id).filter((x): x is string => !!x)),
    ].reverse();
    for (const rid of runIds.slice(0, 4)) {
      try {
        const st = foldEvents(await fetchRunEvents(rid));
        const u = st.understanding;
        if (u && (u.route === "generate" || u.route === "paper") && u.chips.length) {
          if (!this.state.understanding) this.set({ understanding: u });
          return;
        }
      } catch {
        return;
      }
    }
  }

  // ---- 发送与运行 ----
  /** 发送一轮需求。立即给出反馈（≤1s），成功返回 true；失败返回 false（调用方把文字放回输入框）。 */
  async send(text: string): Promise<boolean> {
    const t = text.trim();
    if (!t) return false;
    if (isActive(this.state.run)) {
      this.toast("上一个任务还在进行，请等它完成，或先点“停止”。", "error");
      return false;
    }
    const now = Date.now();
    const starting = initialRun("pending", this.state.sessionId ?? "");
    starting.startedAt = now;
    starting.progress = { label: "已收到，正在开始", current: null, total: null, ts: now };
    starting.steps = [starting.progress];
    this.set({ pendingUser: t, run: starting });
    try {
      let sid = this.state.sessionId;
      if (!sid) {
        const created = await api.createSession();
        sid = created.session.id;
        writeLS(LS_CURRENT, sid);
        this.set({ sessionId: sid });
      }
      const acc = await api.postTurn(sid, t);
      this.touchRecent(sid, this.state.recent.find((r) => r.id === sid)?.title ?? t.slice(0, 24));
      this.attach(acc.run_id, starting);
      return true;
    } catch (e) {
      this.set({ pendingUser: null, run: null });
      this.toast(userMessageOf(e), "error");
      return false;
    }
  }

  async answer(answer: Record<string, unknown>): Promise<void> {
    const run = this.state.run;
    if (!run || run.phase !== "awaiting_user") return;
    this.set({ run: resumed(run) });
    try {
      await api.answerCheckpoint(run.runId, answer);
    } catch (e) {
      this.set({ run });
      this.toast(userMessageOf(e), "error");
    }
  }

  async cancel(): Promise<void> {
    const run = this.state.run;
    if (!run || !isActive(run)) return;
    try {
      await api.cancelRun(run.runId);
    } catch (e) {
      this.toast(userMessageOf(e), "error");
    }
  }

  private attach(runId: string, base?: RunState): void {
    this.streamAbort?.abort();
    const ctl = new AbortController();
    this.streamAbort = ctl;
    const start = base ? { ...base, runId } : initialRun(runId, this.state.sessionId ?? "");
    this.set({ run: start });
    streamEvents(`/api/runs/${runId}/events`, {
      signal: ctl.signal,
      onEvent: (e) => this.onRunEvent(runId, e),
      onState: (s) => this.set({ conn: s }),
    })
      .catch(() => {
        if (ctl.signal.aborted) return;
        this.toast("和服务器的连接断开了，请刷新页面查看最新进展。", "error");
      })
      .finally(() => {
        if (!ctl.signal.aborted) void this.settle();
      });
  }

  private onRunEvent(runId: string, e: AppEvent): void {
    const cur = this.state.run;
    if (!cur || cur.runId !== runId) return;
    const next = reduceEvent(cur, e);
    this.set({ run: next, understanding: this.pickUnderstanding(next) });
    if (e.type === "paper.patch" || e.type === "item.status") void this.refresh();
    if (e.type === "run.finished") void this.onFinished(next);
  }

  private pickUnderstanding(run: RunState): Understanding | null {
    const u = run.understanding;
    if (u && (u.route === "generate" || u.route === "paper") && u.chips.length) return u;
    return this.state.understanding;
  }

  private async onFinished(run: RunState): Promise<void> {
    await this.settle();
    if (run.phase === "failed") {
      this.toast(run.error?.user_message || "这次没有完成，请重试。", "error");
    }
    const route = run.understanding?.route;
    if (run.phase === "succeeded" && route === "edit" && run.patches.length) {
      const toRev = run.patches[run.patches.length - 1]!.rev;
      const fromRev = run.patches[0]!.rev - 1;
      const summary = run.patches[run.patches.length - 1]!.summary || "已按要求修改";
      this.set({ lastEdit: { summary, fromRev, toRev } });
    }
  }

  /** 运行结束后统一对账：取最新消息、试卷、历史。 */
  private async settle(): Promise<void> {
    await this.refresh();
    await this.loadHistory();
  }

  // ---- 取最新状态（合并并发请求） ----
  refresh(): Promise<void> {
    if (this.refreshing) {
      this.refreshAgain = true;
      return this.refreshing;
    }
    const sid = this.state.sessionId;
    if (!sid) return Promise.resolve();
    this.refreshing = (async () => {
      try {
        do {
          this.refreshAgain = false;
          const s = await api.getSession(sid);
          if (this.state.sessionId !== sid) return;
          this.applyServer(s.messages, s.paper, s.session.title);
        } while (this.refreshAgain);
      } catch {
        /* 下一次事件或结束时会再对账 */
      } finally {
        this.refreshing = null;
      }
    })();
    return this.refreshing;
  }

  private applyServer(messages: Message[], paper: Paper | null, title: string): void {
    const prev = this.state.paper;
    const changed = { ...this.state.changed };
    if (prev && paper) {
      const before = new Map(prev.sections.flatMap((s) => s.items).map((it) => [it.id, it.rev]));
      const now = Date.now();
      for (const it of paper.sections.flatMap((s) => s.items)) {
        if (before.get(it.id) !== it.rev) changed[it.id] = now;
      }
    }
    const run = this.state.run;
    const echoed = run && messages.some((m) => m.role === "user" && m.run_id === run.runId);
    this.set({
      messages,
      paper,
      changed,
      pendingUser: echoed ? null : this.state.pendingUser,
    });
    if (this.state.sessionId) this.touchRecent(this.state.sessionId, title);
  }

  async loadHistory(): Promise<void> {
    const sid = this.state.sessionId;
    if (!sid || !this.state.paper) return;
    try {
      const h: PaperHistory = await api.history(sid);
      this.set({ history: { canUndo: h.can_undo, canRedo: h.can_redo, headRev: h.head_rev } });
    } catch {
      /* 历史只影响撤销按钮的可用状态 */
    }
  }

  // ---- 手动编辑、撤销 / 重做 / 回退 ----
  async editOps(ops: Op[]): Promise<boolean> {
    const sid = this.state.sessionId;
    const paper = this.state.paper;
    if (!sid || !paper) return false;
    try {
      const out = await api.patchPaper(sid, ops, paper.rev);
      this.applyUpdate(out.paper, out.can_undo, out.can_redo, out.revision.rev);
      for (const w of out.warnings) this.toast(w);
      if (out.review_run_id) this.followReview(out.review_run_id, touchedItems(ops));
      return true;
    } catch (e) {
      this.toast(userMessageOf(e), "error");
      if (e instanceof ApiError && e.status === 409) await this.refresh();
      return false;
    }
  }

  async undo(): Promise<void> {
    await this.travel(() => api.undo(this.state.sessionId ?? ""), "已撤销");
  }
  async redo(): Promise<void> {
    await this.travel(() => api.redo(this.state.sessionId ?? ""), "已重做");
  }
  async restore(rev: number): Promise<void> {
    await this.travel(() => api.restore(this.state.sessionId ?? "", rev), `已回到第 ${rev} 版`);
  }

  private async travel(
    call: () => Promise<{
      paper: Paper;
      can_undo: boolean;
      can_redo: boolean;
      revision: { rev: number };
      review_run_id: string | null;
    }>,
    ok: string,
  ): Promise<void> {
    if (!this.state.sessionId) return;
    try {
      const out = await call();
      this.applyUpdate(out.paper, out.can_undo, out.can_redo, out.revision.rev);
      this.set({ lastEdit: null });
      this.toast(ok);
      if (out.review_run_id) this.followReview(out.review_run_id, []);
    } catch (e) {
      this.toast(userMessageOf(e), "error");
    }
  }

  private applyUpdate(paper: Paper, canUndo: boolean, canRedo: boolean, headRev: number): void {
    this.applyServer(this.state.messages, paper, "");
    this.set({ history: { canUndo, canRedo, headRev } });
  }

  /** 手改后的复核是独立的运行：订阅它，状态变化时更新试卷，题目上显示"正在核验"。 */
  private followReview(runId: string, itemIds: string[]): void {
    const ids = itemIds;
    if (ids.length) this.set({ reviewing: [...new Set([...this.state.reviewing, ...ids])] });
    let st = initialRun(runId, this.state.sessionId ?? "");
    streamEvents(`/api/runs/${runId}/events`, {
      onEvent: (e) => {
        st = reduceEvent(st, e);
        if (e.type === "item.status" || e.type === "paper.patch") void this.refresh();
        if (e.type === "item.status") {
          this.set({ reviewing: this.state.reviewing.filter((x) => x !== e.item_id) });
        }
      },
      maxRetries: 3,
    })
      .catch(() => undefined)
      .finally(() => {
        this.set({ reviewing: this.state.reviewing.filter((x) => !ids.includes(x)) });
        void this.refresh().then(() => this.loadHistory());
      });
  }

  clearChanged(id: string): void {
    if (!(id in this.state.changed)) return;
    const { [id]: _drop, ...rest } = this.state.changed;
    this.set({ changed: rest });
  }

  dismissLastEdit(): void {
    this.set({ lastEdit: null });
  }
}

function touchedItems(ops: Op[]): string[] {
  const ids = new Set<string>();
  for (const op of ops) {
    if (op.op === "replace_field") ids.add(op.item_id);
  }
  return [...ids];
}

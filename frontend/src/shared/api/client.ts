/** 后端 REST 客户端：薄封装，错误统一成 `ApiError`（带教师可读的 `userMessage`）。 */
import type {
  AggregateMetrics,
  AppEvent,
  Badcase,
  BadcaseIn,
  DebugRunDetail,
  DebugRunItem,
  ExportOptions,
  Health,
  KPDetail,
  KPRef,
  Op,
  Paper,
  PaperDiff,
  PaperHistory,
  PaperUpdate,
  RunView,
  SessionState,
  TurnAccepted,
} from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public userMessage: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const DEBUG_TOKEN_KEY = "verichalk.debugToken";

export function getDebugToken(): string {
  try {
    return sessionStorage.getItem(DEBUG_TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setDebugToken(token: string): void {
  try {
    if (token) sessionStorage.setItem(DEBUG_TOKEN_KEY, token);
    else sessionStorage.removeItem(DEBUG_TOKEN_KEY);
  } catch {
    /* 隐私模式下 sessionStorage 可能不可用：令牌只在本次页面有效 */
  }
}

export function debugHeaders(): Record<string, string> {
  const t = getDebugToken();
  return t ? { "x-debug-token": t } : {};
}

async function toError(res: Response): Promise<ApiError> {
  let code = `http_${res.status}`;
  let message = res.statusText;
  let user = "";
  try {
    const body = (await res.json()) as {
      error?: { code?: string; message?: string; user_message?: string };
      detail?: unknown;
    };
    if (body.error) {
      code = body.error.code ?? code;
      message = body.error.message ?? message;
      user = body.error.user_message ?? "";
    } else if (typeof body.detail === "string") {
      message = body.detail;
    }
  } catch {
    /* 非 JSON 响应 */
  }
  if (!user) {
    user =
      res.status === 403
        ? "没有权限访问。"
        : res.status >= 500
          ? "服务暂时出了点问题，请稍后再试。"
          : "请求没有成功，请稍后再试。";
  }
  return new ApiError(res.status, code, message, user);
}

async function request<T>(
  method: string,
  url: string,
  opts: {
    json?: unknown;
    form?: FormData;
    headers?: Record<string, string>;
    signal?: AbortSignal;
  } = {},
): Promise<T> {
  const headers: Record<string, string> = { ...opts.headers };
  let body: BodyInit | undefined;
  if (opts.json !== undefined) {
    headers["content-type"] = "application/json";
    body = JSON.stringify(opts.json);
  } else if (opts.form) {
    body = opts.form;
  }
  let res: Response;
  try {
    res = await fetch(url, { method, headers, body, signal: opts.signal });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError(0, "network", String(e), "网络连接不上，请检查网络后重试。");
  }
  if (!res.ok) throw await toError(res);
  return (await res.json()) as T;
}

const get = <T>(url: string, headers?: Record<string, string>) =>
  request<T>("GET", url, { headers });
const post = <T>(url: string, json?: unknown, headers?: Record<string, string>) =>
  request<T>("POST", url, { json: json ?? {}, headers });

export interface ExportFile {
  blob: Blob;
  filename: string;
  warnings: string[];
}

function filenameOf(res: Response, fallback: string): string {
  const cd = res.headers.get("content-disposition") ?? "";
  const m = /filename\*=UTF-8''([^;]+)/i.exec(cd);
  if (m?.[1]) {
    try {
      return decodeURIComponent(m[1]);
    } catch {
      /* 落到默认文件名 */
    }
  }
  return fallback;
}

export const api = {
  health: () => get<Health>("/api/health"),
  warmup: () => post<{ state: string }>("/api/warmup"),

  createSession: () => post<SessionState>("/api/sessions"),
  getSession: (id: string) => get<SessionState>(`/api/sessions/${id}`),

  postTurn: (sessionId: string, text: string, images: File[] = []) => {
    const form = new FormData();
    form.set("text", text);
    for (const f of images) form.append("images", f);
    return request<TurnAccepted>("POST", `/api/sessions/${sessionId}/turns`, { form });
  },
  getRun: (runId: string) => get<RunView>(`/api/runs/${runId}`),
  cancelRun: (runId: string) => post<{ cancelled: boolean }>(`/api/runs/${runId}/cancel`),
  answerCheckpoint: (runId: string, answer: Record<string, unknown>) =>
    post<{ accepted: boolean }>(`/api/runs/${runId}/checkpoint`, { answer }),

  patchPaper: (sessionId: string, ops: Op[], baseRev?: number) =>
    request<PaperUpdate>("PATCH", `/api/sessions/${sessionId}/paper`, {
      json: { ops, base_rev: baseRev ?? null },
    }),
  undo: (sessionId: string) => post<PaperUpdate>(`/api/sessions/${sessionId}/paper/undo`),
  redo: (sessionId: string) => post<PaperUpdate>(`/api/sessions/${sessionId}/paper/redo`),
  restore: (sessionId: string, rev: number) =>
    post<PaperUpdate>(`/api/sessions/${sessionId}/paper/restore`, { rev }),
  history: (sessionId: string) => get<PaperHistory>(`/api/sessions/${sessionId}/paper/history`),
  paperAt: (sessionId: string, rev: number) =>
    get<Paper>(`/api/sessions/${sessionId}/paper/revisions/${rev}`),
  diff: (sessionId: string, from: number, to: number) =>
    get<PaperDiff>(`/api/sessions/${sessionId}/paper/diff?from=${from}&to=${to}`),

  attachmentUrl: (sessionId: string, attachmentId: string, full = false) =>
    `/api/sessions/${sessionId}/attachments/${encodeURIComponent(attachmentId)}${full ? "?full=true" : ""}`,
  figureUrl: (sessionId: string, figureId: string) =>
    `/api/sessions/${sessionId}/figures/${encodeURIComponent(figureId)}`,
  kpRefs: (ids: string[]) =>
    ids.length === 0
      ? Promise.resolve<KPRef[]>([])
      : get<KPRef[]>(
          `/api/knowledge/refs?${ids.map((i) => `ids=${encodeURIComponent(i)}`).join("&")}`,
        ),

  async exportPaper(
    sessionId: string,
    options: ExportOptions,
    inline = false,
  ): Promise<ExportFile> {
    let res: Response;
    try {
      res = await fetch(`/api/sessions/${sessionId}/export${inline ? "?inline=true" : ""}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(options),
      });
    } catch (e) {
      throw new ApiError(0, "network", String(e), "网络连接不上，请检查网络后重试。");
    }
    if (!res.ok) throw await toError(res);
    let warnings: string[] = [];
    try {
      warnings = JSON.parse(
        decodeURIComponent(res.headers.get("x-export-warnings") ?? "[]"),
      ) as string[];
    } catch {
      /* 警告头缺失或格式不对：当作没有 */
    }
    return {
      blob: await res.blob(),
      filename: filenameOf(res, `试卷.${options.format}`),
      warnings,
    };
  },

  // ---- 调试台 ----
  debug: {
    runs: (q: { status?: string; session_id?: string; limit?: number; offset?: number } = {}) => {
      const p = new URLSearchParams();
      for (const [k, v] of Object.entries(q)) if (v !== undefined && v !== "") p.set(k, String(v));
      return get<DebugRunItem[]>(`/api/debug/runs?${p}`, debugHeaders());
    },
    run: (id: string) => get<DebugRunDetail>(`/api/debug/runs/${id}`, debugHeaders()),
    events: (id: string, after = 0) =>
      get<AppEvent[]>(`/api/debug/runs/${id}/events?after=${after}`, debugHeaders()),
    metrics: () => get<AggregateMetrics>("/api/debug/metrics", debugHeaders()),
    kp: (id: string) => get<KPDetail>(`/api/debug/kp/${encodeURIComponent(id)}`, debugHeaders()),
    badcases: () => get<Badcase[]>("/api/debug/badcases", debugHeaders()),
    addBadcase: (body: BadcaseIn) => post<Badcase>("/api/debug/badcases", body, debugHeaders()),
  },
};

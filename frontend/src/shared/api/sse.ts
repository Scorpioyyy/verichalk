/**
 * SSE 客户端（D11）：用 fetch 读流，因为 EventSource 不能带请求头（调试台要令牌）。
 * 断线自动续传：记住最后一个 `id:`（事件 seq），重连时带 `Last-Event-ID`，服务端从该 seq 之后重发，不丢不重。
 */
import type { AppEvent } from "./types";

export interface StreamOptions {
  /** 从这个 seq 之后开始（默认 0 = 从头） */
  after?: number;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  /** 重连间隔（毫秒），测试里可调小 */
  retryMs?: number;
  maxRetries?: number;
  /** 每收到一个事件调用；返回后才处理下一个，保证顺序 */
  onEvent: (e: AppEvent) => void;
  /** 连接状态变化（断线重连时界面可以提示） */
  onState?: (s: "open" | "reconnecting") => void;
}

export interface ParsedFrame {
  id?: number;
  event?: string;
  data: string;
}

/** 把一段累积文本拆成完整的 SSE 帧；返回帧与剩余的半帧。 */
export function splitFrames(buffer: string): { frames: ParsedFrame[]; rest: string } {
  const frames: ParsedFrame[] = [];
  const normalized = buffer.replace(/\r\n/g, "\n");
  const parts = normalized.split("\n\n");
  const rest = parts.pop() ?? "";
  for (const block of parts) {
    if (!block.trim() || block.startsWith(":")) continue; // 心跳注释
    const f: ParsedFrame = { data: "" };
    for (const line of block.split("\n")) {
      if (line.startsWith(":")) continue;
      const i = line.indexOf(":");
      const k = i < 0 ? line : line.slice(0, i);
      const v = i < 0 ? "" : line.slice(i + 1).replace(/^ /, "");
      if (k === "id") f.id = Number(v);
      else if (k === "event") f.event = v;
      else if (k === "data") f.data += (f.data ? "\n" : "") + v;
    }
    if (f.data) frames.push(f);
  }
  return { frames, rest };
}

const sleep = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((resolve) => {
    const t = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(t);
        resolve();
      },
      { once: true },
    );
  });

/**
 * 订阅一个运行的事件流，直到收到 `run.finished`（正常结束）、被取消或重连次数用尽。
 * 返回最后收到的 seq。
 */
export async function streamEvents(url: string, opts: StreamOptions): Promise<number> {
  let seq = opts.after ?? 0;
  let finished = false;
  let failures = 0;
  const maxRetries = opts.maxRetries ?? 8;
  while (!finished && !opts.signal?.aborted) {
    try {
      const res = await fetch(url, {
        headers: { accept: "text/event-stream", ...opts.headers, "last-event-id": String(seq) },
        signal: opts.signal,
      });
      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
      opts.onState?.("open");
      failures = 0;
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const { frames, rest } = splitFrames(buf);
        buf = rest;
        for (const f of frames) {
          const ev = JSON.parse(f.data) as AppEvent;
          if (f.id !== undefined) seq = f.id;
          opts.onEvent(ev);
          if (ev.type === "run.finished") finished = true;
        }
      }
      if (!finished) {
        // 服务端正常关闭但没有 run.finished：对已结束的运行意味着没有更多事件
        return seq;
      }
    } catch (e) {
      if (opts.signal?.aborted || (e instanceof DOMException && e.name === "AbortError"))
        return seq;
      failures += 1;
      if (failures > maxRetries) throw e;
      opts.onState?.("reconnecting");
      await sleep(opts.retryMs ?? Math.min(500 * failures, 4000), opts.signal);
    }
  }
  return seq;
}

/** 读取一个已结束运行的全部（用户可见）事件。 */
export async function fetchRunEvents(runId: string): Promise<AppEvent[]> {
  const out: AppEvent[] = [];
  await streamEvents(`/api/runs/${runId}/events`, {
    onEvent: (e) => out.push(e),
    maxRetries: 1,
    retryMs: 300,
  });
  return out;
}

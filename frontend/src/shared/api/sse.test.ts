import { afterEach, describe, expect, it, vi } from "vitest";
import { splitFrames, streamEvents } from "./sse";
import type { AppEvent } from "./types";

const frame = (seq: number, type: string, extra: object = {}) =>
  `id: ${seq}\nevent: ${type}\ndata: ${JSON.stringify({ seq, type, ...extra })}\n\n`;

function bodyOf(chunks: string[], failAfter = false): Response {
  const enc = new TextEncoder();
  let i = 0;
  const stream = new ReadableStream<Uint8Array>({
    pull(ctrl) {
      if (i < chunks.length) ctrl.enqueue(enc.encode(chunks[i++]));
      else if (failAfter) ctrl.error(new Error("connection reset"));
      else ctrl.close();
    },
  });
  return new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } });
}

afterEach(() => vi.unstubAllGlobals());

describe("splitFrames", () => {
  it("拆出完整帧，保留半帧", () => {
    const { frames, rest } = splitFrames(frame(1, "progress") + frame(2, "progress").slice(0, 20));
    expect(frames).toHaveLength(1);
    expect(frames[0]!.id).toBe(1);
    expect(rest.length).toBeGreaterThan(0);
  });

  it("忽略心跳注释，兼容 CRLF", () => {
    const { frames } = splitFrames(`: keepalive\n\n${frame(3, "progress").replace(/\n/g, "\r\n")}`);
    expect(frames.map((f) => f.id)).toEqual([3]);
  });

  it("多行 data 用换行连接", () => {
    const { frames } = splitFrames("id: 1\ndata: a\ndata: b\n\n");
    expect(frames[0]!.data).toBe("a\nb");
  });
});

describe("streamEvents", () => {
  it("按序投递事件，收到 run.finished 即结束", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          bodyOf([frame(1, "run.started"), frame(2, "progress"), frame(3, "run.finished")]),
        ),
    );
    const got: number[] = [];
    const last = await streamEvents("/x", { onEvent: (e: AppEvent) => got.push(e.seq) });
    expect(got).toEqual([1, 2, 3]);
    expect(last).toBe(3);
  });

  it("一帧被拆在两个网络块里也能解析", async () => {
    const f = frame(1, "progress") + frame(2, "run.finished");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(bodyOf([f.slice(0, 30), f.slice(30)])));
    const got: number[] = [];
    await streamEvents("/x", { onEvent: (e) => got.push(e.seq) });
    expect(got).toEqual([1, 2]);
  });

  it("断线后带 Last-Event-ID 续传，不丢不重", async () => {
    const calls: string[] = [];
    const fetchMock = vi
      .fn()
      .mockImplementationOnce((_u: string, init: RequestInit) => {
        calls.push((init.headers as Record<string, string>)["last-event-id"]!);
        return Promise.resolve(bodyOf([frame(1, "progress"), frame(2, "progress")], true));
      })
      .mockImplementationOnce((_u: string, init: RequestInit) => {
        calls.push((init.headers as Record<string, string>)["last-event-id"]!);
        return Promise.resolve(bodyOf([frame(3, "progress"), frame(4, "run.finished")]));
      });
    vi.stubGlobal("fetch", fetchMock);
    const got: number[] = [];
    const states: string[] = [];
    await streamEvents("/x", {
      onEvent: (e) => got.push(e.seq),
      onState: (s) => states.push(s),
      retryMs: 1,
    });
    expect(got).toEqual([1, 2, 3, 4]);
    expect(calls).toEqual(["0", "2"]);
    expect(states).toContain("reconnecting");
  });

  it("重连次数用尽后抛出", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("down")));
    await expect(
      streamEvents("/x", { onEvent: () => undefined, retryMs: 1, maxRetries: 2 }),
    ).rejects.toThrow("down");
  });

  it("中止信号让它安静地结束", async () => {
    const ctl = new AbortController();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() => {
        ctl.abort();
        return Promise.reject(new DOMException("aborted", "AbortError"));
      }),
    );
    await expect(
      streamEvents("/x", { onEvent: () => undefined, signal: ctl.signal }),
    ).resolves.toBe(0);
  });
});

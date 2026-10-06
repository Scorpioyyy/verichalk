import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, debugHeaders } from "@/shared/api/client";
import { streamEvents } from "@/shared/api/sse";
import type { AppEvent, DebugRunDetail } from "@/shared/api/types";
import { buildModel } from "./model";
import type { RunModel } from "./model";

export interface Async<T> {
  data: T | null;
  loading: boolean;
  error: ApiError | Error | null;
  reload: () => void;
}

/** 通用的异步加载：参数变化时重新取；403 单独暴露（令牌无效 → 显示令牌输入框）。 */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]): Async<T> {
  const [state, setState] = useState<{ data: T | null; loading: boolean; error: Error | null }>({
    data: null,
    loading: true,
    error: null,
  });
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  useEffect(() => {
    let live = true;
    setState((s) => ({ ...s, loading: true, error: null }));
    fn()
      .then((data) => live && setState({ data, loading: false, error: null }))
      .catch((error: Error) => live && setState({ data: null, loading: false, error }));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { ...state, reload };
}

export interface LiveRun {
  detail: DebugRunDetail | null;
  events: AppEvent[];
  model: RunModel;
  live: boolean;
  error: Error | null;
  loading: boolean;
}

const EMPTY_MODEL = buildModel([]);

/**
 * 一次运行的事件与模型：先取已落库的事件；运行还没结束就订阅调试事件流，边到边整理。
 * 事件按 seq 去重（续传可能重发），所以实时与回放结果一致。
 */
export function useRun(runId: string): LiveRun {
  const [detail, setDetail] = useState<DebugRunDetail | null>(null);
  const [events, setEvents] = useState<AppEvent[]>([]);
  const [live, setLive] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const seen = useRef(0);

  useEffect(() => {
    let alive = true;
    const ctl = new AbortController();
    seen.current = 0;
    setEvents([]);
    setDetail(null);
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const d = await api.debug.run(runId);
        const evs = await api.debug.events(runId);
        if (!alive) return;
        setDetail(d);
        setEvents(evs);
        seen.current = evs.reduce((m, e) => Math.max(m, e.seq), 0);
        setLoading(false);
        if (!d.run.status || ["succeeded", "failed", "cancelled"].includes(d.run.status)) return;
        setLive(true);
        let buf: AppEvent[] = [];
        let timer: ReturnType<typeof setTimeout> | null = null;
        const flush = () => {
          timer = null;
          const batch = buf;
          buf = [];
          if (alive && batch.length) setEvents((cur) => [...cur, ...batch]);
        };
        await streamEvents(`/api/debug/runs/${runId}/stream`, {
          after: seen.current,
          headers: debugHeaders(),
          signal: ctl.signal,
          onEvent: (e) => {
            if (e.seq <= seen.current) return;
            seen.current = e.seq;
            buf.push(e);
            if (!timer) timer = setTimeout(flush, 150); // 合并高频事件，避免每个事件都重绘
          },
        });
        flush();
        if (alive) {
          setLive(false);
          setDetail(await api.debug.run(runId));
        }
      } catch (e) {
        if (alive) {
          setError(e as Error);
          setLoading(false);
          setLive(false);
        }
      }
    })();
    return () => {
      alive = false;
      ctl.abort();
    };
  }, [runId]);

  const model = useMemo(() => (events.length ? buildModel(events) : EMPTY_MODEL), [events]);
  return { detail, events, model, live, error, loading };
}

export function isForbidden(e: Error | null): boolean {
  return e instanceof ApiError && e.status === 403;
}

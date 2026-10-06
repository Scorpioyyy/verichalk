export function fmtMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1) return "<1ms";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)}s`;
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)}m${String(s % 60).padStart(2, "0")}s`;
}

export function fmtCost(c: number | null | undefined, currency = "¥"): string {
  if (c === null || c === undefined) return "—";
  return `${currency === "CNY" ? "¥" : currency}${c < 0.01 ? c.toFixed(4) : c.toFixed(3)}`;
}

export function fmtTokens(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n >= 10_000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

export function fmtPct(p: number | null | undefined, digits = 0): string {
  if (p === null || p === undefined) return "—";
  return `${(p * 100).toFixed(digits)}%`;
}

export function fmtClock(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString("zh-CN", { hour12: false });
}

export function fmtDateTime(ts: number): string {
  const d = new Date(ts * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function pretty(v: unknown): string {
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

export function truncate(s: string, n: number): string {
  const t = s.replace(/\s+/g, " ").trim();
  return t.length > n ? `${t.slice(0, n)}…` : t;
}

/** 中间省略：保留头尾（运行 ID 的头部是前缀与时间，尾部是区分度最高的部分）。 */
export function midTruncate(s: string, head = 8, tail = 6): string {
  return s.length <= head + tail + 1 ? s : `${s.slice(0, head)}…${s.slice(-tail)}`;
}

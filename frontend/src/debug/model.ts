/**
 * 调试台的运行模型：把一次运行的事件日志（含 debug 可见性的事件）整理成 span 树、模型调用、检索步骤与题目证据。
 * 纯函数；实时订阅与事后回放用同一份代码（D23）。
 */
import type {
  AppEvent,
  CheckResult,
  ErrorInfo,
  EventOf,
  Item,
  LLMCallRecord,
  RetrievalPayload,
  VerifyStatus,
} from "@/shared/api/types";

export type SpanKind = "run" | "stage" | "llm" | "tool" | "check" | "other";
export type SpanState = "running" | "ok" | "error" | "cancelled";

export interface LLMCallItem {
  seq: number;
  ts: number;
  spanId: string | null;
  record: LLMCallRecord;
}

export interface SpanNode {
  id: string;
  parentId: string | null;
  kind: SpanKind;
  name: string;
  start: number; // 秒（事件时间戳）
  end: number | null;
  durationMs: number | null;
  state: SpanState;
  attrs: Record<string, unknown>;
  error: ErrorInfo | null;
  children: SpanNode[];
  depth: number;
  calls: LLMCallItem[];
}

export interface RetrievalStep {
  seq: number;
  ts: number;
  spanId: string | null;
  payload: RetrievalPayload;
}

export interface StatusPoint {
  seq: number;
  ts: number;
  status: VerifyStatus;
  checks: CheckResult[];
}

export interface ItemEvidence {
  id: string;
  item: Item | null;
  order: number | null;
  statuses: StatusPoint[];
}

export interface RunModel {
  runId: string;
  t0: number;
  t1: number; // 最后一个事件的时间
  finished: boolean;
  status: "succeeded" | "failed" | "cancelled" | null;
  error: ErrorInfo | null;
  roots: SpanNode[];
  spans: Map<string, SpanNode>;
  calls: LLMCallItem[];
  retrievals: RetrievalStep[];
  items: ItemEvidence[];
  lastSeq: number;
}

export function buildModel(events: AppEvent[]): RunModel {
  const spans = new Map<string, SpanNode>();
  const calls: LLMCallItem[] = [];
  const retrievals: RetrievalStep[] = [];
  const items = new Map<string, ItemEvidence>();
  const m: RunModel = {
    runId: events[0]?.run_id ?? "",
    t0: events[0]?.ts ?? 0,
    t1: events[0]?.ts ?? 0,
    finished: false,
    status: null,
    error: null,
    roots: [],
    spans,
    calls,
    retrievals,
    items: [],
    lastSeq: 0,
  };
  const ev = (id: string): ItemEvidence => {
    let x = items.get(id);
    if (!x) {
      x = { id, item: null, order: null, statuses: [] };
      items.set(id, x);
    }
    return x;
  };

  for (const e of events) {
    m.lastSeq = Math.max(m.lastSeq, e.seq);
    m.t1 = Math.max(m.t1, e.ts);
    switch (e.type) {
      case "span.started": {
        if (!e.span_id) break;
        spans.set(e.span_id, {
          id: e.span_id,
          parentId: e.parent_id ?? null,
          kind: e.kind,
          name: e.name,
          start: e.ts,
          end: null,
          durationMs: null,
          state: "running",
          attrs: e.attrs,
          error: null,
          children: [],
          depth: 0,
          calls: [],
        });
        break;
      }
      case "span.finished": {
        if (!e.span_id) break;
        let s = spans.get(e.span_id);
        if (!s) {
          // 只有结束没有开始（续传的片段）：用时长反推起点
          s = {
            id: e.span_id,
            parentId: e.parent_id ?? null,
            kind: e.kind,
            name: e.name,
            start: e.ts - e.duration_ms / 1000,
            end: null,
            durationMs: null,
            state: "running",
            attrs: {},
            error: null,
            children: [],
            depth: 0,
            calls: [],
          };
          spans.set(e.span_id, s);
        }
        s.end = e.ts;
        s.durationMs = e.duration_ms;
        s.state = e.status;
        s.attrs = { ...s.attrs, ...e.attrs };
        s.error = e.error;
        break;
      }
      case "llm.call": {
        const c: LLMCallItem = {
          seq: e.seq,
          ts: e.ts,
          spanId: e.span_id ?? null,
          record: e.record,
        };
        calls.push(c);
        break;
      }
      case "retrieval.result":
        retrievals.push({ seq: e.seq, ts: e.ts, spanId: e.span_id ?? null, payload: e.payload });
        break;
      case "item.delivered": {
        const x = ev(e.item.id);
        x.item = e.item;
        x.order = e.order;
        break;
      }
      case "item.status":
        ev(e.item_id).statuses.push({ seq: e.seq, ts: e.ts, status: e.status, checks: e.checks });
        break;
      case "run.finished":
        m.finished = true;
        m.status = e.status;
        m.error = e.error;
        break;
      default:
        break;
    }
  }

  for (const c of calls) {
    const host = c.spanId ? spans.get(c.spanId) : undefined;
    host?.calls.push(c);
  }
  // 建树；父节点缺失（被截断）的挂到根
  for (const s of spans.values()) {
    const parent = s.parentId ? spans.get(s.parentId) : undefined;
    if (parent) parent.children.push(s);
    else m.roots.push(s);
  }
  const sortRec = (nodes: SpanNode[], depth: number) => {
    nodes.sort((a, b) => a.start - b.start);
    for (const n of nodes) {
      n.depth = depth;
      sortRec(n.children, depth + 1);
    }
  };
  sortRec(m.roots, 0);
  m.items = [...items.values()].sort((a, b) => (a.order ?? 1e9) - (b.order ?? 1e9));
  return m;
}

/** 深度优先展开的可见行：折叠的节点不展开子节点。 */
export function visibleRows(roots: SpanNode[], collapsed: Set<string>): SpanNode[] {
  const out: SpanNode[] = [];
  const walk = (n: SpanNode) => {
    out.push(n);
    if (!collapsed.has(n.id)) n.children.forEach(walk);
  };
  roots.forEach(walk);
  return out;
}

/** 默认折叠：深度 ≥ 2 且有子节点的 span（逐题并行的子流程很多，先看阶段，再点开具体某一题）。 */
export function defaultCollapsed(roots: SpanNode[]): Set<string> {
  const c = new Set<string>();
  const walk = (n: SpanNode) => {
    if (n.depth >= 2 && n.children.length) c.add(n.id);
    n.children.forEach(walk);
  };
  roots.forEach(walk);
  return c;
}

export function spanDurationMs(s: SpanNode, now: number): number {
  return s.durationMs ?? Math.max(0, (now - s.start) * 1000);
}

// ---- 检索图 ----
export interface LayoutNode {
  id: string;
  name: string;
  role: "anchor" | "candidate" | "selected" | "context";
  grade: number | null;
  semester: string | null;
  domain: string;
  score: number | null;
  note: string;
  x: number;
  y: number;
  col: number;
}
export interface LayoutEdge {
  source: string;
  target: string;
  type: string;
  weight: number | null;
}
export interface GraphLayout {
  nodes: LayoutNode[];
  edges: LayoutEdge[];
  columns: { key: string; label: string; x: number }[];
  width: number;
  height: number;
  combos: { kp_ids: string[]; score: number; rationale: string }[];
}

const ROLE_RANK = { selected: 3, anchor: 2, candidate: 1, context: 0 } as const;
export const NODE_W = 196;
export const NODE_H = 46;
const COL_GAP = 84;
const ROW_GAP = 14;
const GRADE_CN = ["", "一", "二", "三", "四", "五", "六"];

/** 累计到第 `upTo` 步的检索图，按学期分层：同一学期的知识点在同一列，列从早到晚；列内先放选中 / 锚点，再按得分。 */
export function layoutGraph(steps: RetrievalStep[], upTo: number): GraphLayout {
  const nodes = new Map<string, Omit<LayoutNode, "x" | "y" | "col">>();
  const edges = new Map<string, LayoutEdge>();
  let combos: GraphLayout["combos"] = [];
  for (const s of steps.slice(0, upTo + 1)) {
    for (const n of s.payload.nodes) {
      const prev = nodes.get(n.id);
      if (!prev || ROLE_RANK[n.role] >= ROLE_RANK[prev.role]) {
        nodes.set(n.id, {
          id: n.id,
          name: n.name,
          role: n.role,
          grade: n.grade,
          semester: n.semester,
          domain: n.domain,
          score: n.score ?? prev?.score ?? null,
          note: n.note || prev?.note || "",
        });
      }
    }
    for (const e of s.payload.edges) {
      edges.set(`${e.source}|${e.target}|${e.type}`, {
        source: e.source,
        target: e.target,
        type: e.type,
        weight: e.weight ?? null,
      });
    }
    if (s.payload.combos.length) combos = s.payload.combos;
  }
  const colKey = (n: { grade: number | null; semester: string | null }) =>
    `${n.grade ?? 0}${n.semester ?? ""}`;
  const keys = [...new Set([...nodes.values()].map(colKey))].sort();
  const colIndex = new Map(keys.map((k, i) => [k, i]));
  const byCol = new Map<string, Omit<LayoutNode, "x" | "y" | "col">[]>();
  for (const n of nodes.values()) {
    const k = colKey(n);
    if (!byCol.has(k)) byCol.set(k, []);
    byCol.get(k)!.push(n);
  }
  const out: LayoutNode[] = [];
  let height = 0;
  for (const k of keys) {
    const list = byCol.get(k)!;
    list.sort(
      (a, b) =>
        ROLE_RANK[b.role] - ROLE_RANK[a.role] ||
        (b.score ?? 0) - (a.score ?? 0) ||
        a.name.localeCompare(b.name),
    );
    const col = colIndex.get(k)!;
    list.forEach((n, i) => {
      out.push({
        ...n,
        col,
        x: 24 + col * (NODE_W + COL_GAP),
        y: 44 + i * (NODE_H + ROW_GAP),
      });
    });
    height = Math.max(height, 44 + list.length * (NODE_H + ROW_GAP));
  }
  const columns = keys.map((k, i) => {
    const grade = Number(k[0] ?? 0);
    const sem = k.slice(1);
    return {
      key: k,
      label: grade
        ? `${GRADE_CN[grade] ?? grade}年级${sem === "a" ? "上" : sem === "b" ? "下" : ""}`
        : "未定位",
      x: 24 + i * (NODE_W + COL_GAP),
    };
  });
  return {
    nodes: out,
    edges: [...edges.values()].filter((e) => nodes.has(e.source) && nodes.has(e.target)),
    columns,
    width: 24 + keys.length * (NODE_W + COL_GAP),
    height: Math.max(height + 20, 160),
    combos,
  };
}

export const EDGE_STYLE: Record<string, { color: string; dash?: string; label: string }> = {
  prerequisite: { color: "#3d5af1", label: "前置" },
  builds_on: { color: "#22558b", label: "递进" },
  extends: { color: "#22558b", dash: "6 4", label: "拓展" },
  related: { color: "#7b858c", dash: "2 4", label: "相关" },
  confusable: { color: "#a8261d", dash: "6 4", label: "易混" },
  cooccur: { color: "#7a4f00", dash: "2 3", label: "同题共现" },
};

export function edgeStyle(type: string) {
  return EDGE_STYLE[type] ?? { color: "#7b858c", label: type };
}

export type { EventOf };

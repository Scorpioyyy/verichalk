import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/shared/api/client";
import type { KPDetail } from "@/shared/api/types";
import { Icon } from "@/shared/ui/Icon";
import { fmtClock, truncate } from "./format";
import { useAsync } from "./hooks";
import { EDGE_STYLE, NODE_H, NODE_W, edgeStyle, layoutGraph } from "./model";
import type { LayoutNode, RetrievalStep } from "./model";

const ROLE_LABEL = {
  anchor: "锚点",
  selected: "选中",
  candidate: "候选",
  context: "上下文",
} as const;

/**
 * 图检索视图：按学期分层的知识图（列 = 学期，从早到晚）；命中节点按角色着色、选中的组合高亮、边按类型区分。
 * 滑块 / 播放按钮把"检索过程"逐步回放：每一步把该步骤的节点与边累加上去。
 */
export function GraphView({ steps }: { steps: RetrievalStep[] }) {
  const [at, setAt] = useState(Math.max(0, steps.length - 1));
  const [playing, setPlaying] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [comboIdx, setComboIdx] = useState<number | null>(null);

  useEffect(() => setAt(Math.max(0, steps.length - 1)), [steps.length]);
  useEffect(() => {
    if (!playing) return;
    if (at >= steps.length - 1) {
      setPlaying(false);
      return;
    }
    const t = setTimeout(() => setAt((a) => a + 1), 900);
    return () => clearTimeout(t);
  }, [playing, at, steps.length]);

  const layout = useMemo(() => layoutGraph(steps, at), [steps, at]);
  const nodeById = useMemo(() => new Map(layout.nodes.map((n) => [n.id, n])), [layout]);
  const combo = comboIdx !== null ? layout.combos[comboIdx] : undefined;
  const inCombo = new Set(combo?.kp_ids ?? []);
  const focus = hover ?? selected;

  if (steps.length === 0) {
    return (
      <p className="muted">这次运行没有图检索（例如澄清、编辑、导出等不检索知识库的运行）。</p>
    );
  }

  const step = steps[at]!;
  return (
    <div className="gv">
      <div className="gv-controls">
        <button
          type="button"
          className="btn btn--sm"
          onClick={() => {
            if (at >= steps.length - 1) setAt(0);
            setPlaying(!playing);
          }}
        >
          {playing ? "暂停" : "回放"}
        </button>
        <input
          type="range"
          min={0}
          max={steps.length - 1}
          value={at}
          onChange={(e) => {
            setPlaying(false);
            setAt(Number(e.target.value));
          }}
          aria-label="检索步骤"
          className="gv-range"
        />
        <span className="small">
          第 <b>{at + 1}</b> / {steps.length} 步：<b className="mono">{step.payload.step}</b>
          {step.payload.query && (
            <span className="muted"> · “{truncate(step.payload.query, 40)}”</span>
          )}
          <span className="muted mono"> · {fmtClock(step.ts)}</span>
        </span>
      </div>
      {step.payload.notes.length > 0 && (
        <p className="muted small">{step.payload.notes.join("；")}</p>
      )}

      <div className="gv-body">
        <div className="gv-canvas" data-testid="graph-canvas">
          <Canvas
            layout={layout}
            nodeById={nodeById}
            focus={focus}
            inCombo={inCombo}
            selected={selected}
            onHover={setHover}
            onSelect={setSelected}
          />
          <Legend />
        </div>
        <aside className="gv-side">
          <div>
            <h4>本步骤命中</h4>
            <p className="small">
              {step.payload.nodes.length} 个节点 · {step.payload.edges.length} 条边 ·{" "}
              {step.payload.combos.length} 个组合
            </p>
          </div>
          {layout.combos.length > 0 && (
            <div>
              <h4>候选组合（点击高亮）</h4>
              <ol className="gv-combos">
                {layout.combos.map((c, i) => (
                  <li key={i}>
                    <button
                      type="button"
                      className={`gv-combo ${comboIdx === i ? "gv-combo--on" : ""}`}
                      onClick={() => setComboIdx(comboIdx === i ? null : i)}
                    >
                      <span className="mono">{c.score.toFixed(2)}</span>{" "}
                      {c.kp_ids.map((id) => nodeById.get(id)?.name ?? id).join(" + ")}
                      {c.rationale && <em className="muted small">{truncate(c.rationale, 90)}</em>}
                    </button>
                  </li>
                ))}
              </ol>
            </div>
          )}
          <NodePanel
            node={selected ? nodeById.get(selected) : undefined}
            onClose={() => setSelected(null)}
          />
        </aside>
      </div>
    </div>
  );
}

function Canvas(p: {
  layout: ReturnType<typeof layoutGraph>;
  nodeById: Map<string, LayoutNode>;
  focus: string | null;
  inCombo: Set<string>;
  selected: string | null;
  onHover: (id: string | null) => void;
  onSelect: (id: string | null) => void;
}) {
  const { layout, nodeById, focus } = p;
  const [view, setView] = useState({ k: 1, x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null);
  const connected = useMemo(() => {
    if (!focus) return null;
    const s = new Set<string>([focus]);
    for (const e of layout.edges) {
      if (e.source === focus) s.add(e.target);
      if (e.target === focus) s.add(e.source);
    }
    return s;
  }, [focus, layout.edges]);

  return (
    <svg
      className="gv-svg"
      width={Math.max(layout.width, 480)}
      height={layout.height}
      viewBox={`0 0 ${Math.max(layout.width, 480)} ${layout.height}`}
      role="img"
      aria-label="知识图检索结果"
      onWheel={(e) => {
        if (!e.ctrlKey && !e.metaKey) return;
        e.preventDefault();
        setView((v) => ({
          ...v,
          k: Math.min(2.5, Math.max(0.5, v.k * (e.deltaY < 0 ? 1.1 : 0.9))),
        }));
      }}
      onPointerDown={(e) => {
        if ((e.target as Element).closest(".gv-node")) return;
        drag.current = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y };
        (e.currentTarget as Element).setPointerCapture(e.pointerId);
      }}
      onPointerMove={(e) => {
        const d = drag.current;
        if (d) setView((v) => ({ ...v, x: d.vx + (e.clientX - d.x), y: d.vy + (e.clientY - d.y) }));
      }}
      onPointerUp={() => (drag.current = null)}
      onClick={(e) => {
        if (!(e.target as Element).closest(".gv-node")) p.onSelect(null);
      }}
    >
      <defs>
        {Object.entries({ ...EDGE_STYLE, other: { color: "#7b858c", label: "" } }).map(
          ([k, st]) => (
            <marker
              key={k}
              id={`arr-${k}`}
              viewBox="0 0 8 8"
              refX="7"
              refY="4"
              markerWidth="7"
              markerHeight="7"
              orient="auto"
            >
              <path d="M0 0L8 4L0 8z" fill={st.color} />
            </marker>
          ),
        )}
      </defs>
      <g transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
        {layout.columns.map((c) => (
          <g key={c.key}>
            <text x={c.x + NODE_W / 2} y={22} textAnchor="middle" className="gv-col">
              {c.label}
            </text>
            <line x1={c.x - 6} x2={c.x - 6} y1={30} y2={layout.height} className="gv-colline" />
          </g>
        ))}
        {layout.edges.map((e, i) => {
          const a = nodeById.get(e.source)!;
          const b = nodeById.get(e.target)!;
          const st = edgeStyle(e.type);
          const [x1, y1, x2, y2] = anchorPoints(a, b);
          // 跨列：S 形曲线；同列：向右侧鼓出，避免与节点边框重合
          const sameCol = a.col === b.col;
          const bulge = 34 + Math.min(Math.abs(y2 - y1) / 6, 40);
          const d = sameCol
            ? `M${x1} ${y1} C${x1 + bulge} ${y1} ${x2 + bulge} ${y2} ${x2} ${y2}`
            : `M${x1} ${y1} C${(x1 + x2) / 2} ${y1} ${(x1 + x2) / 2} ${y2} ${x2} ${y2}`;
          const dim =
            connected &&
            !(
              connected.has(e.source) &&
              connected.has(e.target) &&
              (e.source === focus || e.target === focus)
            );
          const combo = p.inCombo.has(e.source) && p.inCombo.has(e.target);
          return (
            <path
              key={`${e.source}-${e.target}-${e.type}-${i}`}
              d={d}
              fill="none"
              stroke={st.color}
              strokeDasharray={st.dash}
              strokeWidth={combo ? 3 : 1.5}
              opacity={dim ? 0.12 : 0.85}
              markerEnd={`url(#arr-${EDGE_STYLE[e.type] ? e.type : "other"})`}
            >
              <title>{`${a.name} → ${b.name}（${st.label}${e.weight !== null ? `，权重 ${e.weight.toFixed(2)}` : ""}）`}</title>
            </path>
          );
        })}
        {layout.nodes.map((n) => {
          const dim = connected && !connected.has(n.id);
          const combo = p.inCombo.has(n.id);
          return (
            <g
              key={n.id}
              className={`gv-node gv-node--${n.role} ${p.selected === n.id ? "gv-node--sel" : ""} ${combo ? "gv-node--combo" : ""}`}
              transform={`translate(${n.x} ${n.y})`}
              opacity={dim ? 0.3 : 1}
              tabIndex={0}
              role="button"
              aria-label={`${n.name}（${ROLE_LABEL[n.role]}）`}
              onMouseEnter={() => p.onHover(n.id)}
              onMouseLeave={() => p.onHover(null)}
              onFocus={() => p.onHover(n.id)}
              onBlur={() => p.onHover(null)}
              onClick={() => p.onSelect(n.id)}
              onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && p.onSelect(n.id)}
            >
              <rect width={NODE_W} height={NODE_H} rx={9} />
              <text x={10} y={19} className="gv-node__name">
                {truncate(n.name, 14)}
              </text>
              <text x={10} y={36} className="gv-node__sub">
                {ROLE_LABEL[n.role]}
                {n.score !== null ? ` · ${n.score.toFixed(2)}` : ""}
              </text>
              <title>{n.name}</title>
            </g>
          );
        })}
      </g>
    </svg>
  );
}

function anchorPoints(a: LayoutNode, b: LayoutNode): [number, number, number, number] {
  if (a.col === b.col) {
    // 同列：从右侧绕出
    return [a.x + NODE_W, a.y + NODE_H / 2, b.x + NODE_W, b.y + NODE_H / 2];
  }
  const aLeft = a.col < b.col;
  return aLeft
    ? [a.x + NODE_W, a.y + NODE_H / 2, b.x, b.y + NODE_H / 2]
    : [a.x, a.y + NODE_H / 2, b.x + NODE_W, b.y + NODE_H / 2];
}

function Legend() {
  return (
    <div className="gv-legend" aria-label="图例">
      {Object.entries(EDGE_STYLE).map(([k, st]) => (
        <span key={k}>
          <svg width="28" height="8" aria-hidden>
            <line
              x1="0"
              x2="28"
              y1="4"
              y2="4"
              stroke={st.color}
              strokeWidth="2"
              strokeDasharray={st.dash}
            />
          </svg>
          {st.label}
        </span>
      ))}
      {(["anchor", "selected", "candidate", "context"] as const).map((r) => (
        <span key={r}>
          <i className={`gv-chip gv-chip--${r}`} />
          {ROLE_LABEL[r]}
        </span>
      ))}
    </div>
  );
}

function NodePanel({ node, onClose }: { node?: LayoutNode; onClose: () => void }) {
  const detail = useAsync<KPDetail | null>(
    () => (node ? api.debug.kp(node.id) : Promise.resolve(null)),
    [node?.id],
  );
  if (!node) return <p className="muted small">点击图中的节点，查看知识点说明与典型错误。</p>;
  const d = detail.data;
  return (
    <div className="gv-node-panel" data-testid="node-panel">
      <header>
        <h4>{node.name}</h4>
        <button
          type="button"
          className="btn btn--ghost btn--icon btn--sm"
          onClick={onClose}
          aria-label="关闭"
        >
          <Icon name="x" size={14} />
        </button>
      </header>
      <dl className="kv">
        <dt>角色</dt>
        <dd>{ROLE_LABEL[node.role]}</dd>
        {node.score !== null && (
          <>
            <dt>得分</dt>
            <dd className="mono">{node.score.toFixed(3)}</dd>
          </>
        )}
        <dt>ID</dt>
        <dd className="mono small">{node.id}</dd>
        <dt>领域</dt>
        <dd>{node.domain || "—"}</dd>
        {node.note && (
          <>
            <dt>备注</dt>
            <dd>{node.note}</dd>
          </>
        )}
      </dl>
      {detail.loading && <p className="muted small">加载知识点详情……</p>}
      {d && (
        <>
          <p className="small">{d.description}</p>
          {d.typical_errors.length > 0 && (
            <>
              <h5>典型错误</h5>
              <ul className="small">
                {d.typical_errors.map((e) => (
                  <li key={e}>{e}</li>
                ))}
              </ul>
            </>
          )}
          <p className="muted small">
            {d.unit_title} · {d.lesson_title}
          </p>
        </>
      )}
    </div>
  );
}

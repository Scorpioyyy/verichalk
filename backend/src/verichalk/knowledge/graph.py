"""知识图索引与组合挖掘（architecture §5.2，D20）。

`GraphData` 是知识库关系图的一份**纯数据快照**（节点、边、教材里真实出现过的跨点共现），由 `KnowledgeService.graph()`
从 chalkbase 的公开接口构建；`ComboMiner` 在快照上做确定性的组合挖掘，不依赖 chalkbase、不做 I/O，因此可复现、可单测、
可在评测里单独打分。LLM 只在挖掘出的候选里"选择与构想情境"。

打分信号（权重在 `config/plan.yaml`，评测里逐项消融）：
- `edge`：两个知识点之间直接的关系边（前置 / 递进 / 相关 / 扩展 / 易混淆），按边类型加权；
- `cooccur`：教材里同一道题同时涉及这两个知识点的次数（真实的跨点搭配）；
- `proximity`：教学位置接近（同一单元 > 同一册 > 相邻册）——刚学过的内容放在一起更自然；
- `thread`：同一主线（如"小数加减法"）；
- `shared`：没有直接边时，共同邻居越多越相关（Adamic–Adar）。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..domain.knowledge import Combo


@dataclass(frozen=True)
class KPNode:
    id: str
    name: str
    grade: int
    semester: str
    domain: str
    thread: str
    topic: str
    position: int  # 教学序列位置：首次引入课时在 12 册里的先后
    unit_id: str
    lesson_id: str
    assessable: bool = True
    kinds: frozenset[str] = (
        frozenset()
    )  # 该知识点在教材里的自然题型（不依赖图形的：calc / fill / application / judge / choice）
    n_exercises: int = 0  # 教材里归到该知识点的习题数：越多越是核心考点


@dataclass
class GraphData:
    """关系图快照。`edges[(a, b)]` 的键是排序后的无向对，值是 {边类型: 方向 "a>b" / "b>a" / "-"}。"""

    nodes: dict[str, KPNode]
    edges: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)
    cooccur: dict[tuple[str, str], int] = field(default_factory=dict)
    neighbors: dict[str, set[str]] = field(default_factory=dict)
    contexts: dict[str, dict[str, int]] = field(
        default_factory=dict
    )  # 知识点 → {教材里出现过的生活情境主题: 出现次数}

    @staticmethod
    def key(a: str, b: str) -> tuple[str, str]:
        return (a, b) if a <= b else (b, a)

    def add_edge(self, src: str, dst: str, etype: str, *, directed: bool) -> None:
        if src not in self.nodes or dst not in self.nodes or src == dst:
            return
        k = self.key(src, dst)
        direction = ("a>b" if k[0] == src else "b>a") if directed else "-"
        self.edges.setdefault(k, {})[etype] = direction
        self.neighbors.setdefault(src, set()).add(dst)
        self.neighbors.setdefault(dst, set()).add(src)

    def add_cooccur(self, ids: Iterable[str]) -> None:
        uniq = sorted({i for i in ids if i in self.nodes})
        for i in range(len(uniq)):
            for j in range(i + 1, len(uniq)):
                k = (uniq[i], uniq[j])
                self.cooccur[k] = self.cooccur.get(k, 0) + 1

    def add_contexts(self, kp_id: str, themes: Iterable[str]) -> None:
        if kp_id in self.nodes:
            c = self.contexts.setdefault(kp_id, {})
            for t in themes:
                c[t] = c.get(t, 0) + 1

    def context_idf(self) -> dict[str, float]:
        """情境的稀有度：很多知识点都出现的情境（"校园活动"）对"自然搭配"的证明力弱。"""
        df: dict[str, int] = {}
        for ts in self.contexts.values():
            for t in ts:
                df[t] = df.get(t, 0) + 1
        n = max(1, len(self.contexts))
        return {t: math.log(1 + n / c) for t, c in df.items()}

    def shared_contexts(self, a: str, b: str) -> list[str]:
        """两个知识点共同出现的情境，按"在两者的教材题里都常见"排序。"""
        ca, cb = self.contexts.get(a, {}), self.contexts.get(b, {})
        common = ca.keys() & cb.keys()
        return sorted(common, key=lambda t: (-min(ca[t], cb[t]), t))

    def own_contexts(self, a: str) -> list[str]:
        """某知识点自己的情境，按教材里出现次数从多到少。"""
        c = self.contexts.get(a, {})
        return sorted(c, key=lambda t: (-c[t], t))

    def without_cooccur(self) -> GraphData:
        """去掉共现信号的副本（留出评测用：看仅凭图结构能否复现教材里真实的搭配）。"""
        return GraphData(self.nodes, self.edges, {}, self.neighbors, self.contexts)


@dataclass(frozen=True)
class ComboWeights:
    """组合打分权重。默认值来自 `config/plan.yaml`；评测里用 `replace(...)` 逐项置零做消融。"""

    edge: float = 0.6
    cooccur: float = 1.0
    proximity: float = 1.0
    thread: float = 0.3
    shared: float = 0.4
    scene: float = 0.5
    bridge_shared: float = (
        0.15  # 跨主题搭配至少要有一条"桥"：直接的关系边 / 教材共现 / 足够多的共同相关知识点
    )
    edge_types: Mapping[str, float] = field(
        default_factory=lambda: {
            "prerequisite": 1.0,
            "builds_on": 0.8,
            "related": 0.9,
            "extends": 0.6,
            "confusable": 0.4,
        }
    )
    max_reuse: int = 2  # 同一个伙伴知识点最多出现在几个入选组合里（保证多样）

    @classmethod
    def load(cls, config_dir: Path) -> ComboWeights:
        """读取 `config/plan.yaml` 的 `combos` 段；缺失的字段用默认值。"""
        path = config_dir / "plan.yaml"
        data: dict[str, Any] = {}
        if path.exists():
            data = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("combos", {})
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def _proximity(a: KPNode, b: KPNode) -> float:
    if a.unit_id == b.unit_id:
        return 1.0
    if a.grade == b.grade and a.semester == b.semester:
        return 0.7
    gap = abs((a.grade * 2 + (a.semester == "b")) - (b.grade * 2 + (b.semester == "b")))
    return max(0.0, 0.5 - 0.15 * (gap - 1))  # 相邻一册 0.5，每远一册再降 0.15


_IDF_CACHE: dict[int, dict[str, float]] = {}


def _idf(g: GraphData) -> dict[str, float]:
    key = id(g.contexts)
    if key not in _IDF_CACHE:
        _IDF_CACHE.clear()
        _IDF_CACHE[key] = g.context_idf()
    return _IDF_CACHE[key]


def pair_signals(g: GraphData, a: str, b: str, w: ComboWeights) -> dict[str, float]:
    """两个知识点的各路信号（已乘权重）；总分是它们的和，调试台直接展示分项。"""
    na, nb = g.nodes[a], g.nodes[b]
    k = GraphData.key(a, b)
    out: dict[str, float] = {}
    types = g.edges.get(k, {})
    if types and w.edge:
        out["edge"] = w.edge * max(w.edge_types.get(t, 0.3) for t in types)
    n_co = g.cooccur.get(k, 0)
    if n_co and w.cooccur:
        out["cooccur"] = w.cooccur * min(1.0, math.log1p(n_co) / math.log1p(3))
    if w.proximity:
        p = _proximity(na, nb)
        if p:
            out["proximity"] = w.proximity * p
    if w.thread and na.thread and na.thread == nb.thread:
        out["thread"] = w.thread
    if w.scene:
        common = g.shared_contexts(a, b)
        if common:
            idf = _idf(g)
            tot = sum(idf.get(t, 0.0) for t in common)
            out["scene"] = w.scene * min(1.0, tot / 3.0)
    if w.shared and a in g.neighbors and b in g.neighbors:
        common = g.neighbors[a] & g.neighbors[b]
        if common:
            aa = sum(1.0 / math.log(2 + len(g.neighbors[c])) for c in common)
            out["shared"] = w.shared * min(1.0, aa / 2.0)
    return out


def pair_score(g: GraphData, a: str, b: str, w: ComboWeights) -> float:
    return sum(pair_signals(g, a, b, w).values())


def _explain(g: GraphData, ids: list[str], w: ComboWeights) -> str:
    parts: list[str] = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            k = GraphData.key(a, b)
            bits: list[str] = []
            for t, d in g.edges.get(k, {}).items():
                label = {
                    "prerequisite": "前置",
                    "builds_on": "递进",
                    "related": "相关",
                    "extends": "扩展",
                    "confusable": "易混",
                }.get(t, t)
                if d == "-":
                    bits.append(label)
                else:
                    src, dst = (k[0], k[1]) if d == "a>b" else (k[1], k[0])
                    bits.append(f"{g.nodes[src].name}是{g.nodes[dst].name}的{label}")
            n_co = g.cooccur.get(k, 0)
            if n_co:
                bits.append(f"教材有 {n_co} 道题同时涉及")
            common_ctx = g.shared_contexts(a, b)
            if common_ctx:
                bits.append("都出现在「" + "、".join(common_ctx[:3]) + "」情境里")
            if not bits and (g.neighbors.get(a, set()) & g.neighbors.get(b, set())):
                bits.append("有共同相关的知识点")
            if bits:
                parts.append(f"{g.nodes[a].name}×{g.nodes[b].name}：" + "；".join(bits))
    return "；".join(parts)


class ComboMiner:
    """在已学范围内，以锚点为中心挖掘 2～3 个知识点的组合。"""

    def __init__(self, graph: GraphData, weights: ComboWeights | None = None) -> None:
        self.g = graph
        self.w = weights or ComboWeights()

    def _eligible(
        self, anchor: str, b: str, topic: str, grade_gap: int | None, cross_unit: bool = False
    ) -> bool:
        if b == anchor or b not in self.g.nodes:
            return False
        na, nb = self.g.nodes[anchor], self.g.nodes[b]
        if topic == "in_topic" and na.topic != nb.topic:
            return False
        if topic == "cross_topic" and na.topic == nb.topic:
            return False
        if cross_unit and na.unit_id == nb.unit_id:
            return False  # 整学期综合：搭档必须来自不同单元（区统考考的正是单元之间的综合）
        return not (grade_gap is not None and nb.grade < na.grade - grade_gap)

    def partners(
        self,
        anchor: str,
        scope: Iterable[str],
        *,
        limit: int = 20,
        topic: str = "any",
        grade_gap: int | None = None,
        cross_unit: bool = False,
    ) -> list[tuple[str, float]]:
        """锚点在范围内得分最高的伙伴知识点（P2 评测直接用它）。

        `topic`：`any` / `in_topic`（同一大主题，教材式搭配）/ `cross_topic`（跨主题，创新型综合）；
        `grade_gap`：伙伴年级不得比锚点低多少年以上（太基础的前置知识不值得单独考；复习场景传 None 不限制）。"""
        scored: list[tuple[str, float]] = []
        for b in scope:
            if not self._eligible(anchor, b, topic, grade_gap, cross_unit):
                continue
            sig = pair_signals(self.g, anchor, b, self.w)
            if (topic == "cross_topic" or cross_unit) and not (
                "edge" in sig or "cooccur" in sig or sig.get("shared", 0.0) >= self.w.bridge_shared
            ):
                continue  # 跨主题却没有任何"桥"：只是碰巧教学位置相近，拼在一起会牵强
            scored.append((b, sum(sig.values())))
        scored.sort(key=lambda x: (-x[1], x[0]))
        return scored[:limit]

    def mine(
        self,
        anchors: list[str],
        scope: set[str],
        *,
        k: int = 8,
        size: int = 2,
        exclude: Iterable[tuple[str, ...]] = (),
        topic: str = "any",
        grade_gap: int | None = 2,
        cross_unit: bool = False,
    ) -> list[Combo]:
        """返回至多 k 个组合，每个组合含至少一个锚点，其余成员来自 `scope`。

        不变量：所有成员都在 `scope` 内（锚点若不在 scope 内会被忽略）；同一伙伴最多出现在 `max_reuse` 个入选组合里；
        结果确定（同分按 ID 排序）。`exclude` 里的组合（无序）不会再出现（用于一套题里不重复用同一搭配）。
        """
        anchors = [a for a in anchors if a in scope and a in self.g.nodes]
        banned = {frozenset(x) for x in exclude}
        pool: list[tuple[float, tuple[str, ...]]] = []
        for a in anchors:
            for b, s in self.partners(
                a, scope, limit=max(12, 2 * k), topic=topic, grade_gap=grade_gap, cross_unit=cross_unit
            ):
                if s <= 0:
                    continue
                pool.append((s, (a, b)))
        if size >= 3:
            top = sorted(pool, key=lambda x: (-x[0], x[1]))[: 3 * k]
            tri: list[tuple[float, tuple[str, ...]]] = []
            for s, (a, b) in top:
                best: tuple[float, str] | None = None
                for c in scope:
                    if c in (a, b) or c not in self.g.nodes or not self._eligible(a, c, "any", grade_gap):
                        continue
                    sc = min(pair_score(self.g, a, c, self.w), pair_score(self.g, b, c, self.w))
                    if best is None or (sc, c) > best:
                        best = (sc, c)
                if best and best[0] > 0:
                    tri.append((s + best[0], (a, b, best[1])))
            pool = tri
        pool.sort(key=lambda x: (-x[0], x[1]))
        chosen: list[Combo] = []
        seen: set[frozenset[str]] = set()
        used: dict[str, int] = {}
        for s, ids in pool:
            fs = frozenset(ids)
            if fs in seen or fs in banned:
                continue
            partners = [i for i in ids if i not in anchors]
            if any(used.get(p, 0) >= self.w.max_reuse for p in partners):
                continue
            seen.add(fs)
            for p in partners:
                used[p] = used.get(p, 0) + 1
            chosen.append(
                Combo(kp_ids=list(ids), score=round(s, 4), rationale=_explain(self.g, list(ids), self.w))
            )
            if len(chosen) >= k:
                break
        return chosen

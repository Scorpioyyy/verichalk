"""组合挖掘评测（eval/specs/plan.md：P1、P2、P5 的数据面）。

P2（留出召回）：知识库里 303 个知识点对在教材里真实地"同题出现"。评测时把共现信号拿掉，以其中一个为锚点，
看组合挖掘能否把另一个排进前 k。基线：随机已学搭配（解析式期望）、教学位置最近邻。
全部确定性，无模型调用，因此可进回归测试。
"""

from __future__ import annotations

import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace

from ..knowledge import ComboMiner, ComboWeights, GraphData, KnowledgeService
from ..metrics.stats import wilson


@dataclass
class PairCase:
    anchor: str
    target: str
    scope: frozenset[str]
    cross_topic: bool


@dataclass
class RecallRow:
    name: str
    n: int
    recall: dict[int, float]
    lo: dict[int, float]
    hi: dict[int, float]
    mrr: float
    by_stratum: dict[str, dict[int, float]] = field(default_factory=dict)


KS = (5, 10, 20)


async def build_pair_cases(kb: KnowledgeService, g: GraphData) -> list[PairCase]:
    """教材跨点共现对 → 评测用例：双向各一例；范围 = 两个知识点都已学时（以较晚引入者的课时为准）的已学集合。"""
    cases: list[PairCase] = []
    cache: dict[str, frozenset[str]] = {}
    for (a, b), _n in sorted(g.cooccur.items()):
        later = a if g.nodes[a].position >= g.nodes[b].position else b
        lesson = g.nodes[later].lesson_id
        if lesson not in cache:
            cache[lesson] = frozenset(await kb.learned_before(lesson, inclusive=True))
        scope = cache[lesson]
        if a not in scope or b not in scope:
            continue
        cross = g.nodes[a].topic != g.nodes[b].topic
        cases.append(PairCase(a, b, scope, cross))
        cases.append(PairCase(b, a, scope, cross))
    return cases


def _rank(order: list[str], target: str) -> int | None:
    try:
        return order.index(target) + 1
    except ValueError:
        return None


Ranker = Callable[[PairCase], list[str]]


def evaluate_ranker(name: str, cases: list[PairCase], ranker: Ranker) -> RecallRow:
    ranks: list[int | None] = [_rank(ranker(c), c.target) for c in cases]
    return _summarize(name, cases, ranks)


def _summarize(name: str, cases: list[PairCase], ranks: list[int | None]) -> RecallRow:
    n = len(cases)
    rec: dict[int, float] = {}
    lo: dict[int, float] = {}
    hi: dict[int, float] = {}
    for k in KS:
        hit = sum(1 for r in ranks if r is not None and r <= k)
        rec[k] = hit / n if n else 0.0
        lo[k], hi[k] = wilson(hit, n)
    mrr = sum(1 / r for r in ranks if r) / n if n else 0.0
    strata: dict[str, dict[int, float]] = {}
    for label, pick in (("同主题", False), ("跨主题", True)):
        idx = [i for i, c in enumerate(cases) if c.cross_topic == pick]
        if idx:
            strata[label] = {k: sum(1 for i in idx if (ranks[i] or 10**9) <= k) / len(idx) for k in KS}
    return RecallRow(name, n, rec, lo, hi, mrr, strata)


def random_baseline(cases: list[PairCase]) -> RecallRow:
    """随机已学搭配：目标排在第 r 位的概率均匀，期望召回@k = k / |候选|（解析式，无需抽样）。"""
    rec = {k: sum(min(1.0, k / max(1, len(c.scope) - 1)) for c in cases) / len(cases) for k in KS}
    n = len(cases)
    strata: dict[str, dict[int, float]] = {}
    for label, pick in (("同主题", False), ("跨主题", True)):
        sub = [c for c in cases if c.cross_topic == pick]
        if sub:
            strata[label] = {
                k: sum(min(1.0, k / max(1, len(c.scope) - 1)) for c in sub) / len(sub) for k in KS
            }
    return RecallRow("随机已学搭配", n, rec, {k: 0.0 for k in KS}, {k: 0.0 for k in KS}, 0.0, strata)


def position_baseline(g: GraphData) -> Ranker:
    """教学位置最近邻：按"首次引入课时离锚点最近"排序（'刚学的内容放在一起'的朴素做法）。"""

    def rank(c: PairCase) -> list[str]:
        a = g.nodes[c.anchor]
        cand = [x for x in c.scope if x != c.anchor and x in g.nodes]
        cand.sort(key=lambda x: (abs(g.nodes[x].position - a.position), x))
        return cand

    return rank


def miner_ranker(
    g: GraphData, w: ComboWeights, *, topic: str = "any", grade_gap: int | None = None
) -> Ranker:
    miner = ComboMiner(g.without_cooccur(), w)

    def rank(c: PairCase) -> list[str]:
        return [
            b
            for b, s in miner.partners(c.anchor, c.scope, limit=max(KS) * 5, topic=topic, grade_gap=grade_gap)
            if s > 0
        ]

    return rank


def signal_ablation(g: GraphData, cases: list[PairCase], base: ComboWeights) -> list[RecallRow]:
    """逐项关闭打分信号（共现已在留出时拿掉）。"""
    rows = [evaluate_ranker("全部信号", cases, miner_ranker(g, base))]
    for sig in ("edge", "proximity", "thread", "shared", "scene"):
        rows.append(evaluate_ranker(f"去掉 {sig}", cases, miner_ranker(g, replace(base, **{sig: 0.0}))))
    for sig in ("edge", "proximity", "thread", "shared", "scene"):
        zero = dict.fromkeys(("edge", "proximity", "thread", "shared", "scene"), 0.0)
        only = replace(base, **{**zero, sig: getattr(base, sig)})
        rows.append(evaluate_ranker(f"仅 {sig}", cases, miner_ranker(g, only)))
    return rows


def render(rows: list[RecallRow]) -> str:
    head = "| 方法 | n | " + " | ".join(f"召回@{k}" for k in KS) + " | MRR | 同主题@10 | 跨主题@10 |"
    sep = "|---|---|" + "---|" * (len(KS) + 3)
    lines = [head, sep]
    for r in rows:
        cells = []
        for k in KS:
            ci = f" [{r.lo[k]:.2f},{r.hi[k]:.2f}]" if r.hi[k] else ""
            cells.append(f"{r.recall[k]:.3f}{ci}")
        s_in = r.by_stratum.get("同主题", {}).get(10)
        s_x = r.by_stratum.get("跨主题", {}).get(10)
        lines.append(
            f"| {r.name} | {r.n} | "
            + " | ".join(cells)
            + f" | {r.mrr:.3f} | "
            + (f"{s_in:.3f}" if s_in is not None else "—")
            + " | "
            + (f"{s_x:.3f}" if s_x is not None else "—")
            + " |"
        )
    return "\n".join(lines)


async def run_pair_eval(kb: KnowledgeService, w: ComboWeights) -> tuple[list[RecallRow], list[RecallRow]]:
    g = await kb.graph()
    cases = await build_pair_cases(kb, g)
    main = [
        random_baseline(cases),
        evaluate_ranker("教学位置最近邻", cases, position_baseline(g)),
        evaluate_ranker("组合挖掘（全部信号，不限主题）", cases, miner_ranker(g, w)),
        evaluate_ranker("组合挖掘（同主题）", cases, miner_ranker(g, w, topic="in_topic")),
        evaluate_ranker("组合挖掘（跨主题）", cases, miner_ranker(g, w, topic="cross_topic")),
    ]
    return main, signal_ablation(g, cases, w)


def sample_random_combos(
    g: GraphData, scope: frozenset[str], anchor: str, n: int, seed: int = 0
) -> list[list[str]]:
    """P5 的随机基线：在已学范围内、不限关系随机取搭配。"""
    rng = random.Random(seed)
    pool = sorted(x for x in scope if x != anchor and x in g.nodes)
    return [[anchor, rng.choice(pool)] for _ in range(n)]


AsyncRunner = Callable[[KnowledgeService], Awaitable[None]]

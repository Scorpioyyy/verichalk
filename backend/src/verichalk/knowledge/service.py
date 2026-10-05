"""知识层：对 `chalkbase.Curriculum` 的薄适配（architecture §8）。

- chalkbase 是同步 API：每次调用经 `asyncio.to_thread` 放入线程池。
- 返回面向 LLM 的紧凑模型（只含必要字段与教师可读名称），控制上下文预算。
- 每次调用产生一个 `tool` span；检索类调用同时发出 `retrieval.result` 事件供调试台绘制。
- 业务代码不得直接 import chalkbase（architecture §2 规则 3）。
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Callable, Iterable
from dataclasses import replace
from typing import Any, TypeVar

from .. import trace
from ..core.config import Profile, Settings, load_credentials
from ..core.errors import ConfigError, KnowledgeError, NotFound
from ..domain.knowledge import (
    ArchetypeBrief,
    BoundaryReportView,
    BoundaryView,
    ChainItem,
    ContextBrief,
    GraphEdge,
    GraphNode,
    KPDetail,
    KPHit,
    KPRef,
    RelationItem,
    RetrievalPayload,
    UnitRef,
    ViolationView,
)
from .embedding import QueryEmbedder
from .graph import GraphData, KPNode

T = TypeVar("T")
_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_ORD_RE = re.compile(r"^(?:第)?([一二三四五六七八九十]+)(?:单元)?")
_TEXT_CLIP = 260
_FORM_TO_KIND = {
    "compute": "calc",
    "fill_blank": "fill",
    "word_problem": "application",
    "judge": "judge",
    "choice": "choice",
}  # 其余题型（作图 / 测量 / 读图 / 其他）依赖图形，本版本不出


def configure_environment(settings: Settings) -> None:
    """chalkbase 的向量检索读取 `DASHSCOPE_API_KEY` / `DASHSCOPE_BASE_URL`。

    `intl` profile 下把新加坡的密钥与端点映射到这两个变量（只影响本进程）；`cn` profile 无需处理。
    """
    # 查询向量缓存放进评测录制目录：回放时无需密钥与网络也能得到相同的检索结果（评测可复现）
    os.environ.setdefault("CHALKBASE_CACHE", str(settings.cassette_path / "chalkbase_embeddings"))
    if settings.profile == Profile.intl:
        key, url = os.environ.get("DASHSCOPE_INTL_API_KEY"), os.environ.get("DASHSCOPE_INTL_BASE_URL")
        if key and url:
            os.environ["DASHSCOPE_API_KEY"], os.environ["DASHSCOPE_BASE_URL"] = key, url


def _clip(s: str, n: int = _TEXT_CLIP) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


class KnowledgeService:
    def __init__(self, cur: Any) -> None:
        self._cur = cur
        self._names: dict[str, str] = {}
        self._units: dict[str, list[UnitRef]] = {}
        self._embedder: QueryEmbedder | None = None
        self._kp_index: list[tuple[str, str, int]] | None = None  # (名称, 可检索文本, 首次引入年级)
        self._graph: GraphData | None = None
        self._vocab: dict[str, list[str]] | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> KnowledgeService:
        configure_environment(settings)
        from chalkbase import Curriculum  # 唯一允许导入 chalkbase 的位置

        svc = cls(Curriculum())  # pyright: ignore[reportCallIssue]  chalkbase 的惰性导出使类型推断失真
        svc._install_embedder(settings)
        return svc

    def _install_embedder(self, settings: Settings) -> None:
        """注入连接池复用的查询向量化（需要 chalkbase 提供 `set_embedder`，且在事件循环内、有密钥时才启用）。"""
        try:
            import chalkbase.query.embed as embed_mod
        except ImportError:  # pragma: no cover
            return
        set_embedder = getattr(embed_mod, "set_embedder", None)  # 动态获取：旧版 chalkbase 没有这个注入点
        if set_embedder is None:
            return  # 旧版 chalkbase：沿用它自带的同步实现
        try:
            loop = asyncio.get_running_loop()
            embedder = QueryEmbedder(load_credentials(settings.profile), loop)
        except (RuntimeError, ConfigError):
            return  # 不在事件循环里，或没有密钥（回放 / 离线）：不注入

        def sync_embed(texts: list[str]) -> list[list[float]]:
            try:
                return embedder(texts)
            except KnowledgeError as e:  # 向量接口不可用：让 chalkbase 降级为词法检索，而不是整个检索失败
                raise embed_mod.EmbeddingUnavailable(str(e)) from e

        set_embedder(sync_embed)
        self._embedder = embedder

    async def warm(self) -> None:
        """预热向量接口的连接（特性开关 `warmup` 控制是否调用）。"""
        if self._embedder is not None:
            await self._embedder.warm()

    @property
    def chalkbase_version(self) -> str:
        try:
            from importlib.metadata import version

            return version("chalkbase")
        except Exception:  # pragma: no cover
            return "unknown"

    @property
    def data_version(self) -> str:
        m = getattr(self._cur, "manifest", None) or {}
        return str(m.get("data_version", "unknown"))

    async def _run(self, fn: Callable[[], T]) -> T:
        try:
            return await asyncio.to_thread(fn)
        except KeyError as e:
            raise NotFound(f"知识库中没有：{e}") from e
        except Exception as e:  # chalkbase 的异常统一包装，细节保留在 message
            raise KnowledgeError(f"{type(e).__name__}: {e}") from e

    # ---- 基础转换 ----
    def _name(self, kp_id: str) -> str:
        n = self._names.get(kp_id)
        if n is None:
            try:
                n = self._cur.kp(kp_id).name
            except KeyError:
                n = kp_id
            self._names[kp_id] = n
        return n

    def _ref(self, kp_id: str) -> KPRef:
        kp = self._cur.kp(kp_id)
        loc = self._cur.locate(kp_id)
        lesson = self._cur.lesson(kp.first_introduced_lesson_id)
        return KPRef(
            id=kp.id,
            name=kp.name,
            grade=getattr(loc, "grade", None),
            semester=getattr(loc, "semester", None),
            domain=str(getattr(kp.domain, "value", kp.domain)),
            lesson_id=kp.first_introduced_lesson_id,
            lesson_title=getattr(lesson, "title", None),
            unit_title=getattr(loc, "unit_title", None),
        )

    # ---- 教材结构：册、单元、课时 ----
    @staticmethod
    def book_id(grade: int, semester: str) -> str:
        return f"g{grade}{semester}"

    @staticmethod
    def _ordinal(title: str) -> int | None:
        m = _ORD_RE.match(title.strip())
        if not m:
            return None
        cn = m.group(1)
        if cn == "十":
            return 10
        if cn.startswith("十"):
            return 10 + _CN_NUM.get(cn[1:], 0)
        return _CN_NUM.get(cn[0]) if len(cn) == 1 else None

    async def units(self, book_id: str) -> list[UnitRef]:
        """某册的单元（按教材顺序）。标题序号来自教材标题，不是 ID 序号。"""
        if book_id not in self._units:

            def _do() -> list[UnitRef]:
                order: list[str] = []
                first: dict[str, str] = {}
                last: dict[str, str] = {}
                title: dict[str, str] = {}
                for les in self._cur.lessons_of(book=book_id):
                    loc = self._cur.lesson_location(les.id)
                    if loc.unit_id not in first:
                        order.append(loc.unit_id)
                        first[loc.unit_id], title[loc.unit_id] = les.id, loc.unit_title
                    last[loc.unit_id] = les.id
                return [
                    UnitRef(
                        id=u,
                        title=title[u],
                        ordinal=self._ordinal(title[u]),
                        first_lesson_id=first[u],
                        last_lesson_id=last[u],
                    )
                    for u in order
                ]

            self._units[book_id] = await self._run(_do)
        return self._units[book_id]

    async def unit_by_ordinal(self, book_id: str, ordinal: int) -> UnitRef | None:
        return next((u for u in await self.units(book_id) if u.ordinal == ordinal), None)

    async def last_lesson(self, book_id: str) -> str | None:
        us = await self.units(book_id)
        return us[-1].last_lesson_id if us else None

    async def latest_lesson(self, lesson_ids: list[str]) -> str | None:
        """教学序列中最靠后的课时（学生"学到哪"取其中最晚的那个）。"""
        if not lesson_ids:
            return None
        return await self._run(lambda: max(lesson_ids, key=self._cur.lesson_position))

    async def topic_floor(self, phrase: str) -> tuple[int, str] | None:
        """知识库里所有名称 / 别名 / 主线含 `phrase` 的知识点中，最早被引入的年级及其名称；没有匹配返回 None。
        用于判断"X 年级的 Y"是否超出范围：Y 在知识库里最早出现在哪个年级。"""
        if self._kp_index is None:

            def _build() -> list[tuple[str, str, int]]:
                out = []
                for kp in self._cur.kps():
                    g = self._cur.kp_grade(kp.id)
                    if g:
                        out.append((kp.name, "|".join([kp.name, *kp.aliases, kp.thread]), g))
                return out

            self._kp_index = await self._run(_build)
        hits = [(g, name) for name, text, g in self._kp_index if phrase in text]
        return min(hits) if hits else None

    async def kp_texts(self, kp_ids: list[str]) -> dict[str, str]:
        """知识点的可检索文本（名称、别名、主线、主题），评测的 `topics_any` 与调试展示用。"""

        def _do() -> dict[str, str]:
            out = {}
            for k in kp_ids:
                try:
                    kp = self._cur.kp(k)
                except KeyError:
                    continue
                out[k] = "|".join([kp.name, *kp.aliases, kp.thread, kp.topic])
            return out

        return await self._run(_do)

    # ---- 关系图快照（组合挖掘用）----
    async def graph(self) -> GraphData:
        """全部知识点、关系边与教材跨点共现的快照（进程内只构建一次，约几十毫秒）。"""
        if self._graph is None:

            def _build() -> GraphData:
                cur = self._cur
                nodes: dict[str, KPNode] = {}
                for kp in cur.kps():
                    loc = cur.locate(kp.id)
                    nodes[kp.id] = KPNode(
                        id=kp.id,
                        name=kp.name,
                        grade=int(getattr(loc, "grade", 0) or 0),
                        semester=str(getattr(loc, "semester", "") or ""),
                        domain=str(getattr(kp.domain, "value", kp.domain)),
                        thread=str(kp.thread or ""),
                        topic=str(kp.topic or ""),
                        position=int(getattr(loc, "position", 0) or 0),
                        unit_id=str(getattr(loc, "unit_id", "") or ""),
                        lesson_id=str(getattr(loc, "lesson_id", "") or ""),
                        assessable=bool(getattr(kp, "is_assessable", True)),
                    )
                kinds: dict[str, set[str]] = {}
                n_ex: dict[str, int] = {}
                for a in cur.archetypes():
                    n_ex[a.primary_knowledge_point_id] = n_ex.get(a.primary_knowledge_point_id, 0) + len(
                        a.source_instance_ids or []
                    )
                    form = _FORM_TO_KIND.get(str(getattr(a.item_form, "value", a.item_form)))
                    ratio = ((a.parameter_constraints or {}).get("observed") or {}).get(
                        "requires_figure_ratio"
                    )
                    if form and (ratio or 0) < 0.5:
                        kinds.setdefault(a.primary_knowledge_point_id, set()).add(form)
                nodes = {
                    k: replace(n, kinds=frozenset(kinds.get(k, ())), n_exercises=n_ex.get(k, 0))
                    for k, n in nodes.items()
                }
                g = GraphData(nodes)
                for kid in nodes:
                    for e in cur.chain(
                        kid,
                        direction="prerequisite",
                        depth=1,
                        edge_types=("prerequisite", "builds_on"),
                        include_implied=False,
                    ):
                        g.add_edge(e.kp_id, kid, e.edge_type, directed=True)
                    for other, etype, _note in cur.relations(kid):
                        g.add_edge(kid, other, etype, directed=False)
                for a in cur.archetypes(include_secondary=True):
                    ids = [a.primary_knowledge_point_id, *a.secondary_knowledge_point_ids]
                    g.add_cooccur(ids)
                    themes = [c.removeprefix("ctx.") for c in a.allowed_contexts if "纯数学" not in c]
                    for kid in ids:
                        g.add_contexts(kid, themes)
                return g

            self._graph = await self._run(_build)
        return self._graph

    # ---- 检索 ----
    async def search(
        self, query: str, k: int = 8, *, grade: int | None = None, domain: str | None = None
    ) -> list[KPHit]:
        async with trace.tool_span("kb.search", query=query, k=k, grade=grade, domain=domain) as sp:

            def _do() -> list[KPHit]:
                hits = self._cur.search(query, k=k, grade=grade, domain=domain)
                return [
                    KPHit(
                        id=h.kp_id,
                        score=float(h.score),
                        name=h.name,
                        grade=h.grade,
                        semester=h.semester,
                        domain=str(h.domain),
                        lesson_id=h.lesson_id,
                        lesson_title=h.lesson_title,
                        unit_title=h.unit_title,
                    )
                    for h in hits
                ]

            hits = await self._run(_do)
            sp.set(n_hits=len(hits), top=[h.id for h in hits[:3]])
            await trace.retrieval(
                RetrievalPayload(
                    step="search",
                    query=query,
                    nodes=[
                        GraphNode(
                            id=h.id,
                            name=h.name,
                            grade=h.grade,
                            semester=h.semester,
                            domain=h.domain,
                            role="candidate",
                            score=h.score,
                        )
                        for h in hits
                    ],
                )
            )
            return hits

    async def kp(self, kp_id: str) -> KPDetail:
        async with trace.tool_span("kb.kp", kp_id=kp_id):

            def _do() -> KPDetail:
                ref = self._ref(kp_id)
                kp = self._cur.kp(kp_id)
                return KPDetail(
                    **ref.model_dump(),
                    aliases=list(kp.aliases),
                    thread=kp.thread,
                    topic=kp.topic,
                    description=_clip(kp.description, 400),
                    mastery_level=str(getattr(kp.mastery_level, "value", kp.mastery_level)),
                    typical_errors=[_clip(e, 80) for e in kp.typical_errors[:4]],
                    is_assessable=kp.is_assessable,
                )

            return await self._run(_do)

    async def find(self, name: str) -> list[KPRef]:
        async with trace.tool_span("kb.find", query=name):
            return await self._run(lambda: [self._ref(k.id) for k in self._cur.find_kp(name)])

    # ---- 图 ----
    async def chain(
        self,
        kp_id: str,
        *,
        direction: str = "prerequisite",
        depth: int = 2,
        edge_types: Iterable[str] | None = None,
        limit: int = 30,
    ) -> list[ChainItem]:
        async with trace.tool_span("kb.chain", kp_id=kp_id, direction=direction, depth=depth) as sp:

            def _do() -> list[ChainItem]:
                kw: dict[str, Any] = {"direction": direction, "depth": depth}
                if edge_types:
                    kw["edge_types"] = tuple(edge_types)
                entries = self._cur.chain(kp_id, **kw)[:limit]
                return [
                    ChainItem(
                        kp_id=e.kp_id,
                        name=self._name(e.kp_id),
                        depth=e.depth,
                        edge_type=e.edge_type,
                        via=e.via,
                        implied=e.implied,
                    )
                    for e in entries
                ]

            items = await self._run(_do)
            sp.set(n=len(items))
            anchor = await self._run(lambda: self._ref(kp_id))
            await trace.retrieval(
                RetrievalPayload(
                    step=f"chain:{direction}",
                    query=kp_id,
                    nodes=[
                        GraphNode(
                            id=anchor.id,
                            name=anchor.name,
                            grade=anchor.grade,
                            semester=anchor.semester,
                            domain=anchor.domain,
                            role="anchor",
                        )
                    ]
                    + [GraphNode(id=i.kp_id, name=i.name, role="context") for i in items],
                    edges=[
                        GraphEdge(source=(i.via or kp_id), target=i.kp_id, type=i.edge_type) for i in items
                    ],
                )
            )
            return items

    async def relations(
        self, kp_id: str, types: Iterable[str] = ("related", "confusable", "extends")
    ) -> list[RelationItem]:
        async with trace.tool_span("kb.relations", kp_id=kp_id):

            def _do() -> list[RelationItem]:
                return [
                    RelationItem(kp_id=o, name=self._name(o), type=t, note=str(note))
                    for (o, t, note) in self._cur.relations(kp_id, types=tuple(types))
                ]

            return await self._run(_do)

    async def learned_before(self, lesson_id: str, *, inclusive: bool = False) -> set[str]:
        async with trace.tool_span("kb.learned_before", lesson_id=lesson_id) as sp:
            ids = await self._run(lambda: set(self._cur.learned_before(lesson_id, inclusive=inclusive)))
            sp.set(n=len(ids))
            return ids

    async def review_candidates(
        self, lesson_id: str, target_kp_ids: list[str] | None = None, depth: int = 3, limit: int = 20
    ) -> list[KPRef]:
        async with trace.tool_span("kb.review_candidates", lesson_id=lesson_id):

            def _do() -> list[KPRef]:
                got = self._cur.review_candidates(lesson_id, target_kp_ids=target_kp_ids, depth=depth)
                return [
                    self._ref(x if isinstance(x, str) else getattr(x, "kp_id", getattr(x, "id", "")))
                    for x in got[:limit]
                ]

            return await self._run(_do)

    # ---- 边界 ----
    async def boundary(self, lesson_id: str) -> BoundaryView:
        async with trace.tool_span("kb.boundary", lesson_id=lesson_id):

            def _do() -> BoundaryView:
                b = self._cur.boundary(lesson_id)
                concepts = sorted(b.concepts)
                return BoundaryView(
                    lesson_id=lesson_id,
                    integer_domain_max=b.integer_domain_max,
                    decimal_max_places=b.decimal_max_places,
                    fraction_types=sorted(b.fraction_types),
                    operations={k: sorted(v) for k, v in b.operation_operand_forms.items()},
                    n_concepts=len(concepts),
                    concepts=concepts[:60],
                    units=sorted(b.units_of_measure),
                    geometry_vocab=sorted(b.geometry_vocab),
                )

            return await self._run(_do)

    async def check_item(self, features: dict[str, Any], lesson_id: str) -> BoundaryReportView:
        async with trace.tool_span("kb.check_item", lesson_id=lesson_id) as sp:

            def _do() -> BoundaryReportView:
                rep = self._cur.check_item(features, lesson_id)
                return BoundaryReportView(
                    verdict=rep.verdict,
                    violations=[
                        ViolationView(
                            dimension=v.dimension,
                            item_value=str(v.item_value),
                            allowed=str(getattr(v, "allowed", "")),
                            introduced_at=getattr(v, "introduced_at", None),
                            detail=str(getattr(v, "detail", "")),
                        )
                        for v in rep.violations
                    ],
                    unknown=[str(u) for u in getattr(rep, "unknown", [])],
                )

            rep = await self._run(_do)
            sp.set(verdict=rep.verdict, n_violations=len(rep.violations))
            return rep

    async def feature_vocab(self) -> dict[str, list[str]]:
        """特征抽取的受控词表：分数类型、运算、计量单位、几何词汇与知识点名称菜单（用于抽取提示词的稳定段）。"""
        if self._vocab is None:

            def _do() -> dict[str, list[str]]:
                from chalkbase.boundary import vocab  # 内部模块：只在这里使用，版本由 <0.2 约束

                return {
                    "fraction_types": list(vocab.FRACTION_TYPES),
                    "ops": list(vocab.OPS),
                    "units": list(vocab.UNITS),
                    "geometry": list(vocab.GEOMETRY_SEED),
                    "kp_names": [kp.name for kp in self._cur.kps()],
                }

            self._vocab = await self._run(_do)
        return self._vocab

    async def check_features(self, raw: dict[str, Any], lesson_id: str) -> BoundaryReportView:
        """把模型抽取的特征（JSON 对象）规范化后做能力边界判定。无法识别的取值进入 `unknown`，不计入越界。"""
        vocab = await self.feature_vocab()

        def _do() -> BoundaryReportView:
            from chalkbase.boundary.extract import to_features  # 内部函数：容错地把 JSON 转为 ItemFeatures

            feats = to_features(raw, set(vocab["kp_names"]))
            rep = self._cur.check_item(feats, lesson_id)
            return BoundaryReportView(
                verdict=rep.verdict,
                violations=[
                    ViolationView(
                        dimension=v.dimension,
                        item_value=str(v.item_value),
                        allowed=str(getattr(v, "allowed", "")),
                        introduced_at=getattr(v, "introduced_at", None),
                        detail=str(getattr(v, "detail", "")),
                    )
                    for v in rep.violations
                ],
                unknown=[str(u) for u in getattr(rep, "unknown", [])],
            )

        async with trace.tool_span("kb.check_features", lesson_id=lesson_id) as sp:
            rep = await self._run(_do)
            sp.set(verdict=rep.verdict, n_violations=len(rep.violations))
            return rep

    async def audit_boundary(self, problem_text: str, lesson_id: str) -> BoundaryReportView:
        """评测审计用：chalkbase 自带的特征抽取（qwen3.7-plus 思考模式，经它自己的客户端）+ `check_item`。
        与线上核验走的是不同的模型与提示，所以可以作为线上结论的独立对照。需要 `chalkbase[llm]` 与密钥。"""

        def _do() -> BoundaryReportView:
            from chalkbase.boundary.extract import extract_features

            feats = extract_features(problem_text)
            rep = self._cur.check_item(feats, lesson_id)
            return BoundaryReportView(
                verdict=rep.verdict,
                violations=[
                    ViolationView(
                        dimension=v.dimension,
                        item_value=str(v.item_value),
                        allowed=str(getattr(v, "allowed", "")),
                        introduced_at=getattr(v, "introduced_at", None),
                        detail=str(getattr(v, "detail", "")),
                    )
                    for v in rep.violations
                ],
                unknown=[str(u) for u in getattr(rep, "unknown", [])],
            )

        return await self._run(_do)

    # ---- 题型与情境（作为上下文，不是模板）----
    async def archetypes_for(
        self, kp_id: str, *, limit: int = 6, include_secondary: bool = True
    ) -> list[ArchetypeBrief]:
        async with trace.tool_span("kb.archetypes_for", kp_id=kp_id):

            def _do() -> list[ArchetypeBrief]:
                ats = self._cur.archetypes(kp_id=kp_id, include_secondary=include_secondary)
                ats = sorted(ats, key=lambda a: (-len(a.source_instance_ids or []), a.difficulty))[:limit]
                return [
                    ArchetypeBrief(
                        id=a.id,
                        kp_id=a.primary_knowledge_point_id,
                        secondary_kp_ids=list(a.secondary_knowledge_point_ids),
                        item_form=str(getattr(a.item_form, "value", a.item_form)),
                        difficulty=a.difficulty,
                        verifiable_type=str(getattr(a.verifiable_type, "value", a.verifiable_type)),
                        template=_clip(a.template, 200),
                        typical_errors=[_clip(e, 60) for e in a.typical_errors[:3]],
                        figure_ratio=((a.parameter_constraints or {}).get("observed") or {}).get(
                            "requires_figure_ratio"
                        ),
                        examples=[
                            _clip(str(ex.get("problem", "")), 160)
                            for ex in (a.rewritten_examples or [])[:3]
                            if isinstance(ex, dict)
                        ],
                    )
                    for a in ats
                ]

            return await self._run(_do)

    async def contexts_for(
        self, grade: int | None = None, text: str | None = None, limit: int = 8
    ) -> list[ContextBrief]:
        async with trace.tool_span("kb.contexts", grade=grade, text=text):

            def _do() -> list[ContextBrief]:
                out = []
                for c in self._cur.contexts(grade=grade, text=text)[:limit]:
                    vr = c.value_ranges or {}
                    rng = (vr.get("numbers_in_texts_by_grade") or {}).get(str(grade), {}) if grade else {}
                    out.append(
                        ContextBrief(
                            id=c.id,
                            theme=c.theme,
                            definition=_clip(str(vr.get("definition", "")), 120),
                            applicable_grades=list(c.applicable_grades),
                            typical_quantities=list(vr.get("typical_quantities", []))[:6],
                            number_range={k: str(v) for k, v in rng.items() if k != "n_numbers"},
                        )
                    )
                return out

            return await self._run(_do)

    async def instantiate(
        self, archetype_id: str, *, seed: int = 0, lesson_id: str | None = None, only_in_bounds: bool = False
    ) -> dict[str, Any]:
        """`template` 来源：题型实例化。返回 JSON 友好字典（`Problem.to_dict()`）。"""
        async with trace.tool_span(
            "kb.instantiate", archetype_id=archetype_id, seed=seed, lesson_id=lesson_id
        ):
            return await self._run(
                lambda: self._cur.instantiate(
                    archetype_id, seed=seed, lesson_id=lesson_id, only_in_bounds=only_in_bounds
                ).to_dict()
            )

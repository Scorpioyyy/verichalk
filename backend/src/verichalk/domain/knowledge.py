"""知识层返回给阶段 / 工具 / 调试台的紧凑数据模型（面向 LLM 的上下文预算，只含必要字段）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class KPRef(BaseModel):
    id: str
    name: str
    grade: int | None = None
    semester: str | None = None  # "a" 上册 / "b" 下册（与 chalkbase 一致）
    domain: str = ""
    lesson_id: str | None = None
    lesson_title: str | None = None
    unit_title: str | None = None


class KPHit(KPRef):
    score: float = 0.0


class GraphNode(BaseModel):
    """检索图中的节点（调试台"图检索视图"直接绘制）。"""

    id: str
    name: str
    grade: int | None = None
    semester: str | None = None
    domain: str = ""
    role: Literal["anchor", "candidate", "selected", "context"] = "candidate"
    score: float | None = None
    note: str = ""


class GraphEdge(BaseModel):
    source: str
    target: str
    type: str  # prerequisite / builds_on / extends / related / confusable / cooccur
    weight: float | None = None


class Combo(BaseModel):
    """一个知识点组合候选（"串联"的单位）。"""

    kp_ids: list[str]
    score: float
    rationale: str = ""


class RetrievalPayload(BaseModel):
    """一次知识检索 / 规划步骤的可视化载荷（事件 `retrieval.result`）。"""

    step: str  # 如 anchor / expand / combos / boundary
    query: str = ""
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    combos: list[Combo] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class KPDetail(KPRef):
    """知识点详情（供 LLM 理解"这个知识点具体教什么、学生常错什么"）。"""

    aliases: list[str] = Field(default_factory=list)
    thread: str = ""
    topic: str = ""
    description: str = ""
    mastery_level: str = ""
    typical_errors: list[str] = Field(default_factory=list)
    is_assessable: bool = True


class ChainItem(BaseModel):
    kp_id: str
    name: str
    depth: int
    edge_type: str
    via: str | None = None
    implied: bool = False


class RelationItem(BaseModel):
    kp_id: str
    name: str
    type: str  # related / confusable / extends
    note: str = ""


class BoundaryView(BaseModel):
    """某课时之前学生具备的能力（紧凑）。"""

    lesson_id: str
    integer_domain_max: int | None = None
    decimal_max_places: int | None = None
    fraction_types: list[str] = Field(default_factory=list)
    operations: dict[str, list[str]] = Field(default_factory=dict)
    n_concepts: int = 0
    concepts: list[str] = Field(default_factory=list)  # 过长时截断
    units: list[str] = Field(default_factory=list)
    geometry_vocab: list[str] = Field(default_factory=list)


class ViolationView(BaseModel):
    dimension: str
    item_value: str
    allowed: str = ""
    introduced_at: str | None = None
    detail: str = ""


class BoundaryReportView(BaseModel):
    verdict: Literal["in", "borderline", "out"]
    violations: list[ViolationView] = Field(default_factory=list)
    unknown: list[str] = Field(default_factory=list)


class ArchetypeBrief(BaseModel):
    """题型卡片摘要：在生成环节作为"学生做过什么"的上下文，不是模板。"""

    id: str
    kp_id: str
    secondary_kp_ids: list[str] = Field(default_factory=list)
    item_form: str = ""
    difficulty: int = 3
    verifiable_type: str = ""
    template: str = ""
    typical_errors: list[str] = Field(default_factory=list)


class ContextBrief(BaseModel):
    id: str
    theme: str
    definition: str = ""
    applicable_grades: list[int] = Field(default_factory=list)
    typical_quantities: list[str] = Field(default_factory=list)
    number_range: dict[str, str] = Field(
        default_factory=dict
    )  # 该年级下的数值范围（min/max/max_decimal_places）


class UnitRef(BaseModel):
    """教材单元。`ordinal` 是标题里的序号（"三 小数乘法"→3），与 ID 里的序号不一定一致（中间可能插入"整理与复习"）。"""

    id: str
    title: str
    ordinal: int | None = None
    first_lesson_id: str
    last_lesson_id: str

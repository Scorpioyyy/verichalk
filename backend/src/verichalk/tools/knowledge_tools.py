"""只读的知识检索工具（供规划阶段的有界工具循环使用）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .registry import Tool, ToolContext, ToolRegistry


class SearchArgs(BaseModel):
    query: str = Field(description="自然语言或知识点名称，如“小数加减法”“三角形内角和”")
    k: int = Field(default=6, ge=1, le=12)
    grade: int | None = Field(default=None, ge=1, le=6, description="只看某个年级首次引入的知识点")


class KpArgs(BaseModel):
    kp_id: str = Field(description="知识点 ID，必须来自检索结果，不要自己编造")


class ChainArgs(BaseModel):
    kp_id: str
    direction: Literal["prerequisite", "dependents"] = "prerequisite"
    depth: int = Field(default=2, ge=1, le=3)


class ArchetypeArgs(BaseModel):
    kp_id: str
    limit: int = Field(default=4, ge=1, le=8)


class ReviewArgs(BaseModel):
    lesson_id: str = Field(description="当前所学课时 ID，如 g4b.u1.l06")
    target_kp_ids: list[str] = Field(default_factory=list, max_length=6)


class BoundaryArgs(BaseModel):
    lesson_id: str


async def _search(a: SearchArgs, c: ToolContext):
    return await c.kb.search(a.query, k=a.k, grade=a.grade)


async def _kp(a: KpArgs, c: ToolContext):
    return await c.kb.kp(a.kp_id)


async def _chain(a: ChainArgs, c: ToolContext):
    return await c.kb.chain(a.kp_id, direction=a.direction, depth=a.depth, limit=20)


async def _relations(a: KpArgs, c: ToolContext):
    return await c.kb.relations(a.kp_id)


async def _archetypes(a: ArchetypeArgs, c: ToolContext):
    return await c.kb.archetypes_for(a.kp_id, limit=a.limit)


async def _review(a: ReviewArgs, c: ToolContext):
    return await c.kb.review_candidates(a.lesson_id, a.target_kp_ids or None, limit=12)


async def _boundary(a: BoundaryArgs, c: ToolContext):
    return await c.kb.boundary(a.lesson_id)


def register_knowledge_tools(reg: ToolRegistry) -> ToolRegistry:
    reg.register(
        Tool("kb_search", "按需求或名称检索知识点，返回命中的知识点及其所在年级与课时。", SearchArgs, _search)
    )
    reg.register(Tool("kb_kp", "查看一个知识点的详情：教什么、掌握层级、学生常见错误。", KpArgs, _kp))
    reg.register(
        Tool(
            "kb_chain",
            "沿关系图查看一个知识点的前置（学它之前要会什么）或后续（它之后学什么）。",
            ChainArgs,
            _chain,
        )
    )
    reg.register(Tool("kb_relations", "查看一个知识点的相关、易混淆、螺旋扩展关系。", KpArgs, _relations))
    reg.register(
        Tool(
            "kb_archetypes",
            "查看教材中该知识点的典型题型（仅作为“学生做过什么”的参考，不得照抄）。",
            ArchetypeArgs,
            _archetypes,
        )
    )
    reg.register(
        Tool("kb_review", "螺旋复习候选：某课时前已学、且在目标知识点前置链上的知识点。", ReviewArgs, _review)
    )
    reg.register(
        Tool(
            "kb_boundary",
            "查看某课时之前学生具备的能力范围：整数范围、小数位数、分数类型、运算形态。",
            BoundaryArgs,
            _boundary,
        )
    )
    return reg


def default_registry() -> ToolRegistry:
    return register_knowledge_tools(ToolRegistry())

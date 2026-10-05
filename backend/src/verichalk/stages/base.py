"""阶段框架：带类型输入输出的异步函数 + 统一的运行包装（architecture §5）。

`run_stage` 负责：开 span、记录耗时与错误、校验输出类型、把输出快照存入运行状态（支持暂停恢复与"从某阶段重放"）。
阶段之间只通过领域模型交换数据；阶段内部可以自由使用 llm / knowledge / sandbox / tools。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from .. import trace
from ..core.config import Settings
from ..core.errors import StageError
from ..domain.brief import Brief
from ..domain.events import CheckpointKind
from ..domain.run import Message
from ..knowledge import KnowledgeService
from ..llm import LLMGateway
from ..store import Store
from ..tools import ToolContext, ToolRegistry, default_registry

In = TypeVar("In", bound=BaseModel)
Out = TypeVar("Out", bound=BaseModel)

AskFn = Callable[[CheckpointKind, str, list[dict[str, Any]], dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass
class RunContext:
    """一次运行的上下文：依赖注入 + 阶段输出快照 + 检查点入口。"""

    run_id: str
    session_id: str
    settings: Settings
    llm: LLMGateway
    kb: KnowledgeService
    store: Store
    tools: ToolRegistry = field(default_factory=default_registry)
    state: dict[str, Any] = field(default_factory=dict)  # 阶段输出快照（可 JSON 序列化）
    reuse: set[str] = field(default_factory=set)  # 重放时直接复用快照的阶段键
    history: list[Message] = field(default_factory=list)
    has_paper: bool = False  # 会话里是否已有试卷（决定"太简单了"是修改还是重出）
    prev_brief: Brief | None = None  # 上一轮成功的需求（继承范围用）
    ask_fn: AskFn | None = None

    @property
    def tool_ctx(self) -> ToolContext:
        return ToolContext(kb=self.kb)

    async def ask(
        self,
        kind: CheckpointKind,
        prompt: str,
        options: list[dict[str, Any]] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """暂停运行，向用户请求澄清 / 确认；用户回应后返回其答复。"""
        if self.ask_fn is None:
            raise StageError("checkpoint", "当前运行环境不支持检查点")
        return await self.ask_fn(kind, prompt, options or [], payload or {})


class Stage(Generic[In, Out]):
    """阶段基类。子类声明 `name`、`input_model`、`output_model` 并实现 `run`。"""

    name: str
    input_model: type[In]
    output_model: type[Out]

    async def run(self, ctx: RunContext, inp: In) -> Out:  # pragma: no cover - 抽象方法
        raise NotImplementedError


async def run_stage(ctx: RunContext, stage: Stage[In, Out], inp: In, *, key: str | None = None) -> Out:
    skey = f"{stage.name}:{key}" if key else stage.name
    if skey in ctx.reuse and skey in ctx.state:
        async with trace.stage_span(stage.name, key=key, reused=True):
            return stage.output_model.model_validate(ctx.state[skey])
    async with trace.stage_span(stage.name, key=key) as sp:
        out = await stage.run(ctx, inp)
        if not isinstance(out, stage.output_model):
            raise StageError(
                stage.name, f"阶段输出类型错误：期望 {stage.output_model.__name__}，得到 {type(out).__name__}"
            )
        ctx.state[skey] = out.model_dump(mode="json")
        sp.set(output_keys=list(ctx.state[skey].keys())[:12])
        return out

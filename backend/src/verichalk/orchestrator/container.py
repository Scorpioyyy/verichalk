"""依赖容器：把设置装配成一组可注入的服务（API 与评测运行器共用）。"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.config import Settings
from ..knowledge import KnowledgeService
from ..llm import LLMGateway, build_gateway
from ..store import Store
from ..trace import EventBus
from .manager import RunManager
from .papers import PaperService
from .warmup import Warmer


@dataclass
class Container:
    settings: Settings
    store: Store
    bus: EventBus
    kb: KnowledgeService
    llm: LLMGateway
    manager: RunManager
    warmer: Warmer
    papers: PaperService = field(init=False)

    def __post_init__(self) -> None:
        self.papers = PaperService(self.settings, self.store)

    async def close(self) -> None:
        await self.warmer.wait()
        await self.manager.shutdown()
        await self.store.close()


async def build_container(
    settings: Settings,
    *,
    kb: KnowledgeService | None = None,
    llm: LLMGateway | None = None,
    store: Store | None = None,
) -> Container:
    """装配依赖；传入的替身（kb / llm / store）用于测试与评测回放。"""
    store = store or await Store.open(settings.data_path / "verichalk.db")
    bus = EventBus()
    kb = kb or KnowledgeService.from_settings(settings)
    llm = llm or build_gateway(settings)
    manager = RunManager(settings, store, bus, kb, llm)
    await manager.recover()
    return Container(settings, store, bus, kb, llm, manager, Warmer(settings, llm, kb))

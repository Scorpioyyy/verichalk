"""按设置装配网关。"""

from __future__ import annotations

from ..core.config import LLMMode, Settings, load_credentials
from ..core.errors import ConfigError
from .gateway import LLMGateway
from .registry import ModelRegistry, PriceTable
from .transport import HttpxTransport, Transport


def build_gateway(settings: Settings, *, transport: Transport | None = None) -> LLMGateway:
    registry = ModelRegistry.load(settings.config_dir, settings.profile)
    prices = PriceTable.load(settings.config_dir)
    if transport is None and settings.llm_mode != LLMMode.replay:
        try:
            transport = HttpxTransport(load_credentials(settings.profile))
        except ConfigError:
            if settings.llm_mode != LLMMode.replay_or_live:  # replay_or_live 允许无密钥（只回放）
                raise
    return LLMGateway(settings, registry, prices, transport)

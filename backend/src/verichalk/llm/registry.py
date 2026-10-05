"""角色注册表与价格表。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from ..core.config import Profile
from ..core.errors import ConfigError
from ..domain.llm import Role, Usage


class RoleSpec(BaseModel):
    model: str
    thinking: bool
    temperature: float = 0.3
    send_temperature: bool = True  # 个别模型（如 kimi-k3）只接受固定温度，传参会报错：此时不发送 temperature
    max_tokens: int = 2048
    timeout_s: float | None = None
    extra: dict[str, Any] = Field(default_factory=dict)  # 透传给请求体的额外参数


class ModelRegistry:
    def __init__(self, roles: dict[str, RoleSpec]) -> None:
        missing = [r.value for r in Role if r.value not in roles]
        if missing:
            raise ConfigError(f"models.yaml 缺少角色：{missing}")
        self._roles = roles

    @classmethod
    def load(cls, config_dir: Path, profile: Profile) -> ModelRegistry:
        data = yaml.safe_load((config_dir / "models.yaml").read_text(encoding="utf-8"))
        merged: dict[str, dict[str, Any]] = {k: dict(v) for k, v in (data.get("roles") or {}).items()}
        for role, patch in ((data.get("profiles") or {}).get(profile.value) or {}).items():
            merged.setdefault(role, {}).update(patch)
        for role in list(merged):  # 环境变量覆盖：VERICHALK_MODEL_FAST=...
            env = os.environ.get(f"VERICHALK_MODEL_{role.upper()}")
            if env:
                merged[role]["model"] = env
        return cls({k: RoleSpec(**v) for k, v in merged.items()})

    def role(self, role: Role | str) -> RoleSpec:
        key = role.value if isinstance(role, Role) else role
        try:
            return self._roles[key]
        except KeyError:
            raise ConfigError(f"未知模型角色：{key}") from None

    def override(self, role: Role | str, **changes: Any) -> ModelRegistry:
        """返回覆盖了某角色参数的新注册表（评测对比用，不修改原对象）。"""
        key = role.value if isinstance(role, Role) else role
        roles = dict(self._roles)
        roles[key] = roles[key].model_copy(update=changes)
        return ModelRegistry(roles)

    def models(self) -> dict[str, str]:
        return {k: v.model for k, v in self._roles.items()}


class Price(BaseModel):
    input: float
    output: float
    cached_input: float | None = None


class PriceTable:
    def __init__(self, models: dict[str, Price], currency: str = "CNY", cached_ratio: float = 0.2) -> None:
        self._models, self.currency, self._ratio = models, currency, cached_ratio

    @classmethod
    def load(cls, config_dir: Path) -> PriceTable:
        data = yaml.safe_load((config_dir / "pricing.yaml").read_text(encoding="utf-8"))
        return cls(
            {k: Price(**v) for k, v in (data.get("models") or {}).items()},
            data.get("currency", "CNY"),
            float(data.get("cached_input_ratio", 0.2)),
        )

    def cost(self, model: str, usage: Usage) -> float | None:
        """按价格表计算成本；价格未知返回 None（不猜测）。命中缓存的输入按折扣价计。"""
        p = self._models.get(model)
        if p is None:
            return None
        cached_price = p.cached_input if p.cached_input is not None else p.input * self._ratio
        uncached = max(usage.prompt_tokens - usage.cached_tokens, 0)
        return (
            uncached * p.input + usage.cached_tokens * cached_price + usage.completion_tokens * p.output
        ) / 1e6

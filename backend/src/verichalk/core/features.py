"""特性开关：每个会增加延迟、成本或复杂度的模块都注册为可关闭的开关，供消融实验使用（evaluation.md §9，D28）。

约定：
- 开关默认开启；关闭后系统必须仍能运行（降级为更简单的做法）。
- 新模块进入主路径时在这里注册，并在同一里程碑内完成首轮消融。
- 运行时通过 `settings.features.enabled("name")` 判断；评测用 `--off a,b` 或环境变量 `VERICHALK_OFF=a,b` 关闭。
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import ConfigError


@dataclass(frozen=True)
class Feature:
    name: str
    description: str


# 随实现补全；不预先注册尚不存在的模块（CLAUDE.md §2.7）
REGISTRY: dict[str, Feature] = {
    f.name: f
    for f in [
        Feature("warmup", "启动与页面打开时预热：建立模型连接、写入前缀缓存、预加载检索器"),
    ]
}


@dataclass(frozen=True)
class FeatureFlags:
    off: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        unknown = self.off - REGISTRY.keys()
        if unknown:
            raise ConfigError(f"未知的特性开关：{sorted(unknown)}（已注册：{sorted(REGISTRY)}）")

    def enabled(self, name: str) -> bool:
        if name not in REGISTRY:
            raise ConfigError(f"未注册的特性开关：{name}")
        return name not in self.off

    @classmethod
    def parse(cls, text: str) -> FeatureFlags:
        return cls(frozenset(x.strip() for x in text.split(",") if x.strip()))

    def describe(self) -> str:
        return "全部开启" if not self.off else "关闭：" + "、".join(sorted(self.off))

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
        Feature("warmup", "启动与页面打开时预热：建立模型连接、写入前缀缓存"),
        Feature("understand.llm", "意图理解使用语言模型；关闭则退化为规则解析（基线）"),
        Feature("understand.parallel_search", "知识点检索与模型解析并行；关闭则串行"),
        Feature("understand.topic_research", "解析出的主题与检索结果对不上时，按主题重新检索"),
        Feature("understand.fewshot", "理解提示词包含示例段"),
        Feature("understand.context", "理解时带上会话上下文（上一轮需求、是否已有试卷）"),
        Feature("plan.combo_miner", "综合题的知识点搭配由图上的组合挖掘提供；关闭则取检索相近的知识点"),
        Feature("plan.llm", "规划模型为每道题选择搭配、构想情境与问法；关闭则用确定性分配"),
        Feature("produce.program_check", "核验：求解程序在沙箱里执行，结果须与题目答案一致"),
        Feature("produce.blind_solve", "核验：独立模型盲解（看不到答案），结果须与题目答案一致"),
        Feature("produce.boundary_check", "核验：特征抽取 + 能力边界判定（是否超纲）"),
        Feature("produce.quality_check", "核验：题面质量与解析一致性、综合题是否真综合（判官）"),
        Feature(
            "produce.repair", "核验未通过时带失败证据修复（≤ 2 次）并在用尽后重写一次；关闭则一次不过即废弃"
        ),
        Feature("context.textbook_examples", "写题上下文里给出教材题型的改写示例（学生做过什么）"),
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

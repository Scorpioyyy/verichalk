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
    default: bool = True  # False = 选择性开启（实验性链路，由 `pipeline=design` 预设或 `on` 开启）


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
        Feature(
            "plan.design",
            "规划为每道题设计考法结构与要暴露的典型错误（规划提示词 v2，读取知识点的易错点）",
            default=False,
        ),
        Feature(
            "produce.design_prompt",
            '写题提示词带"什么是好题"、考法结构、易错点与难度的结构要求',
            default=False,
        ),
        Feature("produce.difficulty_check", "题面评审估计的难度比要求低 2 级以上时判不合格", default=False),
        Feature(
            "plan.cross_unit",
            "整学期 / 整年级综合：综合题的搭配优先选同一学期、不同单元、不同主题且有桥的知识点",
            default=False,
        ),
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


# 预设链路（一键切换）：`classic` 是 M3 中期（mid1）验证过的稳定链路；`design` 在其上叠加"好题设计"类开关。
PIPELINES = ("classic", "design")
DESIGN_FLAGS = frozenset(f.name for f in REGISTRY.values() if not f.default)


@dataclass(frozen=True)
class FeatureFlags:
    off: frozenset[str] = frozenset()
    on: frozenset[str] = frozenset()  # 选择性开启的开关（default=False 的那些）
    pipeline: str = "classic"

    def __post_init__(self) -> None:
        unknown = (self.off | self.on) - REGISTRY.keys()
        if unknown:
            raise ConfigError(f"未知的特性开关：{sorted(unknown)}（已注册：{sorted(REGISTRY)}）")
        if self.pipeline not in PIPELINES:
            raise ConfigError(f"未知的链路预设：{self.pipeline}（可选：{', '.join(PIPELINES)}）")

    def enabled(self, name: str) -> bool:
        feat = REGISTRY.get(name)
        if feat is None:
            raise ConfigError(f"未注册的特性开关：{name}")
        if name in self.off:
            return False
        if feat.default:
            return True
        return name in self.on or self.pipeline == "design"

    @classmethod
    def parse(cls, text: str, on: str = "", pipeline: str = "classic") -> FeatureFlags:
        def split(t: str) -> frozenset[str]:
            return frozenset(x.strip() for x in t.split(",") if x.strip())

        return cls(split(text), split(on), pipeline)

    def describe(self) -> str:
        bits = [f"链路 {self.pipeline}"] if self.pipeline != "classic" else []  # 默认链路不加前缀
        if self.on:
            bits.append("额外开启：" + "、".join(sorted(self.on)))
        bits.append("全部开启" if not self.off else "关闭：" + "、".join(sorted(self.off)))
        return "；".join(bits)

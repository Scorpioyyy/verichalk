"""感知（M4）的数据模型：视觉模型的原始输出、逐页识别结果、学生上下文与参考题集。

分两层：
- `RawPage` / `RawQuestion`：视觉模型按提示词输出的 JSON（分组结构：一道大题 = 题号 + 共同题干 + 若干小题文字，省 token）；
- `PageRead` / `PerceivedItem` / `ReferenceSet`：后处理后的结果（扁平题目、知识点映射、置信度、学生上下文），进入事件与 Brief。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .brief import ReferenceItem
from .paper import ItemKind


class PageVerdict(StrEnum):
    worksheet = "worksheet"  # 可用的小学数学练习页
    not_math = "not_math"  # 不是数学内容（英文页、风景、通知……）
    beyond_primary = "beyond_primary"  # 数学，但明显超出小学范围
    no_exercises = "no_exercises"  # 数学页，但没有需要作答的题（纯讲解 / 空白）
    unreadable = "unreadable"  # 模糊、太暗、遮挡，读不出题

    @property
    def usable(self) -> bool:
        return self is PageVerdict.worksheet


_KIND_ALIASES = {
    "calc": ItemKind.calc,
    "计算": ItemKind.calc,
    "计算题": ItemKind.calc,
    "口算": ItemKind.calc,
    "fill": ItemKind.fill,
    "填空": ItemKind.fill,
    "填空题": ItemKind.fill,
    "choice": ItemKind.choice,
    "选择": ItemKind.choice,
    "选择题": ItemKind.choice,
    "judge": ItemKind.judge,
    "判断": ItemKind.judge,
    "判断题": ItemKind.judge,
    "application": ItemKind.application,
    "应用题": ItemKind.application,
    "解决问题": ItemKind.application,
    "open": ItemKind.open,
    "开放题": ItemKind.open,
}


class Doubt(BaseModel):
    """一处看不清 / 不确定的转写。"""

    item: int = 0  # 该大题里第几个小题（从 1 起；0 = 整道大题）
    note: str = ""


class RawQuestion(BaseModel):
    """视觉模型读出的一道大题（同一题干下可有多个小题，如一排口算）。"""

    no: str = ""
    instruction: str = ""
    items: list[str] = Field(default_factory=list)
    kind: ItemKind | None = None
    topic: str = ""
    difficulty: int | None = None
    confidence: float = 0.9
    has_figure: bool = False
    figure: str = ""
    doubts: list[Doubt] = Field(default_factory=list)

    @field_validator("no", "instruction", "topic", "figure", mode="before")
    @classmethod
    def _text(cls, v: Any) -> str:
        return "" if v is None else str(v).strip()

    @field_validator("items", mode="before")
    @classmethod
    def _items(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            v = [v]
        return [str(x).strip() for x in v if str(x).strip()]

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, v: Any) -> ItemKind | None:
        if v in (None, ""):
            return None
        return _KIND_ALIASES.get(str(v).strip().lower()) or _KIND_ALIASES.get(str(v).strip())

    @field_validator("difficulty", mode="before")
    @classmethod
    def _difficulty(cls, v: Any) -> int | None:
        try:
            return min(5, max(1, int(v))) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, v: Any) -> float:
        try:
            return min(1.0, max(0.0, float(v)))
        except (TypeError, ValueError):
            return 0.9

    @field_validator("has_figure", mode="before")
    @classmethod
    def _bool(cls, v: Any) -> bool:
        return v in (True, "true", "True", "是", 1)

    @field_validator("doubts", mode="before")
    @classmethod
    def _doubts(cls, v: Any) -> list[Any]:
        if v is None:
            return []
        out: list[Any] = []
        for d in v if isinstance(v, list) else [v]:
            out.append({"item": 0, "note": d} if isinstance(d, str) else d)
        return out


class RawPage(BaseModel):
    """视觉模型对一张图片的输出。"""

    verdict: PageVerdict = PageVerdict.worksheet
    reason: str = ""
    title: str = ""
    questions: list[RawQuestion] = Field(default_factory=list)

    @field_validator("verdict", mode="before")
    @classmethod
    def _verdict(cls, v: Any) -> Any:
        return PageVerdict.worksheet if v in (None, "") else v


class ImageQuality(BaseModel):
    """预处理阶段的确定性质量指标。"""

    width: int
    height: int
    orig_width: int
    orig_height: int
    sharpness: float  # 边缘响应的方差：越大越清晰
    brightness: float  # 0～255 的平均亮度
    contrast: float  # 亮度标准差
    hints: list[str] = Field(default_factory=list)  # 教师语言的拍摄建议（"光线偏暗……"）
    poor: bool = False  # 画质差到数字可能读错：识别结果要请教师核对


class PerceivedItem(BaseModel):
    """一道识别出来的题（小题级，与 `ReferenceItem` 一一对应）。"""

    id: str
    attachment_id: str = ""
    no: str = ""
    instruction: str = ""
    text: str
    kind: ItemKind | None = None
    has_figure: bool = False
    figure_desc: str = ""
    topic: str = ""
    difficulty: int | None = None
    confidence: float = 0.9
    uncertain: str = ""  # 看不清的说明；非空即需要教师确认
    kp_ids: list[str] = Field(default_factory=list)
    kp_names: list[str] = Field(default_factory=list)

    @property
    def low_confidence(self) -> bool:
        return bool(self.uncertain)

    def reference(self) -> ReferenceItem:
        src = f"photo:{self.attachment_id}#{self.no}" if self.attachment_id else "user"
        return ReferenceItem(
            id=self.id,
            text=self.text,
            instruction=self.instruction,
            no=self.no,
            kp_ids=self.kp_ids,
            difficulty=self.difficulty,
            kind=self.kind,
            has_figure=self.has_figure,
            figure_desc=self.figure_desc,
            source=src,
            confidence=self.confidence,
            uncertain=self.uncertain,
        )


class PageRead(BaseModel):
    """一张图片的识别结果。"""

    attachment_id: str
    filename: str = ""
    verdict: PageVerdict
    reason: str = ""
    title: str = ""
    items: list[PerceivedItem] = Field(default_factory=list)
    quality: ImageQuality | None = None
    kp_ids: list[str] = Field(default_factory=list)  # 页面级知识点（标题与题目知识点合并）


class StudentContext(BaseModel):
    """照片告诉我们的"学生学到哪、做过什么"：约束后续生成的范围与难度。"""

    grade: int | None = None
    semester: Literal["a", "b"] | None = None
    lesson_id: str | None = None  # 目标课时：能力边界的参照
    kp_ids: list[str] = Field(default_factory=list)  # 按出现的题数从多到少
    kp_names: list[str] = Field(default_factory=list)
    difficulty: list[int] | None = None  # [lo, hi]
    kinds: list[ItemKind] = Field(default_factory=list)
    summary: str = ""  # 教师语言："四年级下册 · 小数的性质、整数运算律在小数运算中的推广"


class ReferenceSet(BaseModel):
    """`perceive` 阶段的输出。"""

    pages: list[PageRead] = Field(default_factory=list)
    context: StudentContext = Field(default_factory=StudentContext)
    usable: bool = False  # 至少有一张可用的练习页且读出了题
    message: str = ""  # 不可用时给教师的话（原因 + 下一步建议）
    needs_confirm: bool = False  # 有低置信的题，需要教师确认

    @property
    def items(self) -> list[PerceivedItem]:
        return [it for p in self.pages if p.verdict.usable for it in p.items]

    def references(self) -> list[ReferenceItem]:
        return [it.reference() for it in self.items]

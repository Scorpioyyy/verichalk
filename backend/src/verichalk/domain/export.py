"""导出选项（前后端契约）。

教师版 / 学生版、是否含解析、答案放在题后还是附页、页眉信息，对应 PRD FR-8。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class ExportFormat(StrEnum):
    pdf = "pdf"
    docx = "docx"
    md = "md"
    tex = "tex"


class ExportVersion(StrEnum):
    teacher = "teacher"  # 含答案（与解析，见 include_solution）
    student = "student"  # 空白卷：不含任何答案与解析，主观题留作答空间


class AnswerPlacement(StrEnum):
    inline = "inline"  # 每道题后面
    appendix = "appendix"  # 全卷之后另起一页："参考答案与解析"


class ExportHeader(BaseModel):
    """页眉信息；留空则取试卷 `meta` 里的同名键，再没有就留空（学生版自动给姓名 / 班级 / 得分的填写线）。"""

    school: str = ""
    class_name: str = ""
    date: str = ""
    duration_minutes: int | None = None
    name_line: bool = True  # 学生版显示"姓名：____"


class ExportOptions(BaseModel):
    format: ExportFormat = ExportFormat.pdf
    version: ExportVersion = ExportVersion.student
    include_solution: bool = True  # 仅教师版有效
    answers: AnswerPlacement = AnswerPlacement.appendix  # 仅教师版有效
    mark_review: bool = True  # 教师版：对"需复核"的题标注
    header: ExportHeader = Field(default_factory=ExportHeader)

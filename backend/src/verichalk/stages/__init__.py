"""阶段（L5）：每个阶段是带类型输入输出的异步函数。"""

from .answer import AnswerIn, AnswerStage
from .base import RunContext, Stage, run_stage
from .diagnostic import DiagnosticIn, DiagnosticOut, DiagnosticStage
from .edit import EditIn, EditStage
from .perceive import PerceiveIn, PerceiveStage
from .plan import PlanIn, PlanStage
from .produce import ProduceIn, ProduceOut, ProduceStage
from .reply import compose_reply
from .review import ReviewIn, ReviewOut, ReviewStage, ReviewTarget
from .understand import UnderstandIn, UnderstandStage

__all__ = [
    "AnswerIn",
    "AnswerStage",
    "DiagnosticIn",
    "DiagnosticOut",
    "DiagnosticStage",
    "EditIn",
    "EditStage",
    "PerceiveIn",
    "PerceiveStage",
    "PlanIn",
    "PlanStage",
    "ProduceIn",
    "ProduceOut",
    "ProduceStage",
    "ReviewIn",
    "ReviewOut",
    "ReviewStage",
    "ReviewTarget",
    "RunContext",
    "Stage",
    "UnderstandIn",
    "UnderstandStage",
    "compose_reply",
    "run_stage",
]

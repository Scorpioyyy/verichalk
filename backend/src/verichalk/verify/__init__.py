"""核验层（L3）：答案的精确比较、内容规范化、各项检查与状态汇总。"""

from .answers import answers_equal, format_answer, parse_value
from .checks import VerifyEnv, VerifyInput
from .content import normalize_text, structure_issues
from .runner import verify_item
from .summary import failing, summarize

__all__ = [
    "VerifyEnv",
    "VerifyInput",
    "answers_equal",
    "failing",
    "format_answer",
    "normalize_text",
    "parse_value",
    "structure_issues",
    "summarize",
    "verify_item",
]

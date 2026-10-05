"""评测框架（L8）：用例、运行器、检查、报告。"""

from .cases import Case, TurnSpec, load_cases
from .checks import CaseResult, CheckOutcome, RunResult, evaluate
from .report import render_markdown, write_report
from .runner import SuiteResult, run_case, run_suite

__all__ = [
    "Case",
    "CaseResult",
    "CheckOutcome",
    "RunResult",
    "SuiteResult",
    "TurnSpec",
    "evaluate",
    "load_cases",
    "render_markdown",
    "run_case",
    "run_suite",
    "write_report",
]

"""核验状态汇总（D9，architecture §4.4）：一组 `CheckResult` → `VerifyStatus`。纯函数，进 L1 不变量测试。

规则：
- 任一检查 `fail` → `rejected`（进入修复；修复上限后废弃重出，不展示）；
- 求解程序与盲解**两路**都 `pass`，且没有任何 `warn` → `verified`（已核验）；
- 至少一路 `pass`、没有 `warn` → `checked`（已校对：单路通过，另一路被关闭 / 降级）；
- 其余（有 `warn`，或没有任何一路求解通过）→ `needs_review`（需复核，界面标注原因）。
`borderline` 的边界结果是 `pass`（证据里带标记），不降级，但界面要给出提示（A3）。
"""

from __future__ import annotations

from ..domain.paper import CheckResult, CheckStatus, Verification, VerifyStatus

ROUTES = ("program", "blind")


def summarize(checks: list[CheckResult]) -> Verification:
    by = {c.name: c for c in checks}
    if any(c.status == CheckStatus.fail for c in checks):
        return Verification(status=VerifyStatus.rejected, checks=checks)
    passed = sum(1 for r in ROUTES if r in by and by[r].status == CheckStatus.passed)
    warns = any(c.status == CheckStatus.warn for c in checks)
    if warns or passed == 0:
        status = VerifyStatus.needs_review
    elif passed >= 2:
        status = VerifyStatus.verified
    else:
        status = VerifyStatus.checked
    return Verification(status=status, checks=checks)


def failing(checks: list[CheckResult]) -> list[CheckResult]:
    return [c for c in checks if c.status == CheckStatus.fail]

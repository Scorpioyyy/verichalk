"""一道题的核验编排：先跑便宜的确定性检查，通过后再并行跑需要模型的检查（节省成本与延迟）。

produce 阶段与核验评测集都调用 `verify_item`，保证评测测到的就是线上跑的。
"""

from __future__ import annotations

import asyncio

from ..core.features import FeatureFlags
from ..domain.paper import CheckResult, CheckStatus, Verification
from .checks import (
    VerifyEnv,
    VerifyInput,
    check_blind,
    check_boundary,
    check_program,
    check_quality,
    check_structure,
)
from .summary import summarize

SKIP_EARLY = "前置检查未通过，未执行"


def _skip(name: str, why: str) -> CheckResult:
    return CheckResult(name=name, status=CheckStatus.skip, detail=why)


async def verify_item(
    env: VerifyEnv,
    inp: VerifyInput,
    flags: FeatureFlags,
    *,
    preset: dict[str, CheckResult] | None = None,
) -> Verification:
    """返回 `Verification`（状态 + 各项检查的证据）。关闭的检查记为 `skip`。

    `preset`：已经由别处得出结论的检查（如 `template` 来源的求解与边界由知识库给出），直接采用，不再执行。"""
    preset = preset or {}
    results: list[CheckResult] = [await check_structure(inp)]
    if "program" in preset:
        results.append(preset["program"])
    elif flags.enabled("produce.program_check"):
        results.append(await check_program(inp))
    else:
        results.append(_skip("program", "已关闭"))
    if any(r.status == CheckStatus.fail for r in results):  # 便宜的检查已经失败：不花模型调用，直接去修复
        results += [_skip(n, SKIP_EARLY) for n in ("blind", "boundary", "quality", "integration")]
        return summarize(results)

    blind_t = asyncio.ensure_future(check_blind(env, inp)) if flags.enabled("produce.blind_solve") else None
    boundary_t = (
        asyncio.ensure_future(check_boundary(env, inp))
        if flags.enabled("produce.boundary_check") and "boundary" not in preset
        else None
    )
    quality_t = (
        asyncio.ensure_future(check_quality(env, inp)) if flags.enabled("produce.quality_check") else None
    )
    pending = [t for t in (blind_t, boundary_t, quality_t) if t is not None]
    try:
        blind = await blind_t if blind_t else _skip("blind", "已关闭")
        boundary = (
            preset["boundary"]
            if "boundary" in preset
            else (await boundary_t if boundary_t else _skip("boundary", "已关闭"))
        )
        quality, integration = (
            await quality_t if quality_t else (_skip("quality", "已关闭"), _skip("integration", "已关闭"))
        )
    finally:
        for t in pending:
            if not t.done():
                t.cancel()
    results += [blind, boundary, quality, integration]
    return summarize(results)

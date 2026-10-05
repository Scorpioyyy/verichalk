"""评测运行器：把用例在进程内跑过完整管线（不经 HTTP），收集事件与指标。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ..core.config import Settings
from ..core.errors import VerichalkError
from ..metrics import AggregateMetrics, aggregate, compute_run_metrics
from ..orchestrator import Container, build_container
from ..store import Store
from .cases import Case
from .checks import CaseResult, RunResult, evaluate, reply_of

ContainerFactory = Callable[[Settings], Awaitable[Container]]


@dataclass
class SuiteResult:
    suite: str
    split: str
    settings: Settings
    cases: list[CaseResult] = field(default_factory=list)
    started_at: float = 0.0
    duration_s: float = 0.0

    @property
    def aggregate(self) -> AggregateMetrics:
        return aggregate([r.metrics for c in self.cases for r in c.results])


async def default_factory(settings: Settings) -> Container:
    return await build_container(settings, store=await Store.open(":memory:"))


async def run_case(c: Container, case: Case, run_timeout_s: float = 300.0) -> CaseResult:
    res = CaseResult(case_id=case.id, tags=case.tags)
    try:
        session = await c.manager.create_session(case.id)
        for turn in case.turns:
            run = await c.manager.start_turn(
                session.id, turn.user, [], pipeline=turn.pipeline, tags=[f"eval:{case.id}", *case.tags]
            )
            done = await c.manager.wait(run.id, run_timeout_s)
            events = await c.store.events.list(run.id)
            res.results.append(
                RunResult(
                    run=done, events=events, metrics=compute_run_metrics(events), reply=reply_of(events)
                )
            )
        res.outcomes = evaluate(case.expect, res.results)
    except VerichalkError as e:
        res.error = f"{e.code}: {e}"
    except TimeoutError:
        res.error = "用例超时"
    return res


async def run_suite(
    settings: Settings,
    cases: list[Case],
    *,
    suite: str,
    split: str,
    concurrency: int = 4,
    factory: ContainerFactory = default_factory,
) -> SuiteResult:
    c = await factory(settings)
    out = SuiteResult(suite=suite, split=split, settings=settings, started_at=time.time())
    sem = asyncio.Semaphore(concurrency)

    async def one(case: Case) -> CaseResult:
        async with sem:
            return await run_case(c, case)

    try:
        out.cases = list(await asyncio.gather(*(one(x) for x in cases)))
    finally:
        out.duration_s = time.time() - out.started_at
        await c.close()
    return out

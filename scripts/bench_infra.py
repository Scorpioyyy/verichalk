"""M1 基线：框架自身的开销（不含网络与模型）。结果写入 eval/reports/m1_baseline.md。

测量：
1. 事件写入：10k 个事件经 `StoreSink`（脱敏 + SQLite 逐条提交）的总耗时与每事件耗时；
2. 空管线开销：管线什么都不做时，一次运行的端到端耗时 p50 / p95（含创建会话、写消息、事件、落盘）；
3. SSE 读取：10k 事件按 seq 读取并解析的耗时。
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend" / "src"))

from verichalk import trace  # noqa: E402
from verichalk.core.config import Settings  # noqa: E402
from verichalk.domain.events import SpanKind  # noqa: E402
from verichalk.orchestrator import PipelineResult, RunManager  # noqa: E402
from verichalk.store import Store  # noqa: E402
from verichalk.trace import EventBus, StoreSink, Tracer, use_tracer  # noqa: E402


async def bench_events(path: Path, n: int = 10_000) -> tuple[float, float]:
    store = await Store.open(path)
    tracer = Tracer("run_bench", StoreSink(store, EventBus()))
    t0 = time.perf_counter()
    async with use_tracer(tracer):
        for i in range(n // 2):
            async with trace.span(SpanKind.tool, "x", i=i):
                pass  # 每个 span 两个事件
    write_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    evs = await store.events.list("run_bench")
    read_s = time.perf_counter() - t0
    assert len(evs) == n
    await store.close()
    return write_s, read_s


async def bench_empty_pipeline(path: Path, n: int = 200) -> list[float]:
    async def noop(ctx, turn):
        return PipelineResult()

    store = await Store.open(path)
    mgr = RunManager(Settings(), store, EventBus(), None, None, {"noop": noop})  # type: ignore[arg-type]
    ses = await mgr.create_session()
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        run = await mgr.start_turn(ses.id, "x", pipeline="noop")
        await mgr.wait(run.id, 10)
        times.append((time.perf_counter() - t0) * 1000)
    await store.close()
    return times


async def main() -> None:
    with tempfile.TemporaryDirectory() as d:
        w, r = await bench_events(Path(d) / "a.db")
        times = await bench_empty_pipeline(Path(d) / "b.db")
    times.sort()
    p50, p95 = statistics.median(times), times[int(len(times) * 0.95) - 1]
    md = f"""# M1 基线：框架开销

测量日期 {time.strftime("%Y-%m-%d")}；本机（Windows，SQLite WAL，逐事件提交）。不含网络与模型。

| 项 | 结果 | 门槛（m1_infra.md §3） |
|---|---|---|
| 10k 事件写入（脱敏 + 提交） | {w:.2f}s（{w / 10_000 * 1000:.3f} ms/事件） | ≤ 3s |
| 10k 事件读取并解析 | {r:.2f}s | — |
| 空管线一次运行（含会话消息、事件、落盘）p50 / p95 | {p50:.1f} ms / {p95:.1f} ms | p95 ≤ 50ms |
"""
    out = ROOT / "eval" / "reports" / "m1_baseline.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    asyncio.run(main())

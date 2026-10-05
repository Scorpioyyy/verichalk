"""路由：健康检查、会话、轮次、运行（SSE / 取消 / 检查点）、调试接口。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, Header, Query, Request, UploadFile
from fastapi.responses import StreamingResponse

from .. import __version__
from ..core.errors import Conflict
from ..domain.events import Event
from ..domain.run import Run
from ..metrics import AggregateMetrics, aggregate, compute_run_metrics
from .deps import ContainerDep, DebugDep
from .schemas import (
    CheckpointAnswer,
    DebugRunDetail,
    DebugRunItem,
    HealthOut,
    RunView,
    SessionState,
    TurnAccepted,
    WarmupOut,
)
from .sse import event_stream
from .uploads import save_uploads

router = APIRouter(prefix="/api")
debug = APIRouter(prefix="/api/debug", dependencies=[DebugDep])

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}


# ---- 健康 ----
@router.get("/health", response_model=HealthOut)
async def health(c: ContainerDep) -> HealthOut:
    return HealthOut(
        version=__version__,
        chalkbase_version=c.kb.chalkbase_version,
        data_version=c.kb.data_version,
        profile=c.settings.profile.value,
        llm_mode=c.settings.llm_mode.value,
        models=c.llm.registry.models(),
    )


@router.post("/warmup", response_model=WarmupOut)
async def warmup(c: ContainerDep) -> WarmupOut:
    """页面打开时由前端调用：后台预热模型连接与前缀缓存，立即返回；有效期内重复调用不会再发请求。"""
    st = c.warmer.trigger()
    return WarmupOut(state=st.state, last_age_s=st.last_age_s)


# ---- 会话 ----
@router.post("/sessions", response_model=SessionState)
async def create_session(c: ContainerDep) -> SessionState:
    s = await c.manager.create_session()
    return SessionState(session=s, messages=[], paper=None, active_run_id=None)


@router.get("/sessions/{session_id}", response_model=SessionState)
async def get_session(session_id: str, c: ContainerDep) -> SessionState:
    s = await c.store.sessions.get(session_id)
    return SessionState(
        session=s,
        messages=await c.store.messages.list(session_id),
        paper=await c.store.papers.get_current(session_id),
        active_run_id=c.manager.active_run(session_id),
    )


@router.post("/sessions/{session_id}/turns", response_model=TurnAccepted, status_code=202)
async def post_turn(
    session_id: str,
    c: ContainerDep,
    text: Annotated[str, Form()] = "",
    images: Annotated[list[UploadFile] | None, File()] = None,
) -> TurnAccepted:
    files = [f for f in (images or []) if f.filename]
    if not text.strip() and not files:
        from ..core.errors import InvalidRequest

        raise InvalidRequest("内容为空", user_message="请输入需求，或上传一张图片。")
    await c.store.sessions.get(session_id)  # 先确认会话存在，再处理上传
    if c.manager.active_run(session_id):
        raise Conflict("会话有进行中的运行", user_message="上一个任务还在进行，请等待完成或先停止。")
    atts = await save_uploads(c.settings, session_id, files)
    for a in atts:
        await c.store.attachments.add(a)
    run = await c.manager.start_turn(session_id, text.strip(), atts)
    return TurnAccepted(session_id=session_id, run_id=run.id, message_id=run.message_id or "")


# ---- 运行 ----
@router.get("/runs/{run_id}", response_model=RunView)
async def get_run(run_id: str, c: ContainerDep) -> RunView:
    return RunView.of(await c.store.runs.get(run_id))


@router.get("/runs/{run_id}/events")
async def run_events(
    run_id: str,
    request: Request,
    c: ContainerDep,
    after: int = 0,
    last_event_id: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    await c.store.runs.get(run_id)  # 不存在则 404
    start = int(last_event_id) if last_event_id and last_event_id.isdigit() else after
    return StreamingResponse(
        event_stream(c, run_id, after=start, visibility="user", request=request),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, c: ContainerDep) -> dict[str, bool]:
    await c.store.runs.get(run_id)
    return {"cancelled": await c.manager.cancel(run_id)}


@router.post("/runs/{run_id}/checkpoint", status_code=202)
async def answer_checkpoint(run_id: str, body: CheckpointAnswer, c: ContainerDep) -> dict[str, bool]:
    await c.manager.resume(run_id, body.answer)
    return {"accepted": True}


# ---- 调试 ----
@debug.get("/runs", response_model=list[DebugRunItem])
async def debug_runs(
    c: ContainerDep,
    status: str | None = None,
    session_id: str | None = None,
    limit: Annotated[int, Query(le=100)] = 30,
    offset: int = 0,
) -> list[DebugRunItem]:
    runs = await c.store.runs.list(session_id=session_id, status=status, limit=limit, offset=offset)
    out = []
    for r in runs:
        out.append(
            DebugRunItem(run=_strip_state(r), metrics=compute_run_metrics(await c.store.events.list(r.id)))
        )
    return out


@debug.get("/runs/{run_id}", response_model=DebugRunDetail)
async def debug_run(run_id: str, c: ContainerDep) -> DebugRunDetail:
    run = await c.store.runs.get(run_id)
    events = await c.store.events.list(run_id)
    return DebugRunDetail(run=run, metrics=compute_run_metrics(events), n_events=len(events))


@debug.get("/runs/{run_id}/events", response_model=list[Event])
async def debug_events(run_id: str, c: ContainerDep, after: int = 0) -> list[Event]:
    await c.store.runs.get(run_id)
    return await c.store.events.list(run_id, after_seq=after)


@debug.get("/metrics", response_model=AggregateMetrics)
async def debug_metrics(c: ContainerDep, limit: Annotated[int, Query(le=200)] = 50) -> AggregateMetrics:
    runs = await c.store.runs.list(limit=limit)
    return aggregate(
        [compute_run_metrics(await c.store.events.list(r.id)) for r in runs if r.status.terminal]
    )


def _strip_state(run: Run) -> Run:
    return run.model_copy(update={"state": {}})

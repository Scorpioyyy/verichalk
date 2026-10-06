"""路由：健康检查、会话、轮次、运行（SSE / 取消 / 检查点）、调试接口。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Header, Query, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from .. import __version__
from ..core.errors import Conflict, InvalidRequest, NotFound
from ..domain.badcase import Badcase, BadcaseIn
from ..domain.events import Event
from ..domain.export import ExportOptions
from ..domain.knowledge import KPDetail, KPRef
from ..domain.paper import Paper
from ..domain.paper_ops import PaperDiff
from ..domain.run import Run, Session
from ..metrics import AggregateMetrics, aggregate, compute_run_metrics
from ..orchestrator.debug_chat import ChatTurn, stream_analysis
from ..perception import thumbnail_jpeg
from .deps import ContainerDep, DebugDep
from .schemas import (
    CheckpointAnswer,
    DebugRunDetail,
    DebugRunItem,
    HealthOut,
    PaperHistory,
    PaperPatchBody,
    PaperUpdate,
    RestoreBody,
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


class SessionRename(BaseModel):
    title: str = Field(min_length=1, max_length=60)


@router.patch("/sessions/{session_id}", response_model=Session)
async def rename_session(session_id: str, body: SessionRename, c: ContainerDep) -> Session:
    """给对话改名（历史列表里显示的标题）。"""
    return await c.manager.rename_session(session_id, body.title)


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str, c: ContainerDep) -> Response:
    """删除一个对话：消息、试卷与修订、事件、上传的图片一并删除，不可恢复。进行中的对话先停止再删（409）。"""
    await c.manager.delete_session(session_id)
    return Response(status_code=204)


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


# ---- 试卷：手动编辑、撤销 / 重做、历史 ----
async def _update(c: ContainerDep, session_id: str, out) -> PaperUpdate:  # type: ignore[no-untyped-def]
    h = await c.papers.history(session_id)
    return PaperUpdate(
        paper=out.paper,
        revision=out.revision,
        warnings=out.warnings,
        review_run_id=out.review_run_id,
        can_undo=h.can_undo,
        can_redo=h.can_redo,
    )


@router.patch("/sessions/{session_id}/paper", response_model=PaperUpdate)
async def patch_paper(session_id: str, body: PaperPatchBody, c: ContainerDep) -> PaperUpdate:
    """手动编辑：应用补丁并立即返回新修订；内容被改的题状态回到"待核验"，同时发起一个复核运行（`review_run_id`）。"""
    out = await c.papers.edit(
        session_id, [op.model_dump(mode="json") for op in body.ops], base_rev=body.base_rev
    )
    return await _update(c, session_id, out)


@router.post("/sessions/{session_id}/paper/undo", response_model=PaperUpdate)
async def undo_paper(session_id: str, c: ContainerDep) -> PaperUpdate:
    return await _update(c, session_id, await c.papers.undo(session_id))


@router.post("/sessions/{session_id}/paper/redo", response_model=PaperUpdate)
async def redo_paper(session_id: str, c: ContainerDep) -> PaperUpdate:
    return await _update(c, session_id, await c.papers.redo(session_id))


@router.post("/sessions/{session_id}/paper/restore", response_model=PaperUpdate)
async def restore_paper(session_id: str, body: RestoreBody, c: ContainerDep) -> PaperUpdate:
    return await _update(c, session_id, await c.papers.restore(session_id, body.rev))


@router.get("/sessions/{session_id}/paper/history", response_model=PaperHistory)
async def paper_history(session_id: str, c: ContainerDep) -> PaperHistory:
    h = await c.papers.history(session_id)
    return PaperHistory(revisions=h.revisions, head_rev=h.head_rev, can_undo=h.can_undo, can_redo=h.can_redo)


@router.get("/sessions/{session_id}/paper/revisions/{rev}", response_model=Paper)
async def paper_at(session_id: str, rev: int, c: ContainerDep) -> Paper:
    return await c.papers.at(session_id, rev)


@router.get("/sessions/{session_id}/paper/diff", response_model=PaperDiff)
async def paper_diff(
    session_id: str,
    c: ContainerDep,
    rev_from: Annotated[int, Query(alias="from")],
    rev_to: Annotated[int, Query(alias="to")],
) -> PaperDiff:
    return await c.papers.diff(session_id, rev_from, rev_to)


@router.get(
    "/sessions/{session_id}/figures/{figure_id}",
    response_class=Response,
    responses={200: {"content": {"image/svg+xml": {}}, "description": "图形的 SVG"}},
)
async def paper_figure(session_id: str, figure_id: str, c: ContainerDep) -> Response:
    """试卷里某个图形的 SVG（预览与导出共用同一份渲染）。"""
    return Response(await c.papers.figure_svg(session_id, figure_id), media_type="image/svg+xml")


@router.get(
    "/sessions/{session_id}/attachments/{attachment_id}",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}}, "description": "上传的图片（默认缩略图）"}},
)
async def attachment_image(
    session_id: str, attachment_id: str, c: ContainerDep, full: bool = False
) -> Response:
    """会话里上传的图片：聊天里显示缩略图，`full=true` 取原图。只能取本会话的附件。"""
    att = await c.store.attachments.get(attachment_id)
    if att.session_id != session_id:
        raise NotFound("附件不属于这个会话")
    path = (c.settings.data_path / att.path).resolve()
    if not path.is_relative_to(c.settings.data_path.resolve()) or not path.exists():
        raise NotFound("附件文件不存在")
    data = await asyncio.to_thread(path.read_bytes)
    if full:
        return Response(data, media_type=att.mime, headers={"Cache-Control": "private, max-age=3600"})
    thumb = await asyncio.to_thread(thumbnail_jpeg, data)
    return Response(thumb, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/knowledge/refs", response_model=list[KPRef])
async def knowledge_refs(c: ContainerDep, ids: Annotated[list[str], Query()]) -> list[KPRef]:
    """知识点的教师可读名称与位置，用于在题目旁显示"涉及：小数加减法（四下·第一单元）"。"""
    return await c.kb.refs(ids[:50])


# ---- 导出 ----
@router.post(
    "/sessions/{session_id}/export",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}, "description": "导出的文件"}},
)
async def export_paper_file(
    session_id: str, body: ExportOptions, c: ContainerDep, inline: bool = False
) -> Response:
    """导出当前试卷。`inline=true` 用于页面内预览（PDF 直接嵌入）；警告（缺字、图无法绘制等）放在 `X-Export-Warnings`。"""
    res = await c.papers.export(session_id, body)
    disp = "inline" if inline else "attachment"
    headers = {
        "Content-Disposition": f"{disp}; filename*=UTF-8''{quote(res.filename)}",
        "X-Export-Warnings": quote(json.dumps(res.warnings, ensure_ascii=False)),
        "Access-Control-Expose-Headers": "Content-Disposition, X-Export-Warnings",
    }
    return Response(res.data, media_type=res.media_type, headers=headers)


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
async def _input_text(c: ContainerDep, run: Run) -> str:
    msg = await c.store.messages.get(run.message_id) if run.message_id else None
    return msg.content if msg else ""


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
            DebugRunItem(
                run=_strip_state(r),
                metrics=compute_run_metrics(await c.store.events.list(r.id)),
                input_text=await _input_text(c, r),
            )
        )
    return out


@debug.get("/runs/{run_id}", response_model=DebugRunDetail)
async def debug_run(run_id: str, c: ContainerDep) -> DebugRunDetail:
    run = await c.store.runs.get(run_id)
    events = await c.store.events.list(run_id)
    return DebugRunDetail(
        run=run,
        metrics=compute_run_metrics(events),
        n_events=len(events),
        input_text=await _input_text(c, run),
    )


class DebugChatBody(BaseModel):
    messages: list[ChatTurn] = Field(min_length=1, max_length=40)


@debug.post("/runs/{run_id}/chat", response_class=StreamingResponse)
async def debug_chat(run_id: str, body: DebugChatBody, c: ContainerDep) -> StreamingResponse:
    """运行分析助手：围绕这一次运行的多轮对话（SSE：delta / tool / done / error）。上下文由运行摘要组织，细节靠工具按需查询。"""
    last = body.messages[-1]
    if last.role != "user" or not last.content.strip():
        raise InvalidRequest("最后一条必须是非空的用户消息", user_message="请先输入问题。")
    run = await c.store.runs.get(run_id)
    input_text = await _input_text(c, run)

    async def gen() -> AsyncIterator[str]:
        async for ev in stream_analysis(c, run_id, input_text, body.messages):
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@debug.get("/runs/{run_id}/events", response_model=list[Event])
async def debug_events(run_id: str, c: ContainerDep, after: int = 0) -> list[Event]:
    await c.store.runs.get(run_id)
    return await c.store.events.list(run_id, after_seq=after)


@debug.get("/runs/{run_id}/stream")
async def debug_stream(
    run_id: str,
    request: Request,
    c: ContainerDep,
    after: int = 0,
    last_event_id: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    """调试台的实时事件流：与用户端同一协议，但包含 debug 可见性的事件（span、模型调用、检索）。"""
    await c.store.runs.get(run_id)
    start = int(last_event_id) if last_event_id and last_event_id.isdigit() else after
    return StreamingResponse(
        event_stream(c, run_id, after=start, visibility=None, request=request),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@debug.get("/kp/{kp_id}", response_model=KPDetail)
async def debug_kp(kp_id: str, c: ContainerDep) -> KPDetail:
    """图检索视图里点击节点：知识点说明、掌握要求、典型错误。"""
    return await c.kb.kp(kp_id)


@debug.get("/badcases", response_model=list[Badcase])
async def debug_badcases(c: ContainerDep) -> list[Badcase]:
    return await c.badcases.list()


@debug.post("/badcases", response_model=Badcase, status_code=201)
async def debug_add_badcase(body: BadcaseIn, c: ContainerDep) -> Badcase:
    """把一次运行里发现的坏例子写入 Badcase 簿（YAML 文件，之后按根因类别修复并转成回归用例）。"""
    await c.store.runs.get(body.run_id)
    return await c.badcases.add(body)


@debug.get("/metrics", response_model=AggregateMetrics)
async def debug_metrics(c: ContainerDep, limit: Annotated[int, Query(le=200)] = 50) -> AggregateMetrics:
    runs = await c.store.runs.list(limit=limit)
    return aggregate(
        [compute_run_metrics(await c.store.events.list(r.id)) for r in runs if r.status.terminal]
    )


def _strip_state(run: Run) -> Run:
    return run.model_copy(update={"state": {}})

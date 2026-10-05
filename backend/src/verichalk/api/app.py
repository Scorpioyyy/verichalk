"""FastAPI 应用工厂。"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..core.config import Settings
from ..core.errors import ConfigError, Conflict, InvalidRequest, NotFound, VerichalkError
from ..core.logging import setup_logging
from ..domain.common import ErrorInfo
from ..orchestrator import Container, build_container
from ..trace import error_info
from .routes import debug, router
from .schemas import ErrorBody

log = logging.getLogger("verichalk.api")

_STATUS = {NotFound: 404, Conflict: 409, InvalidRequest: 422, ConfigError: 500}


def create_app(settings: Settings | None = None, *, container: Container | None = None) -> FastAPI:
    settings = settings or Settings()
    setup_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = container is None
        app.state.container = container or await build_container(settings)
        try:
            yield
        finally:
            if owned:
                await app.state.container.close()

    app = FastAPI(title="VeriChalk", version=__version__, lifespan=lifespan)
    if container is not None:  # 测试 / 评测：同步注入，免去 lifespan
        app.state.container = container
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Last-Event-ID"],
    )

    @app.exception_handler(VerichalkError)
    async def _typed(_: Request, exc: VerichalkError) -> JSONResponse:
        status = next((s for t, s in _STATUS.items() if isinstance(exc, t)), 500)
        return JSONResponse(ErrorBody(error=error_info(exc)).model_dump(mode="json"), status_code=status)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        info: ErrorInfo = error_info(exc)
        return JSONResponse(ErrorBody(error=info).model_dump(mode="json"), status_code=500)

    app.include_router(router)
    app.include_router(debug)
    _mount_frontend(app, settings)
    return app


def _mount_frontend(app: FastAPI, settings: Settings) -> None:
    """生产形态：同源托管前端构建产物（SPA 回退到 index.html）。目录不存在则跳过（纯后端开发）。"""
    dist = Path(settings.root_dir) / "frontend" / "dist"
    index = dist / "index.html"
    if not index.exists():
        return
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        target = dist / path
        return FileResponse(target if path and target.is_file() else index)

"""依赖注入：容器与调试台鉴权。"""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from ..orchestrator import Container

_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def get_container(request: Request) -> Container:
    return request.app.state.container


ContainerDep = Annotated[Container, Depends(get_container)]


async def require_debug(
    request: Request,
    c: ContainerDep,
    x_debug_token: str | None = Header(default=None),
    token: str | None = None,
) -> None:
    """调试台鉴权：配置了令牌就必须匹配；未配置时只允许本机访问（开发态）。"""
    expected = c.settings.debug_token.get_secret_value() if c.settings.debug_token else None
    given = x_debug_token or token
    if expected:
        if not given or not hmac.compare_digest(given, expected):
            raise HTTPException(status_code=403, detail="调试台需要有效令牌")
        return
    host = request.client.host if request.client else ""
    if host not in _LOCAL_HOSTS:
        raise HTTPException(status_code=403, detail="未配置 VERICHALK_DEBUG_TOKEN，调试台仅限本机访问")


DebugDep = Depends(require_debug)

"""接口层（L7）：FastAPI 应用、SSE、调试接口。"""

from .app import create_app

__all__ = ["create_app"]

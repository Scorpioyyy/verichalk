"""uvicorn 入口：`verichalk.api.factory:app`。"""

from .app import create_app

app = create_app()

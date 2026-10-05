"""命令行：`python -m verichalk serve` 启动本地服务（localhost:8000）。"""

from __future__ import annotations

import argparse


def main() -> None:
    ap = argparse.ArgumentParser(prog="verichalk")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sv = sub.add_parser("serve", help="启动 API 服务")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--reload", action="store_true")
    args = ap.parse_args()
    if args.cmd == "serve":
        import uvicorn

        uvicorn.run("verichalk.api.factory:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()

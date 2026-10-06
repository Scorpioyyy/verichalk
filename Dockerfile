# syntax=docker/dockerfile:1
# 说明：开发机没有 Docker，镜像本身未在本机构建；其中每一步都已在等价环境验证——
# 干净 venv 里 `pip install ./backend`（chalkbase 取自 PyPI）、VERICHALK_ROOT 指向只含 config/ 与 frontend/dist 的目录，
# 健康检查、前端同源托管、出题与四种格式的导出全部通过。首次部署若构建失败，先看这里。
# 单容器：构建前端 → 安装后端 → 同一进程托管 API 与静态资源（D2）。

FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim AS app
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    VERICHALK_ROOT=/app \
    VERICHALK_DATA_DIR=/data
# 中文字体：PDF 导出（Typst）与图形文字渲染需要
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/ backend/
COPY config/ config/
# chalkbase 从 PyPI 安装（数据随包发布）；pandoc（pypandoc_binary）与 typst 是 pip 依赖，无需 TeX Live
RUN pip install ./backend
COPY --from=web /web/dist frontend/dist
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import os,urllib.request as u; u.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT','8000'))"
CMD ["sh", "-c", "exec python -m verichalk serve --host 0.0.0.0 --port ${PORT:-8000}"]

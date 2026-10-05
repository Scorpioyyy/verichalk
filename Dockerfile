# 草案（M1）：本机未安装 Docker，尚未构建验证；在 M8 部署时验证并定稿。
# 单容器：构建前端 → 安装后端 → 同一进程托管 API 与静态资源（D2）。
# syntax=docker/dockerfile:1

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

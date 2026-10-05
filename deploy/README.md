# 部署

> 本期（M1～M7）只保证本地 `localhost` 跑通；云端部署（Zeabur）在 M8 完成。本目录与根目录 `Dockerfile` 已按"单容器、同源托管"的约定组织，之后可直接部署。

## 形态

- 根目录一个多阶段 `Dockerfile`：构建前端 → 安装后端与 chalkbase → 单进程托管 API 与静态资源。
- 端口取环境变量 `PORT`（默认 8000）；健康检查 `GET /api/health`。
- 数据（SQLite、上传、导出）在 `VERICHALK_DATA_DIR`（镜像内默认 `/data`），部署时挂持久卷。
- SSE 需要代理层不缓冲：应用已返回 `X-Accel-Buffering: no`；若平台前面还有一层反向代理，确认它不对 `text/event-stream` 做缓冲。

## 环境变量（部署时配置，绝不入库）

| 变量 | 说明 |
|---|---|
| `VERICHALK_PROFILE` | 部署用 `intl`（新加坡节点，香港 / 新加坡机房延迟低） |
| `DASHSCOPE_INTL_API_KEY`、`DASHSCOPE_INTL_BASE_URL` | 新加坡节点的密钥与端点 |
| `VERICHALK_DEBUG_TOKEN` | **必须设置**：调试台访问令牌（未设置时调试台仅限本机访问，公网部署等于关闭） |
| `VERICHALK_DATA_DIR` | 持久卷挂载点 |
| `PORT` | 平台注入 |

## 资源（估算，M8 实测后更新）

- 常驻内存：chalkbase 数据加载约 +62MB（实测）；加上 Python 与 FastAPI，预计 300～500MB；导出 PDF 时 Typst 峰值另计。
- Zeabur 开发者套餐：内存按量计费，CPU 不计费；先选 1GB 内存观察。

## CI

`.github/workflows/ci.yml`：每次 push 到 `main` 与每个 PR 自动运行 ruff、pyright 与全部测试（无网络、无密钥）。

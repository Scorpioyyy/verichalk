# 部署

单容器、同源托管：根目录 `Dockerfile` 先构建前端，再安装后端（chalkbase 取自 PyPI），同一个进程提供 API 与静态页面。用户端在 `/`，调试台在 `/debug`。

> 开发机没有 Docker，**镜像本身没有在本机构建过**；其中每一步都在等价环境验证过：干净 venv 里 `pip install ./backend`、`VERICHALK_ROOT` 指向只含 `config/` 与 `frontend/dist` 的目录，健康检查、前端同源托管、出题与四种格式的导出均通过。首次部署若构建失败，先看构建日志里是哪一步。

## 部署到 Zeabur（推荐步骤）

1. 把仓库推到 GitHub，在 Zeabur 新建项目 → **从 GitHub 部署** → 选本仓库。Zeabur 发现根目录的 `Dockerfile` 会按它构建，无需额外配置构建命令。
2. 在服务的 **环境变量** 里设置（见下表；密钥只放这里，绝不入库）。
3. 在服务的 **存储 / 卷** 里挂一个持久卷到 `/data`（SQLite 数据库、上传与导出都在这里；不挂卷则每次重启丢会话）。
4. 绑定域名（或用 Zeabur 提供的域名），打开首页应看到"今天想出什么题？"。
5. 验证：`GET /api/health` 返回 `status: ok`，其中 `profile` 应为 `intl`、`llm_mode` 应为 `live`；随便出一道题并导出 PDF。

## 环境变量

| 变量 | 必需 | 说明 |
|---|---|---|
| `VERICHALK_PROFILE` | 是 | 部署用 `intl`（新加坡节点） |
| `DASHSCOPE_INTL_API_KEY`、`DASHSCOPE_INTL_BASE_URL` | 是 | 新加坡节点的密钥与端点（端点主机名按敏感信息处理，只放这里） |
| `VERICHALK_DEBUG_TOKEN` | **是** | 调试台令牌。未设置时调试台只允许本机访问——在反向代理后面等于关闭；设置后访问 `/debug` 需要输入它 |
| `VERICHALK_DATA_DIR` | 否 | 镜像里默认 `/data`，与卷挂载点一致即可 |
| `PORT` | 否 | 平台注入；容器按它监听（默认 8000） |
| `VERICHALK_LLM_MODE` | 否 | 必须是 `live`（默认）。`replay` / `record` 只用于评测与测试 |

## 上线前必读

- **单实例**：会话与进行中的运行保存在 SQLite 与进程内存里，**只能跑一个实例**（不要开水平扩容）；重启会把进行中的运行标为"被中断"，用户重新发起即可。
- **没有访问控制与配额**（PRD §8 本期不做）：拿到链接的人都能出题并消耗你的模型额度。首次上线建议只把链接发给小范围的人；公开前需要加限流或访问码（尚未实现）。
- **调试台含用户内容**（教师的原话与生成的题目）：令牌要设强一点，且不要把 `/debug` 的链接外传。
- **费用观察**：调试台首页有"单次成本 均值"；一次整卷约 ¥0.3 左右（见 `eval/reports`、`scripts/spend.py`）。
- **拍照出题**会把用户上传的图片存进数据目录（`uploads/`，不入库、不进事件日志）并发往视觉模型；每张最大 12MB、一次最多 4 张；公网部署时同样没有访问控制与配额，建议在入口层限流。

## 资源

- 常驻内存：chalkbase 数据加载约 +62MB（实测），加上 Python 与 FastAPI，预计 300～500MB；导出 PDF 时 Typst 峰值另计。先选 1GB 内存观察。
- 中文字体：镜像里装了 `fonts-noto-cjk`（PDF 导出与图形文字需要）；镜像体积因此偏大。
- 冷启动约几秒（载入知识库与预热模型连接，`/api/warmup` 由页面打开时触发）。

## 本地用同一个镜像形态试跑

```bash
docker build -t verichalk .
docker run --rm -p 8000:8000 -v verichalk-data:/data \
  -e VERICHALK_PROFILE=intl -e VERICHALK_DEBUG_TOKEN=change-me \
  -e DASHSCOPE_INTL_API_KEY=... -e DASHSCOPE_INTL_BASE_URL=... verichalk
```

没有 Docker 时，等价的本地形态：`pip install ./backend && (cd frontend && npm ci && npm run build) && python -m verichalk serve`。

## 持续集成

`.github/workflows/ci.yml`，每次 push 到 `main` 与每个 PR：

| 作业 | 内容 |
|---|---|
| `backend` | ruff、pyright、全部后端测试（无网络、无密钥） |
| `frontend` | 前端类型与后端契约一致（`scripts/gen_types.py --check`）、tsc / ESLint / Prettier / vitest、构建 |
| `e2e` | Playwright + Chromium，真实后端 + 模型回放（`eval/cassettes/e2e`，无网络、无密钥）：12 个脚本化用户场景 |

模型回放的录制随提示词变化会失效（`replay 未命中`）：改了提示词后，删掉 `eval/cassettes/e2e`，用 `E2E_MODE=replay_or_record`（需要 `DASHSCOPE_*`）整体重录并提交。

# VeriChalk

**面向小学数学教师的可验证命题 Agent。** 用一句话或一张练习册照片说清需求，几十秒内得到答案经核验、不超纲、有新意的题目或整份试卷；在线微调，一键导出 PDF / Word / Markdown / LaTeX。

课程知识（北师大版 1～6 年级：知识图谱、题型、能力边界、情境）来自 [ChalkBase](https://github.com/Scorpioyyy/chalkbase)。

> 状态：开发中。后端（理解 / 规划 / 创作与核验 / 编辑与组卷 / 导出）、用户端与调试台已可本地跑通；拍照出题（M4）已完成（后端 + 用户端）；云端部署（M8）未完成。路线图见 [docs/roadmap.md](docs/roadmap.md)。

## 设计要点

- **知识库是上下文，不是模板。** 教材题告诉 Agent"学生学过什么"，新题由知识图谱串联多个知识点、在新情境里创作，而不是改教材原题。用户明确要求优先。
- **答案可核验，且诚实标注。** 求解程序与独立盲解两路一致才标"已核验"；超纲由课时级能力边界校验。
- **一切可观测。** 每次运行是一棵 span 树；用户的进度条、开发者的调试台、指标与评测读的是同一份事件日志。
- **评测先行。** 指标分北极星 / 闸门 / 目标 / 护栏 / 诊断五层（[docs/metrics.md](docs/metrics.md)），badcase 闭环到回归集。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/brief.md](docs/brief.md) | 项目委托书：目标、约束与工作方式要求（需求来源） |
| [docs/prd.md](docs/prd.md) | 产品需求：用户、场景、原则、功能与验收 |
| [docs/metrics.md](docs/metrics.md) | 指标体系 |
| [docs/architecture.md](docs/architecture.md) | 分层架构、数据模型、事件协议、阶段、调试台 |
| [docs/design.md](docs/design.md) | 设计决策记录（D1…） |
| [docs/evaluation.md](docs/evaluation.md) | 评测方案、数据集、判官、badcase 闭环 |
| [docs/roadmap.md](docs/roadmap.md) | 里程碑与出口标准 |
| [CLAUDE.md](CLAUDE.md) | 工程规则 |

## 本地运行

需要环境变量 `DASHSCOPE_API_KEY` / `DASHSCOPE_BASE_URL`（国内）或 `DASHSCOPE_INTL_API_KEY` / `DASHSCOPE_INTL_BASE_URL`（新加坡），以及 `VERICHALK_PROFILE=cn|intl`。Python ≥ 3.11、Node ≥ 20。

```bash
pip install -e "backend[dev]"        # 后端（依赖 chalkbase）
cd frontend && npm install && npm run build && cd ..
python scripts/dev.py run            # http://localhost:8000：用户端 /，调试台 /debug（本机未配置令牌时直接可进）
```

前端开发：`cd frontend && npm run dev`（Vite，`/api` 代理到 8000）。完整命令见 [CLAUDE.md §8](CLAUDE.md)。

## 许可

[MIT](LICENSE)

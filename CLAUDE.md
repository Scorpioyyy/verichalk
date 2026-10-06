# VeriChalk 工程规则

## 1. 项目背景

VeriChalk 是面向小学数学教师的**可验证命题 Agent**：自然语言或练习册照片 → 不超纲、答案经核验、有新意的题目与试卷 → 在线编辑 → 导出。课程知识由独立项目 [ChalkBase](https://github.com/Scorpioyyy/chalkbase)（`pip install chalkbase`）提供。最终形态是前后端全栈服务，部署到 Zeabur。

文档入口：[docs/brief.md](docs/brief.md)（项目委托书：需求来源）→ [docs/prd.md](docs/prd.md)（做什么）→ [docs/metrics.md](docs/metrics.md)（怎样算做好）→ [docs/architecture.md](docs/architecture.md)（怎么搭）→ [docs/design.md](docs/design.md)（为什么这样）→ [docs/evaluation.md](docs/evaluation.md)（怎么评）→ [docs/roadmap.md](docs/roadmap.md)（节奏）。

## 2. 工程原则

1. **评测先于实现。** 每个阶段开工前先写 `eval/specs/<阶段>.md`（失败模式、检测手段、评测数据、阈值与动作、基线），跑出基线再实现。
2. **用户视角先于技术视角。** 任何设计先回答"教师会怎样用、会在哪里卡住"。界面文案与对话使用教师语言，不暴露内部概念。
3. **分层与契约。** 依赖只能向下（[architecture §2](docs/architecture.md)，由 `tests/test_architecture.py` 强制）；领域模型是前后端契约的唯一来源（OpenAPI → TypeScript 类型自动生成，不手写）。
4. **在根因所在的层修复，不打补丁。** 修复前先归因（[evaluation §6](docs/evaluation.md)）；改动涉及 3 个以上模块时，先回到架构文档确认是否缺少抽象；修复必须附回归用例和前后指标对比。
5. **确定性优先。** 能用规则或算法完成的不交给模型；模型只用在需要语义理解 / 生成的地方，并且每次调用都在 trace 里可查。
6. **一切可观测。** 业务代码里的阶段、工具、模型调用都必须在 `trace.span(...)` 内；不允许绕过网关直连模型、绕过沙箱执行代码。
7. **不预先加组件。** 新组件、新依赖、新字段必须对应已观察到的失败或未达标的指标；非显然的决策记入 `docs/design.md`（`D<n>`）。
8. **数值精确。** 小数、分数一律 `Decimal` / `Fraction`，禁止浮点参与答案计算与比较。
9. **单一事实来源。** 知识只来自 chalkbase 的公开接口；派生物（TypeScript 类型、指标报告）由脚本生成，不手工编辑。
10. **密钥与隐私。** 密钥只在环境变量里，绝不写入文件、日志、trace、提交；上传图片与会话内容不入库；日志脱敏有测试保证。
11. **消融验证，拒绝堆砌。** 每个模块都是可关闭的特性开关（`core/features.py`），进入主路径的同一里程碑内完成首轮消融（[evaluation §9](docs/evaluation.md)）；没有可度量收益的模块不保留。

## 3. 评测与标注

- 三层评测、判官校准、模型选型、badcase 闭环见 [docs/evaluation.md](docs/evaluation.md)；指标定义见 [docs/metrics.md](docs/metrics.md)。
- **不允许为了过线修改评测**；评测定义变化记录在 `eval/CHANGELOG.md`，并用新定义重算历史。
- 标注数据用多模型交叉标注 + 抽样复核（方法同 ChalkBase）；判官与被评对象不同源。
- val 用于开发调参，test 只在阶段 / 里程碑验收时使用。

## 4. 模型调用规范

- 通过 `verichalk.llm` 网关调用，业务代码只写**角色**（`fast / smart / vision / judge / solver / embed`），不写模型名。
- 密钥与端点来自环境变量：`cn` profile 用 `DASHSCOPE_API_KEY`、`DASHSCOPE_BASE_URL`；`intl` profile 用 `DASHSCOPE_INTL_API_KEY`、`DASHSCOPE_INTL_BASE_URL`；`VERICHALK_PROFILE` 选择。**端点主机名属于敏感信息，不写入任何受版本控制的文件或日志。**
- 思考模式必须显式指定；提示词放在 `prompts/`，带 `id / version / role`；提示布局遵守"静态段 → 稳定段 → 动态段"的缓存友好顺序。
- 本机 HTTP 代理对阿里云端点不稳定：网关对 `.aliyuncs.com` 直连。

## 5. 代码规范

- Python：类型标注完整（`pyright` 通过）；`ruff` 格式与检查；公共函数写中文 docstring，说明输入、输出、不变量；标识符用英文。
- TypeScript：`strict`；ESLint + Prettier；组件按 `shared / user / debug` 分区，调试台与用户端不互相导入。
- 测试：新增行为必须有测试；涉及模型的集成测试使用 `replay` 录制，不在 CI 里调用真实模型。
- 提交信息用英文祈使句，正文说明动机；一次提交只做一件事。

## 6. 目录整洁

每个文件必须属于：源代码、配置、文档、评测资产（用例 / 标注 / 报告 / 录制）、生成物（且有生成脚本）。不属于任何一类的即为冗余，应删除。

- 临时文件只能放 `tmp/`，每个里程碑验收后清空；一次性脚本用完即删，会复用的放 `scripts/`。
- 被取代的产物直接覆盖或删除，不留 `_v2` / `_old` / `_backup`，历史由 git 负责。
- 运行时数据（SQLite、上传、导出）在 `VERICHALK_DATA_DIR`（默认 `data/`，不入库）。
- 新增顶层目录须先更新本文件第 7 节；`tests/test_layout.py` 检查顶层条目与 `tmp/` 为空。

## 7. 目录结构

```
verichalk/
├── README.md  CLAUDE.md  LICENSE  Dockerfile  .env.example  .editorconfig
├── backend/
│   ├── pyproject.toml
│   ├── src/verichalk/        core domain llm trace store sandbox knowledge verify figures render perception
│   │                         tools stages orchestrator metrics api eval prompts
│   └── tests/
├── frontend/                 Vite + React + TS（user / debug / shared）
├── config/                   models.yaml  pricing.yaml
├── eval/                     specs/  datasets/  annotation/  rubrics/  badcases/  reports/  cassettes/  CHANGELOG.md
├── docs/                     prd  metrics  architecture  design  evaluation  roadmap
├── deploy/                   部署文档与脚本
├── scripts/                  可复用脚本（类型生成、开发辅助）
├── data/                     运行时数据（不入库）
└── tmp/                      临时文件（不入库）
```

## 8. 环境与命令

- Python 环境：conda 环境 `verichalk`（Python 3.11）；Node ≥ 20。
- 知识库：`pip install -e ../chalkbase`（开发联调）或 `pip install chalkbase`。
- 常用命令（均在仓库根目录）：

```bash
pip install -e "backend[dev]"                 # 后端依赖
python scripts/dev.py check                   # ruff + pyright + pytest（L1）
python scripts/dev.py run                     # 本地启动后端（localhost:8000）
python -m verichalk.eval run --suite core --split val --mode replay
python scripts/probe_produce.py "四年级下册小数加减法，出5道" --items   # M3 快速诊断：只跑生成阶段，约 1 分钟
python scripts/compare_sets.py a.json b.json                        # 两套产出的盲评对比（好题）
python scripts/spend.py                                              # 累计模型花费
python scripts/make_photo_variants.py                               # 拍照评测的变体集与负例集（照片在 eval/datasets/photos/raw/，不入库）
python -m verichalk.eval perceive --set raw,variant,negative --split all   # 拍照识别评测（B4 / B5 / P5 / P6）
python -m verichalk.eval photo-e2e                                   # 拍照出题端到端（P8 防雷同 / P9）
python scripts/make_worksheet_fixture.py                            # 重新生成端到端用的合成练习页（无版权）

cd frontend && npm install                    # 前端依赖（Node ≥ 20）
npm run dev                                   # Vite 开发服务（localhost:5173，/api 代理到 :8000）
npm run check                                 # tsc + eslint + prettier + vitest
npm run build                                 # 构建到 frontend/dist（后端同源托管）
python scripts/gen_types.py [--check]         # 由后端 OpenAPI 生成前端类型（--check 查漂移）
E2E_MODE=replay npx playwright test           # 端到端（模型回放）；E2E_MODE=replay_or_record 补录（整体重录先删 eval/cassettes/e2e）；本机用 Edge 时加 PW_CHANNEL=msedge
```
评测纪律：迭代用上面的小样本工具，完整评测（含审计）只在里程碑验收时整套跑；接续指南见 `eval/specs/produce.md §9`。

## 9. 流程

- 每个里程碑：评测规格 → 基线 → 实现 → 验收（test 划分）→ 清理 → 提交并推送。
- 仓库为公开 GitHub 仓库（`github.com/Scorpioyyy/verichalk`）；不入库：密钥、`.env`、运行时数据、拍照评测集（真实照片、标注、识别报告与录制：含教辅题面）、模型响应录制中的用户内容。

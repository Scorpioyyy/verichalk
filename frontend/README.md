# VeriChalk 前端

Vite + React + TypeScript（strict）。一个应用、两个入口：`/` 用户端（教师）、`/debug` 调试台（开发者）。设计决策见 [docs/design.md](../docs/design.md) D47～D50，评测见 [eval/specs/frontend.md](../eval/specs/frontend.md)。

```
src/
  shared/   api（schema.gen.ts 由后端 OpenAPI 生成，别手改）· events（事件 → 状态的纯函数）· math（题目文本解析与 KaTeX）· ui（设计令牌与基础组件）· labels.ts（教师语言）
  user/     controller.ts（会话控制器，不依赖 React）· chat/ · paper/（工作台、编辑器、导出、版本历史）
  debug/    model.ts（事件 → span 树 / 调用 / 检索 / 题目证据）· 各视图
e2e/        Playwright 端到端（真实后端 + 模型回放）
```

## 命令

```bash
npm install
npm run dev            # http://localhost:5173，/api 代理到 http://127.0.0.1:8000（先启动后端）
npm run check          # tsc + eslint + prettier + vitest（单元 / 组件 / 公式语料）
npm run build          # 构建到 dist/，由后端同源托管（python -m verichalk serve）
python ../scripts/gen_types.py          # 后端接口变了：重新生成类型；加 --check 只检查是否漂移
E2E_MODE=replay npx playwright test     # 端到端（模型回放，确定、零成本）；本机用 Edge：PW_CHANNEL=msedge
```

## 约定

- `user/` 与 `debug/` 互不导入（ESLint 强制），两者只依赖 `shared/`。
- 界面里不出现内部概念：内部标识到教师语言的映射只在 `shared/labels.ts`；知识点取不到名称就不显示，绝不显示 ID。
- 运行状态是事件的纯函数（`shared/events/reducer.ts`）；试卷内容由服务端持有，前端不重新实现补丁。
- 新增界面必须过：axe（serious / critical 为 0）、三种宽度无横向滚动、无内部词（`e2e/helpers.ts`）。

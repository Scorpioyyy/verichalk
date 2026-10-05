# 评测报告：understand_para / val

- 时间：2026-10-05 18:59:51（用时 4.8s）　提交：`2ad3142+dirty`　profile：`intl`　模式：`replay`
- 模型角色：fast=deepseek-v4.1-flash，smart=qwen3.8-max，vision=qwen3.8-flash，judge=deepseek-v4.1-flash，solver=deepseek-v4.1-flash
- 特性开关：全部开启

## 总览

| 指标 | 值 |
|---|---|
| 用例数 / 运行数 | 85 / 85 |
| F1 运行成功率 | 1.000（85/85，95% CI 0.957–1.000） |
| F6 trace 完整度 | 1.000（85/85，95% CI 0.957–1.000） |
| D3 端到端时长 | p50 240ms / p95 1294ms（n=85） |
| D1 首反馈（服务端） | p50 24ms / p95 61ms（n=85） |
| E2 KV 缓存命中率 | 0.963 |
| E1 单次运行成本 | p50 0.0006 / p95 0.0010 |
| E3 每次运行的模型调用数 | 均值 1.1 |
| F2 模型错误率 | 0.000（0/93，95% CI 0.000–0.040） |
| E5 结构化输出重试率 | 0.000（0/93，95% CI 0.000–0.040） |

## 检查项通过率

| 检查 | 通过率 |
|---|---|
| absent:constraints | 0.985（65/66，95% CI 0.919–0.997） |
| absent:count | 1.000（14/14，95% CI 0.785–1.000） |
| absent:difficulty | 1.000（61/61，95% CI 0.941–1.000） |
| absent:kinds | 1.000（57/57，95% CI 0.937–1.000） |
| absent:scenes | 1.000（64/64，95% CI 0.943–1.000） |
| absent:source | 1.000（68/68，95% CI 0.947–1.000） |
| absent:tier | 1.000（55/55，95% CI 0.935–1.000） |
| absent:topics | 1.000（13/13，95% CI 0.772–1.000） |
| clarify | 1.000（85/85，95% CI 0.957–1.000） |
| field:action | 0.833（5/6，95% CI 0.436–0.970） |
| field:constraints_any | 1.000（3/3，95% CI 0.438–1.000） |
| field:count | 1.000（58/58，95% CI 0.938–1.000） |
| field:difficulty | 0.750（6/8，95% CI 0.409–0.929） |
| field:grade | 1.000（69/69，95% CI 0.947–1.000） |
| field:kinds | 0.833（10/12，95% CI 0.552–0.953） |
| field:paper | 1.000（4/4，95% CI 0.510–1.000） |
| field:scenes_any | 1.000（5/5，95% CI 0.566–1.000） |
| field:semester | 1.000（33/33，95% CI 0.896–1.000） |
| field:source | 1.000（1/1，95% CI 0.207–1.000） |
| field:tier | 0.917（11/12，95% CI 0.646–0.985） |
| field:topics_any | 0.981（53/54，95% CI 0.902–0.997） |
| field:unit | 1.000（4/4，95% CI 0.510–1.000） |
| no_secrets | 1.000（85/85，95% CI 0.957–1.000） |
| origin:action | 0.833（5/6，95% CI 0.436–0.970） |
| origin:count | 1.000（58/58，95% CI 0.938–1.000） |
| origin:difficulty | 0.875（7/8，95% CI 0.529–0.978） |
| origin:grade | 1.000（67/67，95% CI 0.946–1.000） |
| origin:kinds | 0.833（10/12，95% CI 0.552–0.953） |
| origin:semester | 1.000（33/33，95% CI 0.896–1.000） |
| origin:source | 1.000（1/1，95% CI 0.207–1.000） |
| origin:tier | 0.917（11/12，95% CI 0.646–0.985） |
| origin:unit | 1.000（4/4，95% CI 0.510–1.000） |
| route | 1.000（85/85，95% CI 0.957–1.000） |
| status | 1.000（85/85，95% CI 0.957–1.000） |
| trace_complete | 1.000（85/85，95% CI 0.957–1.000） |

## 按标签切片（用例整体通过率）

| 标签 | 通过率 |
|---|---|
| S1 | 0.976（41/42，95% CI 0.877–0.996） |
| S2 | 0.700（7/10，95% CI 0.397–0.892） |
| S4 | 0.900（9/10，95% CI 0.596–0.982） |
| S5 | 0.857（6/7，95% CI 0.487–0.974） |
| S9 | 1.000（16/16，95% CI 0.806–1.000） |
| clarify | 1.000（8/8，95% CI 0.676–1.000） |
| g1 | 1.000（2/2，95% CI 0.342–1.000） |
| g2 | 1.000（1/1，95% CI 0.207–1.000） |
| g3 | 1.000（4/4，95% CI 0.510–1.000） |
| g4 | 1.000（6/6，95% CI 0.610–1.000） |
| g5 | 1.000（6/6，95% CI 0.610–1.000） |
| gg | 1.000（6/6，95% CI 0.610–1.000） |
| grade-only | 1.000（7/7，95% CI 0.646–1.000） |
| na | 1.000（11/11，95% CI 0.741–1.000） |
| offtopic | 1.000（8/8，95% CI 0.676–1.000） |
| paper | 0.900（9/10，95% CI 0.596–0.982） |
| para | 0.929（79/85，95% CI 0.854–0.967） |
| review | 0.857（6/7，95% CI 0.487–0.974） |
| scene | 0.778（7/9，95% CI 0.453–0.937） |
| sp | 1.000（2/2，95% CI 0.342–1.000） |
| template | 0.000（0/1，95% CI 0.000–0.793） |
| topic-only | 1.000（2/2，95% CI 0.342–1.000） |
| variants | 0.929（13/14，95% CI 0.685–0.987） |

## 意图理解（B 组）

| 指标 | 值 | 验收线 |
|---|---|---|
| **B1 字段通过率** | 0.974（262/269，95% CI 0.947–0.987） | ≥ 0.90 |
| B1-origin（明说的字段来源为 user） | 0.975（196/201，95% CI 0.943–0.989） | ≥ 0.95 |
| B1-幻觉率（没说却断言） | 0.003（1/398，95% CI 0.000–0.014） | ≤ 0.03 |
| **B2 澄清精确率** | 1.000（8/8，95% CI 0.676–1.000） | ≥ 0.85 |
| **B2 澄清召回** | 1.000（8/8，95% CI 0.676–1.000） | ≥ 0.80 |
| B2 提问占比 | 0.094（8/85，95% CI 0.048–0.175） | ≤ 0.15 |
| **B3 路由准确率** | 1.000（85/85，95% CI 0.957–1.000） | ≥ 0.95 |
| 理解阶段延迟 | p50 198ms / p95 1260ms（n=85） | p50 ≤ 2500ms |
| 解析方式 | {'llm': 85} | |

### B1 按字段

| 字段 | 通过率 |
|---|---|
| action | 0.833（5/6，95% CI 0.436–0.970） |
| constraints_any | 1.000（3/3，95% CI 0.438–1.000） |
| count | 1.000（58/58，95% CI 0.938–1.000） |
| difficulty | 0.750（6/8，95% CI 0.409–0.929） |
| grade | 1.000（69/69，95% CI 0.947–1.000） |
| kinds | 0.833（10/12，95% CI 0.552–0.953） |
| paper | 1.000（4/4，95% CI 0.510–1.000） |
| scenes_any | 1.000（5/5，95% CI 0.566–1.000） |
| semester | 1.000（33/33，95% CI 0.896–1.000） |
| source | 1.000（1/1，95% CI 0.207–1.000） |
| tier | 0.917（11/12，95% CI 0.646–0.985） |
| topics_any | 0.981（53/54，95% CI 0.902–0.997） |
| unit | 1.000（4/4，95% CI 0.510–1.000） |

### B3 路由混淆（仅错误项）

无错误。

### 失败类别计数（前 15）

| 检查项 | 失败数 |
|---|---|
| field:kinds | 2 |
| origin:kinds | 2 |
| field:difficulty | 2 |
| field:topics_any | 1 |
| absent:constraints | 1 |
| field:tier | 1 |
| origin:difficulty | 1 |
| origin:tier | 1 |
| field:action | 1 |
| origin:action | 1 |

## 失败样本（6）

- **p-d13-b** [S1, variants, para]
  - ✗ field:topics_any：期望知识点含 ['时']，实际映射 ['用乘法口诀求商', '10以内数字的书写', '5的乘法口诀']（run `run_01M45VGQBAD6F007DQ745RQF38`）
- **p-e07-a** [S2, scene, para]
  - ✗ field:kinds：期望包含 ['calc']，实际 []（run `run_01M45VGQPBH5VKTT7P0CZ1NWRE`）
  - ✗ origin:kinds：来源应为 user，实际 无（run `run_01M45VGQPBH5VKTT7P0CZ1NWRE`）
  - ✗ absent:constraints：用户没说，却断言了具体值（run `run_01M45VGQPBH5VKTT7P0CZ1NWRE`）
- **p-e07-b** [S2, scene, para]
  - ✗ field:kinds：期望包含 ['calc']，实际 ['application']（run `run_01M45VGQPHMC6QPE7PZZDDC60G`）
  - ✗ origin:kinds：来源应为 user，实际 inferred（run `run_01M45VGQPHMC6QPE7PZZDDC60G`）
- **p-e11-a** [S2, template, para]
  - ✗ field:tier：期望 consolidate，实际 None（run `run_01M45VGR0NM2B6J6YPTQ3757TW`）
  - ✗ field:difficulty：未解析出难度（run `run_01M45VGR0NM2B6J6YPTQ3757TW`）
  - ✗ origin:difficulty：来源应为 user，实际 default（run `run_01M45VGR0NM2B6J6YPTQ3757TW`）
  - ✗ origin:tier：来源应为 user，实际 default（run `run_01M45VGR0NM2B6J6YPTQ3757TW`）
- **p-g07-b** [S4, paper, para]
  - ✗ field:difficulty：期望 {'min': 4}，实际 [3,5]（run `run_01M45VGRA7M8E1DF493SF98JTF`）
- **p-h01-a** [S5, review, para]
  - ✗ field:action：期望 review，实际 generate（run `run_01M45VGRA7FZ488KS5X2XG992J`）
  - ✗ origin:action：来源应为 user，实际 default（run `run_01M45VGRA7FZ488KS5X2XG992J`）

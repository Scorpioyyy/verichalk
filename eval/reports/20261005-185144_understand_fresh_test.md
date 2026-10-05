# 评测报告：understand_fresh / test

- 时间：2026-10-05 18:51:44（用时 3.5s）　提交：`2ad3142+dirty`　profile：`intl`　模式：`replay`
- 模型角色：fast=deepseek-v4.1-flash，smart=qwen3.8-max，vision=qwen3.8-flash，judge=deepseek-v4.1-flash，solver=deepseek-v4.1-flash
- 特性开关：关闭：understand.llm

## 总览

| 指标 | 值 |
|---|---|
| 用例数 / 运行数 | 42 / 47 |
| F1 运行成功率 | 1.000（47/47，95% CI 0.924–1.000） |
| F6 trace 完整度 | 1.000（47/47，95% CI 0.924–1.000） |
| D3 端到端时长 | p50 160ms / p95 2079ms（n=47） |
| D1 首反馈（服务端） | — |
| E2 KV 缓存命中率 | — |
| E1 单次运行成本 | p50 0.0000 / p95 0.0000 |
| E3 每次运行的模型调用数 | 均值 0.0 |
| F2 模型错误率 | — |
| E5 结构化输出重试率 | — |

## 检查项通过率

| 检查 | 通过率 |
|---|---|
| absent:constraints | 1.000（27/27，95% CI 0.875–1.000） |
| absent:count | 1.000（10/10，95% CI 0.722–1.000） |
| absent:difficulty | 1.000（25/25，95% CI 0.867–1.000） |
| absent:kinds | 0.875（21/24，95% CI 0.690–0.957） |
| absent:scenes | 1.000（25/25，95% CI 0.867–1.000） |
| absent:source | 1.000（27/27，95% CI 0.875–1.000） |
| absent:tier | 1.000（26/26，95% CI 0.871–1.000） |
| absent:topics | 0.600（3/5，95% CI 0.231–0.882） |
| clarify | 0.894（42/47，95% CI 0.774–0.954） |
| field:count | 1.000（21/21，95% CI 0.845–1.000） |
| field:difficulty | 1.000（2/2，95% CI 0.342–1.000） |
| field:grade | 1.000（27/27，95% CI 0.875–1.000） |
| field:kinds | 1.000（3/3，95% CI 0.438–1.000） |
| field:paper | 0.000（0/1，95% CI 0.000–0.793） |
| field:scenes_any | 0.000（0/1，95% CI 0.000–0.793） |
| field:semester | 1.000（13/13，95% CI 0.772–1.000） |
| field:tier | 1.000（1/1，95% CI 0.207–1.000） |
| field:topics_any | 0.947（18/19，95% CI 0.754–0.991） |
| field:unit | 1.000（2/2，95% CI 0.342–1.000） |
| no_secrets | 1.000（47/47，95% CI 0.924–1.000） |
| origin:count | 1.000（21/21，95% CI 0.845–1.000） |
| origin:difficulty | 1.000（2/2，95% CI 0.342–1.000） |
| origin:grade | 1.000（25/25，95% CI 0.867–1.000） |
| origin:kinds | 1.000（3/3，95% CI 0.438–1.000） |
| origin:semester | 1.000（13/13，95% CI 0.772–1.000） |
| origin:tier | 1.000（1/1，95% CI 0.207–1.000） |
| origin:unit | 1.000（2/2，95% CI 0.342–1.000） |
| route | 0.809（38/47，95% CI 0.675–0.896） |
| status | 1.000（42/42，95% CI 0.916–1.000） |
| trace_complete | 1.000（47/47，95% CI 0.924–1.000） |

## 按标签切片（用例整体通过率）

| 标签 | 通过率 |
|---|---|
| S1 | 0.706（12/17，95% CI 0.469–0.867） |
| S2 | 0.500（1/2，95% CI 0.095–0.905） |
| S4 | 0.000（0/2，95% CI 0.000–0.658） |
| S6 | 0.667（2/3，95% CI 0.208–0.939） |
| S7 | 0.000（0/1，95% CI 0.000–0.793） |
| S8 | 1.000（1/1，95% CI 0.207–1.000） |
| S9 | 0.250（3/12，95% CI 0.089–0.532） |
| clarify | 0.375（3/8，95% CI 0.137–0.694） |
| conflict | 0.500（2/4，95% CI 0.150–0.850） |
| cross-grade | 1.000（5/5，95% CI 0.566–1.000） |
| multi | 0.600（3/5，95% CI 0.231–0.882） |
| no-clarify | 0.800（4/5，95% CI 0.376–0.964） |
| offtopic | 0.250（2/8，95% CI 0.071–0.591） |
| paper | 0.000（0/2，95% CI 0.000–0.658） |
| primary-edge | 0.400（2/5，95% CI 0.118–0.769） |
| voice | 0.500（2/4，95% CI 0.150–0.850） |

## 意图理解（B 组）

| 指标 | 值 | 验收线 |
|---|---|---|
| **B1 字段通过率** | 0.967（87/90，95% CI 0.907–0.989） | ≥ 0.90 |
| B1-origin（明说的字段来源为 user） | 1.000（67/67，95% CI 0.946–1.000） | ≥ 0.95 |
| B1-幻觉率（没说却断言） | 0.030（5/169，95% CI 0.013–0.067） | ≤ 0.03 |
| **B2 澄清精确率** | 1.000（3/3，95% CI 0.438–1.000） | ≥ 0.85 |
| **B2 澄清召回** | 0.375（3/8，95% CI 0.137–0.694） | ≥ 0.80 |
| B2 提问占比 | 0.064（3/47，95% CI 0.022–0.172） | ≤ 0.15 |
| **B3 路由准确率** | 0.809（38/47，95% CI 0.675–0.896） | ≥ 0.95 |
| 理解阶段延迟 | p50 87ms / p95 2035ms（n=47） | p50 ≤ 2500ms |
| 解析方式 | {'rules': 47} | |

### B1 按字段

| 字段 | 通过率 |
|---|---|
| count | 1.000（21/21，95% CI 0.845–1.000） |
| difficulty | 1.000（2/2，95% CI 0.342–1.000） |
| grade | 1.000（27/27，95% CI 0.875–1.000） |
| kinds | 1.000（3/3，95% CI 0.438–1.000） |
| paper | 0.000（0/1，95% CI 0.000–0.793） |
| scenes_any | 0.000（0/1，95% CI 0.000–0.793） |
| semester | 1.000（13/13，95% CI 0.772–1.000） |
| tier | 1.000（1/1，95% CI 0.207–1.000） |
| topics_any | 0.947（18/19，95% CI 0.754–0.991） |
| unit | 1.000（2/2，95% CI 0.342–1.000） |

### B3 路由混淆（仅错误项）

- 期望 `offtopic` → 实际 `generate`：6
- 期望 `paper` → 实际 `generate`：1
- 期望 `ask` → 实际 `generate`：1
- 期望 `edit` → 实际 `ask`：1

### 失败类别计数（前 15）

| 检查项 | 失败数 |
|---|---|
| route | 9 |
| clarify | 5 |
| absent:kinds | 3 |
| absent:topics | 2 |
| field:paper | 1 |
| field:topics_any | 1 |
| field:scenes_any | 1 |

## 失败样本（21）

- **f-01** [S1, primary-edge]
  - ✗ absent:kinds：用户没说，却断言了具体值（run `run_01M45V1RNJXRR8HPN2QYFJ9G06`）
- **f-02** [S1, primary-edge]
  - ✗ absent:kinds：用户没说，却断言了具体值（run `run_01M45V1RNJCX5FCC74X8DNSAMA`）
- **f-05** [S1, primary-edge]
  - ✗ absent:kinds：用户没说，却断言了具体值（run `run_01M45V1RP26PWS10WPQPF5XM66`）
- **f-12** [S9, offtopic]
  - ✗ route：期望 offtopic，实际 generate（run `run_01M45V1TSP7FJ8ZP0KJX1AD3F9`）
- **f-13** [S9, offtopic]
  - ✗ route：期望 offtopic，实际 generate（run `run_01M45V1TSPBBMR6B52QT77MJ79`）
- **f-14** [S9, offtopic]
  - ✗ route：期望 offtopic，实际 generate（run `run_01M45V1TSPVZJMYQNA7TTC2VAX`）
- **f-15** [S9, offtopic]
  - ✗ route：期望 offtopic，实际 generate（run `run_01M45V1TSPENR2GY69S1CN08FY`）
- **f-16** [S9, offtopic]
  - ✗ route：期望 offtopic，实际 generate（run `run_01M45V1TT64GSQ1XRXWPT92Z46`）
- **f-18** [S9, offtopic]
  - ✗ route：期望 offtopic，实际 generate（run `run_01M45V1V0D4YRDQ85QC8F6E6QK`）
- **f-20** [clarify, S9]
  - ✗ clarify：期望提问，实际未提问（run `run_01M45V1V1Z6FC44RDX4JDMT5D4`）
- **f-21** [clarify, S9]
  - ✗ clarify：期望提问，实际未提问（run `run_01M45V1V27A1FD7BSVPFJGKXB6`）
- **f-22** [clarify, S9]
  - ✗ clarify：期望提问，实际未提问（run `run_01M45V1V27MG6EEXFYSE1PQ56Q`）
- **f-24** [clarify, conflict]
  - ✗ clarify：期望提问，实际未提问（run `run_01M45V1V2G5GKWJ68RZ9DQEG4P`）
- **f-25** [clarify, conflict]
  - ✗ clarify：期望提问，实际未提问（run `run_01M45V1V6S1ZSW6CT7N2WZ679Q`）
- **f-27** [S1, no-clarify]
  - ✗ absent:topics：用户没说，却断言了具体值（run `run_01M45V1VB5E1E2MGK6JXTGVCG8`）
- **f-32** [S4, paper]
  - ✗ field:paper：期望 {'duration_min': 90, 'total_score': 100}，实际 {'duration_min': 90, 'total_score': None, 'structure': []}（run `run_01M45V1VHHVY5GCJAM8T8V9NGR`）
  - ✗ absent:topics：用户没说，却断言了具体值（run `run_01M45V1VHHVY5GCJAM8T8V9NGR`）
- **f-33** [S4, paper]
  - ✗ route：期望 paper，实际 generate（run `run_01M45V1VJ1T3NDF3FDHE4NR9DR`）
- **f-35** [S7, multi]
  - ✗ route：期望 ask，实际 generate（run `run_01M45V1VXH21S4GJFXYFJXF7YF`）
- **f-37** [S6, multi]
  - ✗ route：期望 edit，实际 ask（run `run_01M45V1VZ015SB7XR97EFY3GWD`）
- **f-40** [S1, voice]
  - ✗ field:topics_any：期望知识点含 ['20以内']，实际映射 ['10以内加法表、减法表的规律']（run `run_01M45V1VSK81CW204DVKB41RF7`）
- **f-41** [S2, voice]
  - ✗ field:scenes_any：期望含 ['六一', '儿童节']，实际 ''（run `run_01M45V1VSQDBE602BDDYRSJR68`）

# 评测报告：understand_para / test

- 时间：2026-10-05 18:51:35（用时 4.9s）　提交：`2ad3142+dirty`　profile：`intl`　模式：`replay`
- 模型角色：fast=deepseek-v4.1-flash，smart=qwen3.8-max，vision=qwen3.8-flash，judge=deepseek-v4.1-flash，solver=deepseek-v4.1-flash
- 特性开关：关闭：understand.llm

## 总览

| 指标 | 值 |
|---|---|
| 用例数 / 运行数 | 94 / 94 |
| F1 运行成功率 | 1.000（94/94，95% CI 0.961–1.000） |
| F6 trace 完整度 | 1.000（94/94，95% CI 0.961–1.000） |
| D3 端到端时长 | p50 155ms / p95 1974ms（n=94） |
| D1 首反馈（服务端） | — |
| E2 KV 缓存命中率 | — |
| E1 单次运行成本 | p50 0.0000 / p95 0.0000 |
| E3 每次运行的模型调用数 | 均值 0.0 |
| F2 模型错误率 | — |
| E5 结构化输出重试率 | — |

## 检查项通过率

| 检查 | 通过率 |
|---|---|
| absent:constraints | 1.000（70/70，95% CI 0.948–1.000） |
| absent:count | 0.933（14/15，95% CI 0.702–0.988） |
| absent:difficulty | 1.000（71/71，95% CI 0.949–1.000） |
| absent:kinds | 1.000（60/60，95% CI 0.940–1.000） |
| absent:scenes | 1.000（68/68，95% CI 0.947–1.000） |
| absent:source | 1.000（76/76，95% CI 0.952–1.000） |
| absent:tier | 1.000（66/66，95% CI 0.945–1.000） |
| absent:topics | 0.238（5/21，95% CI 0.106–0.451） |
| clarify | 0.915（86/94，95% CI 0.841–0.956） |
| field:action | 0.714（5/7，95% CI 0.359–0.918） |
| field:constraints_any | 0.000（0/6，95% CI 0.000–0.390） |
| field:count | 1.000（63/63，95% CI 0.943–1.000） |
| field:difficulty | 0.400（2/5，95% CI 0.118–0.769） |
| field:grade | 1.000（73/73，95% CI 0.950–1.000） |
| field:kinds | 1.000（14/14，95% CI 0.785–1.000） |
| field:paper | 0.500（3/6，95% CI 0.188–0.812） |
| field:scenes_any | 1.000（6/6，95% CI 0.610–1.000） |
| field:semester | 0.975（39/40，95% CI 0.871–0.996） |
| field:tier | 0.875（7/8，95% CI 0.529–0.978） |
| field:topics_any | 0.943（50/53，95% CI 0.846–0.981） |
| field:unit | 1.000（2/2，95% CI 0.342–1.000） |
| no_secrets | 1.000（94/94，95% CI 0.961–1.000） |
| origin:action | 0.714（5/7，95% CI 0.359–0.918） |
| origin:count | 1.000（63/63，95% CI 0.943–1.000） |
| origin:difficulty | 0.600（3/5，95% CI 0.231–0.882） |
| origin:grade | 1.000（72/72，95% CI 0.949–1.000） |
| origin:kinds | 1.000（14/14，95% CI 0.785–1.000） |
| origin:semester | 0.975（39/40，95% CI 0.871–0.996） |
| origin:tier | 0.875（7/8，95% CI 0.529–0.978） |
| origin:unit | 1.000（2/2，95% CI 0.342–1.000） |
| route | 0.936（88/94，95% CI 0.868–0.970） |
| status | 1.000（94/94，95% CI 0.961–1.000） |
| trace_complete | 1.000（94/94，95% CI 0.961–1.000） |

## 按标签切片（用例整体通过率）

| 标签 | 通过率 |
|---|---|
| S1 | 0.652（30/46，95% CI 0.508–0.773） |
| S2 | 0.750（9/12，95% CI 0.468–0.911） |
| S4 | 0.000（0/10，95% CI 0.000–0.278） |
| S5 | 0.571（4/7，95% CI 0.250–0.842） |
| S9 | 0.368（7/19，95% CI 0.191–0.590） |
| clarify | 0.000（0/8，95% CI 0.000–0.324） |
| edge | 1.000（1/1，95% CI 0.207–1.000） |
| g1 | 1.000（2/2，95% CI 0.342–1.000） |
| g2 | 1.000（4/4，95% CI 0.510–1.000） |
| g3 | 1.000（2/2，95% CI 0.342–1.000） |
| g4 | 0.750（3/4，95% CI 0.301–0.954） |
| g5 | 1.000（2/2，95% CI 0.342–1.000） |
| g6 | 0.667（4/6，95% CI 0.300–0.903） |
| gg | 0.667（4/6，95% CI 0.300–0.903） |
| grade-only | 0.286（2/7，95% CI 0.082–0.641） |
| na | 0.917（11/12，95% CI 0.646–0.985） |
| offtopic | 0.600（6/10，95% CI 0.313–0.832） |
| paper | 0.000（0/10，95% CI 0.000–0.278） |
| para | 0.532（50/94，95% CI 0.432–0.630） |
| review | 0.571（4/7，95% CI 0.250–0.842） |
| scene | 0.700（7/10，95% CI 0.397–0.892） |
| sp | 1.000（2/2，95% CI 0.342–1.000） |
| topic-only | 1.000（4/4，95% CI 0.510–1.000） |
| variants | 0.467（7/15，95% CI 0.248–0.699） |
| variation | 1.000（2/2，95% CI 0.342–1.000） |

## 意图理解（B 组）

| 指标 | 值 | 验收线 |
|---|---|---|
| **B1 字段通过率** | 0.933（264/283，95% CI 0.898–0.957） | ≥ 0.90 |
| B1-origin（明说的字段来源为 user） | 0.972（205/211，95% CI 0.939–0.987） | ≥ 0.95 |
| B1-幻觉率（没说却断言） | 0.038（17/447，95% CI 0.024–0.060） | ≤ 0.03 |
| **B2 澄清精确率** | — | ≥ 0.85 |
| **B2 澄清召回** | 0.000（0/8，95% CI 0.000–0.324） | ≥ 0.80 |
| B2 提问占比 | 0.000（0/94，95% CI 0.000–0.039） | ≤ 0.15 |
| **B3 路由准确率** | 0.936（88/94，95% CI 0.868–0.970） | ≥ 0.95 |
| 理解阶段延迟 | p50 88ms / p95 1920ms（n=94） | p50 ≤ 2500ms |
| 解析方式 | {'rules': 94} | |

### B1 按字段

| 字段 | 通过率 |
|---|---|
| action | 0.714（5/7，95% CI 0.359–0.918） |
| constraints_any | 0.000（0/6，95% CI 0.000–0.390） |
| count | 1.000（63/63，95% CI 0.943–1.000） |
| difficulty | 0.400（2/5，95% CI 0.118–0.769） |
| grade | 1.000（73/73，95% CI 0.950–1.000） |
| kinds | 1.000（14/14，95% CI 0.785–1.000） |
| paper | 0.500（3/6，95% CI 0.188–0.812） |
| scenes_any | 1.000（6/6，95% CI 0.610–1.000） |
| semester | 0.975（39/40，95% CI 0.871–0.996） |
| tier | 0.875（7/8，95% CI 0.529–0.978） |
| topics_any | 0.943（50/53，95% CI 0.846–0.981） |
| unit | 1.000（2/2，95% CI 0.342–1.000） |

### B3 路由混淆（仅错误项）

- 期望 `offtopic` → 实际 `generate`：4
- 期望 `generate` → 实际 `paper`：2

### 失败类别计数（前 15）

| 检查项 | 失败数 |
|---|---|
| absent:topics | 16 |
| clarify | 8 |
| route | 6 |
| field:constraints_any | 6 |
| field:topics_any | 3 |
| field:difficulty | 3 |
| field:paper | 3 |
| origin:difficulty | 2 |
| field:action | 2 |
| origin:action | 2 |
| field:semester | 1 |
| origin:semester | 1 |
| field:tier | 1 |
| origin:tier | 1 |
| absent:count | 1 |

## 失败样本（44）

- **p-a04-a** [S1, g6, gg, para]
  - ✗ field:topics_any：期望知识点含 ['圆的周长']，实际映射 ['圆周率的意义']（run `run_01M45V1G1PQ0BDSZ9B0ZWSHPHR`）
- **p-a04-b** [S1, g6, gg, para]
  - ✗ field:topics_any：期望知识点含 ['圆的周长']，实际映射 ['圆周率的意义']（run `run_01M45V1G1P9QGJ2SYKH38V92T2`）
- **p-a10-b** [S1, g4, na, para]
  - ✗ field:semester：期望 a，实际 None（run `run_01M45V1J2M2G0F6MKYHAHP9ARH`）
  - ✗ origin:semester：来源应为 user，实际 无（run `run_01M45V1J2M2G0F6MKYHAHP9ARH`）
- **p-c02-b** [S1, grade-only, para]
  - ✗ absent:topics：用户没说，却断言了具体值（run `run_01M45V1JFT9Y47FKZE7WC1PJ7G`）
- **p-c04-b** [S1, grade-only, para]
  - ✗ absent:topics：用户没说，却断言了具体值（run `run_01M45V1JHGJ6ENAVYMSECGW6P0`）
- **p-c06-a** [S1, grade-only, para]
  - ✗ route：期望 generate，实际 paper（run `run_01M45V1JHGPR5F9H62RSNK7ZAY`）
- **p-c06-b** [S1, grade-only, para]
  - ✗ route：期望 generate，实际 paper（run `run_01M45V1JHQRJDXRADGXJTF1ZV3`）
  - ✗ absent:topics：用户没说，却断言了具体值（run `run_01M45V1JHQRJDXRADGXJTF1ZV3`）
- **p-c08-b** [S1, grade-only, para]
  - ✗ absent:topics：用户没说，却断言了具体值（run `run_01M45V1JJKJYMJSEDJ8WM29QVB`）
- **p-d02-a** [S1, variants, para]
  - ✗ field:difficulty：未解析出难度（run `run_01M45V1JJVZPY1SFNT6PGZ60FS`）
  - ✗ origin:difficulty：来源应为 user，实际 default（run `run_01M45V1JJVZPY1SFNT6PGZ60FS`）
- **p-d08-b** [S1, variants, para]
  - ✗ field:difficulty：未解析出难度（run `run_01M45V1JT2S73V3NY40JYD6JF1`）
  - ✗ origin:difficulty：来源应为 user，实际 default（run `run_01M45V1JT2S73V3NY40JYD6JF1`）
- **p-d14-a** [S1, variants, para]
  - ✗ field:constraints_any：期望含 ['不要图形']，实际 ''（run `run_01M45V1K0JRPT8TBRK9FNXXHGG`）
- **p-d14-b** [S1, variants, para]
  - ✗ field:constraints_any：期望含 ['不要图形']，实际 ''（run `run_01M45V1K160SAY554WJTRECS7P`）
  - ✗ field:topics_any：期望知识点含 ['分数']，实际映射 ['三角形内角和', '用圆规画圆', '四边形分类（平行四边形与梯形）']（run `run_01M45V1K160SAY554WJTRECS7P`）
- **p-d16-a** [S1, variants, para]
  - ✗ field:constraints_any：期望含 ['不要文字题']，实际 ''（run `run_01M45V1K16P5XNP67JRXEHRSDH`）
- **p-d16-b** [S1, variants, para]
  - ✗ field:constraints_any：期望含 ['不要文字题']，实际 ''（run `run_01M45V1K1RWS59S9GJ6WNQ90AJ`）
- **p-d18-a** [S1, variants, para]
  - ✗ field:constraints_any：期望含 ['解析']，实际 ''（run `run_01M45V1K1RQY1AYMCP28QB05TP`）
- **p-d18-b** [S1, variants, para]
  - ✗ field:constraints_any：期望含 ['解析']，实际 ''（run `run_01M45V1K20H15V15JK1TK9KF42`）
- **p-e04-a** [S2, scene, para]
  - ✗ field:difficulty：期望 {'min': 4}，实际 [3,5]（run `run_01M45V1KB9K5NCDSGF8NC8YHWX`）
- **p-e04-b** [S2, scene, para]
  - ✗ absent:topics：用户没说，却断言了具体值（run `run_01M45V1KBPEY4SVKDSKSAXY5HH`）
- **p-e08-a** [S2, scene, para]
  - ✗ field:tier：期望 integrated，实际 None（run `run_01M45V1KBTMYRRE1DFQ9F5XWMW`）
  - ✗ origin:tier：来源应为 user，实际 default（run `run_01M45V1KBTMYRRE1DFQ9F5XWMW`）
- **p-g02-a** [S4, paper, para]
  - ✗ field:paper：期望 {'duration_min': 60, 'total_score': 100}，实际 {'duration_min': 60, 'total_score': None, 'structure': []}（run `run_01M45V1KK6HN1DWDKYQX7K1GS0`）
  - ✗ absent:topics：用户没说，却断言了具体值（run `run_01M45V1KK6HN1DWDKYQX7K1GS0`）
- **p-g02-b** [S4, paper, para]
  - ✗ field:paper：期望 {'duration_min': 60, 'total_score': 100}，实际 {'duration_min': 60, 'total_score': None, 'structure': []}（run `run_01M45V1KK7AHMQAF05W7F9JNY3`）
  - ✗ absent:topics：用户没说，却断言了具体值（run `run_01M45V1KK7AHMQAF05W7F9JNY3`）
- **p-g04-a** [S4, paper, para]
  - ✗ absent:topics：用户没说，却断言了具体值（run `run_01M45V1KK9EGVXZ4V9NPW1KYBN`）
- **p-g04-b** [S4, paper, para]
  - ✗ absent:topics：用户没说，却断言了具体值（run `run_01M45V1KKDFT5XV196A9B6JHN5`）
- **p-g06-a** [S4, paper, para]
  - ✗ absent:topics：用户没说，却断言了具体值（run `run_01M45V1KKS88XH0TY7YEDBM998`）
- **p-g06-b** [S4, paper, para]
  - ✗ absent:topics：用户没说，却断言了具体值（run `run_01M45V1KMEBDVX2WX6AADW7S18`）
- **p-g08-a** [S4, paper, para]
  - ✗ absent:topics：用户没说，却断言了具体值（run `run_01M45V1KSH8Y4QQA6TC9A35P4R`）
- **p-g08-b** [S4, paper, para]
  - ✗ field:paper：期望 {'total_score': 50}，实际 {'duration_min': None, 'total_score': None, 'structure': []}（run `run_01M45V1KVBRD5N6B3FDZT4ZJJV`）
  - ✗ absent:count：用户没说，却断言了具体值（run `run_01M45V1KVBRD5N6B3FDZT4ZJJV`）
  - ✗ absent:topics：用户没说，却断言了具体值（run `run_01M45V1KVBRD5N6B3FDZT4ZJJV`）
- **p-g10-a** [S4, paper, para]
  - ✗ absent:topics：用户没说，却断言了具体值（run `run_01M45V1KVBJ9WHFGVWYE96S34K`）
- **p-g10-b** [S4, paper, para]
  - ✗ absent:topics：用户没说，却断言了具体值（run `run_01M45V1KVMDZ8XG29DAWZSAD1C`）
- **p-h02-b** [S5, review, para]
  - ✗ absent:topics：用户没说，却断言了具体值（run `run_01M45V1KVSEHBC415EBXY4QQJR`）
- **p-h04-a** [S5, review, para]
  - ✗ field:action：期望 review，实际 generate（run `run_01M45V1KVWXZ7AR2VP9B4K3R3E`）
  - ✗ origin:action：来源应为 user，实际 default（run `run_01M45V1KVWXZ7AR2VP9B4K3R3E`）
- **p-h04-b** [S5, review, para]
  - ✗ field:action：期望 review，实际 generate（run `run_01M45V1KWKQCY18SRKGH1PJXN9`）
  - ✗ origin:action：来源应为 user，实际 default（run `run_01M45V1KWKQCY18SRKGH1PJXN9`）
- **p-k02-a** [clarify, S9, para]
  - ✗ clarify：期望提问，实际未提问（run `run_01M45V1M3YP7PSNA4E3DQSC43D`）
- **p-k02-b** [clarify, S9, para]
  - ✗ clarify：期望提问，实际未提问（run `run_01M45V1M3YS6EXZE6NFXFFT86Q`）
- **p-k04-a** [clarify, S9, para]
  - ✗ clarify：期望提问，实际未提问（run `run_01M45V1M46K8KAEK81MVEY0MCV`）
- **p-k04-b** [clarify, S9, para]
  - ✗ clarify：期望提问，实际未提问（run `run_01M45V1M4AF3RJN4ZB5Q9XK4D0`）
- **p-k06-a** [clarify, S9, para]
  - ✗ clarify：期望提问，实际未提问（run `run_01M45V1M57VVQ8NYWZC3BAKBS9`）
- **p-k06-b** [clarify, S9, para]
  - ✗ clarify：期望提问，实际未提问（run `run_01M45V1M8DZHBA4YM5A0A4Q13N`）
- **p-k08-a** [clarify, S9, para]
  - ✗ clarify：期望提问，实际未提问（run `run_01M45V1MBGW9M45QJAJCCGJ9WQ`）
- **p-k08-b** [clarify, S9, para]
  - ✗ clarify：期望提问，实际未提问（run `run_01M45V1MBYQDV0T9PPD2Y2CGFN`）
- **p-l02-a** [S9, offtopic, para]
  - ✗ route：期望 offtopic，实际 generate（run `run_01M45V1MC18ACJV8EYND0JQJD9`）
- **p-l10-a** [S9, offtopic, para]
  - ✗ route：期望 offtopic，实际 generate（run `run_01M45V1MKDRPH8SSV2V8NNS9A5`）
- **p-l12-a** [S9, offtopic, para]
  - ✗ route：期望 offtopic，实际 generate（run `run_01M45V1MKRX5XER6F9MNRA5TTY`）
- **p-l12-b** [S9, offtopic, para]
  - ✗ route：期望 offtopic，实际 generate（run `run_01M45V1MKRZD5N9KRNCKJJAJH2`）

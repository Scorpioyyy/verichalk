# 评测报告：understand_para / val

- 时间：2026-10-05 18:51:25（用时 5.1s）　提交：`2ad3142+dirty`　profile：`intl`　模式：`replay`
- 模型角色：fast=deepseek-v4.1-flash，smart=qwen3.8-max，vision=qwen3.8-flash，judge=deepseek-v4.1-flash，solver=deepseek-v4.1-flash
- 特性开关：关闭：understand.llm

## 总览

| 指标 | 值 |
|---|---|
| 用例数 / 运行数 | 85 / 85 |
| F1 运行成功率 | 1.000（85/85，95% CI 0.957–1.000） |
| F6 trace 完整度 | 1.000（85/85，95% CI 0.957–1.000） |
| D3 端到端时长 | p50 166ms / p95 2057ms（n=85） |
| D1 首反馈（服务端） | — |
| E2 KV 缓存命中率 | — |
| E1 单次运行成本 | p50 0.0000 / p95 0.0000 |
| E3 每次运行的模型调用数 | 均值 0.0 |
| F2 模型错误率 | — |
| E5 结构化输出重试率 | — |

## 检查项通过率

| 检查 | 通过率 |
|---|---|
| absent:constraints | 0.970（64/66，95% CI 0.896–0.992） |
| absent:count | 1.000（14/14，95% CI 0.785–1.000） |
| absent:difficulty | 1.000（61/61，95% CI 0.941–1.000） |
| absent:kinds | 1.000（57/57，95% CI 0.937–1.000） |
| absent:scenes | 1.000（64/64，95% CI 0.943–1.000） |
| absent:source | 1.000（68/68，95% CI 0.947–1.000） |
| absent:tier | 1.000（55/55，95% CI 0.935–1.000） |
| absent:topics | 0.308（4/13，95% CI 0.127–0.576） |
| clarify | 0.918（78/85，95% CI 0.840–0.960） |
| field:action | 0.167（1/6，95% CI 0.030–0.564） |
| field:constraints_any | 0.000（0/3，95% CI 0.000–0.562） |
| field:count | 0.983（57/58，95% CI 0.909–0.997） |
| field:difficulty | 0.750（6/8，95% CI 0.409–0.929） |
| field:grade | 1.000（69/69，95% CI 0.947–1.000） |
| field:kinds | 1.000（12/12，95% CI 0.757–1.000） |
| field:paper | 0.750（3/4，95% CI 0.301–0.954） |
| field:scenes_any | 1.000（5/5，95% CI 0.566–1.000） |
| field:semester | 1.000（33/33，95% CI 0.896–1.000） |
| field:source | 0.000（0/1，95% CI 0.000–0.793） |
| field:tier | 0.583（7/12，95% CI 0.320–0.807） |
| field:topics_any | 0.981（53/54，95% CI 0.902–0.997） |
| field:unit | 1.000（4/4，95% CI 0.510–1.000） |
| no_secrets | 1.000（85/85，95% CI 0.957–1.000） |
| origin:action | 0.833（5/6，95% CI 0.436–0.970） |
| origin:count | 0.983（57/58，95% CI 0.909–0.997） |
| origin:difficulty | 0.750（6/8，95% CI 0.409–0.929） |
| origin:grade | 1.000（67/67，95% CI 0.946–1.000） |
| origin:kinds | 1.000（12/12，95% CI 0.757–1.000） |
| origin:semester | 1.000（33/33，95% CI 0.896–1.000） |
| origin:source | 0.000（0/1，95% CI 0.000–0.793） |
| origin:tier | 0.583（7/12，95% CI 0.320–0.807） |
| origin:unit | 1.000（4/4，95% CI 0.510–1.000） |
| route | 0.906（77/85，95% CI 0.825–0.952） |
| status | 1.000（85/85，95% CI 0.957–1.000） |
| trace_complete | 1.000（85/85，95% CI 0.957–1.000） |

## 按标签切片（用例整体通过率）

| 标签 | 通过率 |
|---|---|
| S1 | 0.738（31/42，95% CI 0.589–0.847） |
| S2 | 0.700（7/10，95% CI 0.397–0.892） |
| S4 | 0.200（2/10，95% CI 0.057–0.510） |
| S5 | 0.286（2/7，95% CI 0.082–0.641） |
| S9 | 0.500（8/16，95% CI 0.280–0.720） |
| clarify | 0.125（1/8，95% CI 0.022–0.471） |
| g1 | 1.000（2/2，95% CI 0.342–1.000） |
| g2 | 1.000（1/1，95% CI 0.207–1.000） |
| g3 | 0.500（2/4，95% CI 0.150–0.850） |
| g4 | 1.000（6/6，95% CI 0.610–1.000） |
| g5 | 1.000（6/6，95% CI 0.610–1.000） |
| gg | 1.000（6/6，95% CI 0.610–1.000） |
| grade-only | 0.429（3/7，95% CI 0.158–0.750） |
| na | 0.818（9/11，95% CI 0.523–0.949） |
| offtopic | 0.875（7/8，95% CI 0.529–0.978） |
| paper | 0.200（2/10，95% CI 0.057–0.510） |
| para | 0.588（50/85，95% CI 0.482–0.687） |
| review | 0.286（2/7，95% CI 0.082–0.641） |
| scene | 0.778（7/9，95% CI 0.453–0.937） |
| sp | 1.000（2/2，95% CI 0.342–1.000） |
| template | 0.000（0/1，95% CI 0.000–0.793） |
| topic-only | 1.000（2/2，95% CI 0.342–1.000） |
| variants | 0.643（9/14，95% CI 0.388–0.837） |

## 意图理解（B 组）

| 指标 | 值 | 验收线 |
|---|---|---|
| **B1 字段通过率** | 0.929（250/269，95% CI 0.892–0.954） | ≥ 0.90 |
| B1-origin（明说的字段来源为 user） | 0.950（191/201，95% CI 0.911–0.973） | ≥ 0.95 |
| B1-幻觉率（没说却断言） | 0.028（11/398，95% CI 0.016–0.049） | ≤ 0.03 |
| **B2 澄清精确率** | 1.000（1/1，95% CI 0.207–1.000） | ≥ 0.85 |
| **B2 澄清召回** | 0.125（1/8，95% CI 0.022–0.471） | ≥ 0.80 |
| B2 提问占比 | 0.012（1/85，95% CI 0.002–0.064） | ≤ 0.15 |
| **B3 路由准确率** | 0.906（77/85，95% CI 0.825–0.952） | ≥ 0.95 |
| 理解阶段延迟 | p50 90ms / p95 1965ms（n=85） | p50 ≤ 2500ms |
| 解析方式 | {'rules': 85} | |

### B1 按字段

| 字段 | 通过率 |
|---|---|
| action | 0.167（1/6，95% CI 0.030–0.564） |
| constraints_any | 0.000（0/3，95% CI 0.000–0.562） |
| count | 0.983（57/58，95% CI 0.909–0.997） |
| difficulty | 0.750（6/8，95% CI 0.409–0.929） |
| grade | 1.000（69/69，95% CI 0.947–1.000） |
| kinds | 1.000（12/12，95% CI 0.757–1.000） |
| paper | 0.750（3/4，95% CI 0.301–0.954） |
| scenes_any | 1.000（5/5，95% CI 0.566–1.000） |
| semester | 1.000（33/33，95% CI 0.896–1.000） |
| source | 0.000（0/1，95% CI 0.000–0.793） |
| tier | 0.583（7/12，95% CI 0.320–0.807） |
| topics_any | 0.981（53/54，95% CI 0.902–0.997） |
| unit | 1.000（4/4，95% CI 0.510–1.000） |

### B3 路由混淆（仅错误项）

- 期望 `generate` → 实际 `paper`：4
- 期望 `paper` → 实际 `generate`：3
- 期望 `offtopic` → 实际 `generate`：1

### 失败类别计数（前 15）

| 检查项 | 失败数 |
|---|---|
| absent:topics | 9 |
| route | 8 |
| clarify | 7 |
| field:tier | 5 |
| origin:tier | 5 |
| field:action | 5 |
| field:constraints_any | 3 |
| absent:constraints | 2 |
| field:difficulty | 2 |
| origin:difficulty | 2 |
| field:topics_any | 1 |
| field:count | 1 |
| origin:count | 1 |
| field:source | 1 |
| origin:source | 1 |

## 失败样本（35）

- **p-a17-a** [S1, g3, na, para]
  - ✗ absent:constraints：用户没说，却断言了具体值（run `run_01M45V18Z37P675ZSVP32MCW49`）
- **p-a17-b** [S1, g3, na, para]
  - ✗ absent:constraints：用户没说，却断言了具体值（run `run_01M45V193FDS7N8GXJVZT81GGG`）
- **p-c01-b** [S1, grade-only, para]
  - ✗ absent:topics：用户没说，却断言了具体值（run `run_01M45V1985RF4CMR6FVVJZ7Z5D`）
- **p-c03-a** [S1, grade-only, para]
  - ✗ absent:topics：用户没说，却断言了具体值（run `run_01M45V199286HVBR7WAPV80Z3B`）
- **p-c03-b** [S1, grade-only, para]
  - ✗ absent:topics：用户没说，却断言了具体值（run `run_01M45V199KARFV9CPEVTZ3VGZB`）
- **p-c05-b** [S1, grade-only, para]
  - ✗ absent:topics：用户没说，却断言了具体值（run `run_01M45V19GMBBGW6FNDP4CMAARA`）
- **p-d01-a** [S1, variants, para]
  - ✗ field:difficulty：未解析出难度（run `run_01M45V19JMRHQGQ7PFBCZASS1A`）
  - ✗ origin:difficulty：来源应为 user，实际 default（run `run_01M45V19JMRHQGQ7PFBCZASS1A`）
- **p-d05-b** [S1, variants, para]
  - ✗ field:topics_any：期望知识点含 ['圆的面积']，实际映射 ['长方形和正方形的面积', '面积单位的认识与换算', '长方体和正方体的表面积']（run `run_01M45V19TWEWF165G4Y8DM4WDG`）
- **p-d13-b** [S1, variants, para]
  - ✗ field:constraints_any：期望含 ['数字不要太大']，实际 ''（run `run_01M45V19WVMJPHPJVQWEA1MVZ6`）
- **p-d15-a** [S1, variants, para]
  - ✗ field:constraints_any：期望含 ['除数']，实际 ''（run `run_01M45V19X7ZY3B747DF94J6WPP`）
- **p-d15-b** [S1, variants, para]
  - ✗ field:constraints_any：期望含 ['除数']，实际 ''（run `run_01M45V1A0NB1J9YZJZ6G0W6K6Q`）
- **p-e03-b** [S2, scene, para]
  - ✗ field:count：期望 2，实际 5（run `run_01M45V1A3SNZDM26EZRFNFGK9J`）
  - ✗ origin:count：来源应为 user，实际 default（run `run_01M45V1A3SNZDM26EZRFNFGK9J`）
- **p-e05-a** [S2, scene, para]
  - ✗ field:tier：期望 integrated，实际 None（run `run_01M45V1A526F59NYFQNGMNGXS0`）
  - ✗ origin:tier：来源应为 user，实际 default（run `run_01M45V1A526F59NYFQNGMNGXS0`）
- **p-e11-a** [S2, template, para]
  - ✗ field:source：期望 template，实际 auto（run `run_01M45V1ACPPB2SRT4MBQ20K980`）
  - ✗ origin:source：来源应为 user，实际 default（run `run_01M45V1ACPPB2SRT4MBQ20K980`）
- **p-g01-b** [S4, paper, para]
  - ✗ absent:topics：用户没说，却断言了具体值（run `run_01M45V1AE7WF847SEFD4SYQ42H`）
- **p-g03-a** [S4, paper, para]
  - ✗ absent:topics：用户没说，却断言了具体值（run `run_01M45V1AEF8WPEP142Y1R38ZKY`）
- **p-g03-b** [S4, paper, para]
  - ✗ field:paper：期望 {'duration_min': 90, 'total_score': 100}，实际 {'duration_min': 90, 'total_score': None, 'structure': []}（run `run_01M45V1AFZP1K40FW2JXCHMQBC`）
  - ✗ absent:topics：用户没说，却断言了具体值（run `run_01M45V1AFZP1K40FW2JXCHMQBC`）
- **p-g05-a** [S4, paper, para]
  - ✗ route：期望 paper，实际 generate（run `run_01M45V1AGWN8S1K6737BA9VYJW`）
- **p-g05-b** [S4, paper, para]
  - ✗ route：期望 paper，实际 generate（run `run_01M45V1AM6FZ6J3F0D6DT5J0SM`）
- **p-g07-a** [S4, paper, para]
  - ✗ field:difficulty：未解析出难度（run `run_01M45V1APJME5MYWV45RW7MA51`）
  - ✗ origin:difficulty：来源应为 user，实际 default（run `run_01M45V1APJME5MYWV45RW7MA51`）
  - ✗ absent:topics：用户没说，却断言了具体值（run `run_01M45V1APJME5MYWV45RW7MA51`）
- **p-g07-b** [S4, paper, para]
  - ✗ absent:topics：用户没说，却断言了具体值（run `run_01M45V1APTJEMR0QXZVKNP7QDX`）
- **p-g09-a** [S4, paper, para]
  - ✗ route：期望 paper，实际 generate（run `run_01M45V1AQHGSCHH1YH457FV1V0`）
- **p-h01-a** [S5, review, para]
  - ✗ route：期望 generate，实际 paper（run `run_01M45V1ARE51PS3RM2C68M1V40`）
  - ✗ field:action：期望 review，实际 paper（run `run_01M45V1ARE51PS3RM2C68M1V40`）
  - ✗ field:tier：期望 integrated，实际 None（run `run_01M45V1ARE51PS3RM2C68M1V40`）
  - ✗ origin:tier：来源应为 user，实际 default（run `run_01M45V1ARE51PS3RM2C68M1V40`）
- **p-h01-b** [S5, review, para]
  - ✗ route：期望 generate，实际 paper（run `run_01M45V1AT1FAVCMXQ9DHQTJ4QN`）
  - ✗ field:action：期望 review，实际 paper（run `run_01M45V1AT1FAVCMXQ9DHQTJ4QN`）
  - ✗ field:tier：期望 integrated，实际 None（run `run_01M45V1AT1FAVCMXQ9DHQTJ4QN`）
  - ✗ origin:tier：来源应为 user，实际 default（run `run_01M45V1AT1FAVCMXQ9DHQTJ4QN`）
- **p-h03-a** [S5, review, para]
  - ✗ field:action：期望 review，实际 generate（run `run_01M45V1AWB7G8RYYB3NYDWGDHB`）
  - ✗ origin:action：来源应为 user，实际 default（run `run_01M45V1AWB7G8RYYB3NYDWGDHB`）
- **p-h05-a** [S5, review, para]
  - ✗ route：期望 generate，实际 paper（run `run_01M45V1B0KHGN00QC18G8R8Q1A`）
  - ✗ field:action：期望 review，实际 paper（run `run_01M45V1B0KHGN00QC18G8R8Q1A`）
  - ✗ field:tier：期望 integrated，实际 None（run `run_01M45V1B0KHGN00QC18G8R8Q1A`）
  - ✗ origin:tier：来源应为 user，实际 default（run `run_01M45V1B0KHGN00QC18G8R8Q1A`）
- **p-h05-b** [S5, review, para]
  - ✗ route：期望 generate，实际 paper（run `run_01M45V1B0PBM06Y8PCJ75EMN66`）
  - ✗ field:action：期望 review，实际 paper（run `run_01M45V1B0PBM06Y8PCJ75EMN66`）
  - ✗ field:tier：期望 integrated，实际 None（run `run_01M45V1B0PBM06Y8PCJ75EMN66`）
  - ✗ origin:tier：来源应为 user，实际 default（run `run_01M45V1B0PBM06Y8PCJ75EMN66`）
- **p-k01-a** [clarify, S9, para]
  - ✗ clarify：期望提问，实际未提问（run `run_01M45V1B1ZMTQJBYTCAGBV59SZ`）
- **p-k01-b** [clarify, S9, para]
  - ✗ clarify：期望提问，实际未提问（run `run_01M45V1B2FKSJX8RQXHNYSXVEZ`）
- **p-k03-a** [clarify, S9, para]
  - ✗ clarify：期望提问，实际未提问（run `run_01M45V1B41CWDM67HR8T5Y1WK5`）
- **p-k03-b** [clarify, S9, para]
  - ✗ clarify：期望提问，实际未提问（run `run_01M45V1B6MGX0Q6NJVWBRRGMMH`）
- **p-k05-b** [clarify, S9, para]
  - ✗ clarify：期望提问，实际未提问（run `run_01M45V1B9V5Y4QC9DKMJSPK2E4`）
- **p-k07-a** [clarify, S9, para]
  - ✗ clarify：期望提问，实际未提问（run `run_01M45V1BA1AS16XZH5BH5TAAY4`）
- **p-k07-b** [clarify, S9, para]
  - ✗ clarify：期望提问，实际未提问（run `run_01M45V1BA1DTZCHRVTG1WAHYHC`）
- **p-l11-a** [S9, offtopic, para]
  - ✗ route：期望 offtopic，实际 generate（run `run_01M45V1BH0K32DRJC3ZZXWQHZZ`）

### solver_dsflash_nothink · checks=blind · split=val · solver=deepseek-v4.1-flash

| 指标 | 值 |
|---|---|
| V1 错答检出率 | 0.933 (111/119) [0.87,0.97] |
| V2 正确题误杀率 | 0.043 (3/70) [0.01,0.12] |
| V5 缺陷题检出率 | 0.909 (10/11) [0.62,0.98] |
| 单题成本（元） | 0.0004 |
| 单题 token（输入 / 输出） | 294 / 78 |
| 延迟 p50 / p95 | 1.8s / 3.5s |

按注入 / 破坏类型的检出：absurd 3/3；ambiguous 1/2；contradict 4/4；digit 11/12；digit+solution 11/11；flip 11/11；missing 2/2；off1 20/22；off1+solution 8/9；op 16/17；op+solution 10/12；shift 17/18；shift+solution 7/7

错答由哪项检查抓住（可重叠）：blind 111

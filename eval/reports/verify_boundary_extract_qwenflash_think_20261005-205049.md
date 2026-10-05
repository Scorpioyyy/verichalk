### extract_qwenflash_think · split=val · extract=qwen3.8-flash:think

| 指标 | 值 |
|---|---|
| V3 超纲检出率（判 out） | 0.893 (25/28) [0.73,0.96] |
| V3' 超纲检出率（out 或 borderline） | 0.893 (25/28) [0.73,0.96] |
| V4 范围内误杀率（判 out） | 0.143 (4/28) [0.06,0.31] |
| V4' 范围内被标 out / borderline | 0.143 (4/28) [0.06,0.31] |
| 抽取失败（skip） | 0 |
| 单题成本（元） | 0.0066 |
| 延迟 p50 / p95 | 16.1s / 167.2s |

越界题按维度的检出（判 out）：concept 5/6；decimal_places 3/4；fraction_types 3/3；integer_domain 12/13；operation_forms 2/2

越界题按来源的检出：authored 13/14；template 12/14

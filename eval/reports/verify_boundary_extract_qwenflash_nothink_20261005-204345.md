### extract_qwenflash_nothink · split=val · extract=qwen3.8-flash

| 指标 | 值 |
|---|---|
| V3 超纲检出率（判 out） | 0.929 (26/28) [0.77,0.98] |
| V3' 超纲检出率（out 或 borderline） | 0.929 (26/28) [0.77,0.98] |
| V4 范围内误杀率（判 out） | 0.179 (5/28) [0.08,0.36] |
| V4' 范围内被标 out / borderline | 0.179 (5/28) [0.08,0.36] |
| 抽取失败（skip） | 0 |
| 单题成本（元） | 0.0015 |
| 延迟 p50 / p95 | 2.8s / 4.0s |

越界题按维度的检出（判 out）：concept 6/6；decimal_places 4/4；fraction_types 3/3；integer_domain 11/13；operation_forms 2/2

越界题按来源的检出：authored 13/14；template 13/14

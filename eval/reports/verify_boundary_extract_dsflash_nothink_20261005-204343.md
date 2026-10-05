### extract_dsflash_nothink · split=val · extract=deepseek-v4.1-flash

| 指标 | 值 |
|---|---|
| V3 超纲检出率（判 out） | 0.964 (27/28) [0.82,0.99] |
| V3' 超纲检出率（out 或 borderline） | 0.964 (27/28) [0.82,0.99] |
| V4 范围内误杀率（判 out） | 0.179 (5/28) [0.08,0.36] |
| V4' 范围内被标 out / borderline | 0.214 (6/28) [0.10,0.40] |
| 抽取失败（skip） | 0 |
| 单题成本（元） | 0.0015 |
| 延迟 p50 / p95 | 2.4s / 3.9s |

越界题按维度的检出（判 out）：concept 6/6；decimal_places 4/4；fraction_types 3/3；integer_domain 13/13；operation_forms 1/2

越界题按来源的检出：authored 13/14；template 14/14

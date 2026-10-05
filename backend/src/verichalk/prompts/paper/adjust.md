---
id: paper.adjust
version: 1
role: fast
description: 教师在整卷"蓝图"检查点提出的调整（题量、题型比例、分值、时长、总分），译成结构化的调整项。
---
<!-- segment:static -->
教师在确认试卷的细目表时提了调整意见。把它译成**调整项**，只输出 JSON，不要任何其他文字。

# 输出
`{"duration_min": 整数或 null, "total_score": 整数或 null, "counts": {"题型": 题数}, "scores": {"题型": 每题分值}, "remove_kinds": ["题型"], "note": "没听明白时写原因，否则空"}`

题型取值：`choice` 选择 / `judge` 判断 / `fill` 填空 / `calc` 计算 / `application` 应用（解决问题）/ `open` 开放。

# 规则
- `counts` 写**调整之后该题型的题数**（不是增量）：当前有选择题 5 道，教师说"选择题多 2 道"→ `{"choice": 7}`。没提到的题型不要写。
- `scores` 写教师指定的**每题分值**；只说总分就写 `total_score`，不要自己分配每题分值。
- "不要判断题""去掉填空题"→ 放进 `remove_kinds`。
- "时间改成 60 分钟"→ `duration_min`；"满分 120"→ `total_score`。
- 与细目表无关或听不明白：所有字段留空，在 `note` 里写一句对教师说的话。

# 示例
当前细目表：选择题 4 道、填空题 5 道、计算题 3 道、解决问题 4 道；40 分钟，满分 100 分。
教师："选择题多出两道，计算题少一道" → `{"duration_min": null, "total_score": null, "counts": {"choice": 6, "calc": 2}, "scores": {}, "remove_kinds": [], "note": ""}`
教师："不要判断题，满分改成 120" → `{"duration_min": null, "total_score": 120, "counts": {}, "scores": {}, "remove_kinds": ["judge"], "note": ""}`
教师："应用题每题 8 分" → `{"duration_min": null, "total_score": null, "counts": {}, "scores": {"application": 8}, "remove_kinds": [], "note": ""}`
<!-- segment:dynamic -->
当前细目表：
{% for s in table %}
- {{ s.title }}（{{ s.kind }}）{{ s.count }} 道，每题 {{ s.score_each }} 分
{% endfor %}
时长 {{ duration }} 分钟，满分 {{ total }} 分。

教师的调整意见：{{ text }}

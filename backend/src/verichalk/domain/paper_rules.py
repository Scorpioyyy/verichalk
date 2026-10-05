"""试卷的确定性规则：分值分配、"追加还是替换"的判断。纯函数，不依赖模型。"""

from __future__ import annotations

import re
from typing import Literal

from .paper import ItemKind

# 各题型的相对分值权重（同一份卷子里，应用题的分值比选择题高）
KIND_WEIGHT: dict[ItemKind, int] = {
    ItemKind.choice: 2,
    ItemKind.judge: 2,
    ItemKind.fill: 3,
    ItemKind.calc: 4,
    ItemKind.application: 6,
    ItemKind.open: 8,
}


def default_score(kind: ItemKind) -> float:
    """在已经有分值的试卷上追加题目时，新题用的默认分值。"""
    return float(KIND_WEIGHT[kind])


def distribute_scores(kinds: list[ItemKind], total: int) -> list[int]:
    """把总分按题型权重分给每道题：同题型同分，合计**恰好**等于 `total`。

    先按权重比例向下取整到整数分（至少 1 分），余数从权重最高的题型的最后几道题开始每题补 1 分。
    总分太小（< 题数）时无法每题 ≥ 1 分，抛 ValueError。"""
    n = len(kinds)
    if n == 0:
        return []
    if total < n:
        raise ValueError(f"总分 {total} 小于题数 {n}，无法每题至少 1 分")
    wsum = sum(KIND_WEIGHT[k] for k in kinds)
    scores = [max(1, total * KIND_WEIGHT[k] // wsum) for k in kinds]
    diff = total - sum(scores)
    order = sorted(range(n), key=lambda i: (-KIND_WEIGHT[kinds[i]], -i))  # 权重高的题型、靠后的题优先
    step = 1 if diff > 0 else -1
    j = 0
    while diff != 0:
        i = order[j % n]
        if step < 0 and scores[i] <= 1:
            j += 1
            continue
        scores[i] += step
        diff -= step
        j += 1
    return scores


Placement = Literal["new", "append", "replace"]
_REPLACE = re.compile(
    r"重新(?:出|生成|来)|重出|重来|换一批|换一套|换一份|全部(?:换|重)|整套(?:换|重)|都换掉|推倒重来"
)


def placement(text: str, has_paper: bool) -> Placement:
    """新题进入试卷的方式：没有试卷则新建；默认**追加**（不丢教师已有的东西，替换才需要明说）。"""
    if not has_paper:
        return "new"
    return "replace" if _REPLACE.search(text) else "append"

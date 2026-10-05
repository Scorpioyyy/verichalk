"""构建超纲核验评测集 `BoundaryBank`（eval/specs/produce.md §2.2）。

组成：
1. 手写的成对题（`eval/annotation/boundary/items.yaml`）：概念 / 运算形态 / 小数位数维度。构建时把 `culprit`（越界内容的知识点规范名）
   解析成知识点 ID，并**断言**该知识点的首次引入课时晚于目标课时且不在同一单元（防止标注本身出错）；
2. 数值维度：同一教材题型实例化，在较晚的课时是范围内（标签取 chalkbase 对参数特征的确定性判定 `in`），
   在更早的课时是越界（`out`，且越界维度是整数数域 / 小数位数）。

用法：python scripts/build_boundary_bank.py
产物：eval/datasets/verify/boundary.yaml
"""

from __future__ import annotations

import hashlib
import random
import sys
import warnings
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend" / "src"))

KIND = {"compute": "calc", "fill_blank": "fill", "word_problem": "application"}


def split_of(key: str) -> str:
    return "val" if int(hashlib.sha1(key.encode()).hexdigest(), 16) % 2 == 0 else "test"


def resolve(cur: Any, name: str) -> Any:
    hits = cur.find_kp(name)
    if not hits:
        raise SystemExit(f"找不到知识点：{name}")
    return hits[0]


def authored(cur: Any) -> list[dict[str, Any]]:
    raw = yaml.safe_load((ROOT / "eval/annotation/boundary/items.yaml").read_text(encoding="utf-8"))
    out: list[dict[str, Any]] = []
    for i, it in enumerate(raw):
        lesson = it["lesson_id"]
        row = {
            "id": f"bb-a{i:03d}",
            "split": split_of(it["pair"]),
            "source": "authored",
            "pair": it["pair"],
            "lesson_id": lesson,
            "grade": cur.lesson_location(lesson).grade,
            "label": it["label"],
            "dimension": it["dimension"],
            "culprit": None,
            "stem": it["stem"],
            "solution": it["solution"],
        }
        if it["culprit"]:
            kp = resolve(cur, it["culprit"])
            loc = cur.locate(kp.id)
            if cur.lesson_position(loc.lesson_id) <= cur.lesson_position(lesson):
                raise SystemExit(f"{it['pair']}：{it['culprit']} 在 {loc.lesson_id} 引入，不晚于目标课时 {lesson}")
            if cur.lesson_location(loc.lesson_id).unit_id == cur.lesson_location(lesson).unit_id:
                raise SystemExit(f"{it['pair']}：{it['culprit']} 与目标课时在同一单元（borderline 灰区）")
            row["culprit"] = kp.id
        out.append(row)
    return out


def numeric(cur: Any, n: int, seed: int) -> list[dict[str, Any]]:
    """同一题型：较晚课时 in、较早课时 out（只取整数数域 / 小数位数维度的越界）。"""
    rng = random.Random(seed)
    ats = [a for a in cur.archetypes(verifiable_type="program") if getattr(a.item_form, "value", a.item_form) in KIND]
    rng.shuffle(ats)
    rows: list[dict[str, Any]] = []
    all_lessons = list(cur.lesson_ids())
    for a in ats:
        if len(rows) >= n:
            break
        try:
            lessons = cur.archetype_lessons(a.id)
            if not lessons:
                continue
            late = lessons[-1]
            late_pos = cur.lesson_position(late)
            # 较早课时：向前约 2 册（教学序列里约 40 课时）
            early_cands = [x for x in all_lessons if cur.lesson_position(x) <= late_pos - 70]
            if not early_cands:
                continue
            early = early_cands[-1]
            seed_i = rng.randint(0, 40)
            p_in = cur.instantiate(a.id, seed=seed_i, lesson_id=late)
            p_out = cur.instantiate(a.id, seed=seed_i, lesson_id=early)
        except Exception:
            continue
        form = getattr(a.item_form, "value", a.item_form)
        bad = ("如图", "下图", "表格", "|", "______ )", "画", "量一量", "判断")
        if p_in.warnings or any(w in p_in.problem for w in bad) or len(p_in.problem) > 150:
            continue
        dims = set(p_out.violated_dimensions or [])
        if p_in.verdict != "in" or p_out.verdict != "out" or not dims & {"integer_domain", "decimal_places"}:
            continue
        key = f"num{len(rows) // 2}"
        for lab, p, lesson in (("in", p_in, late), ("out", p_out, early)):
            rows.append(
                {
                    "id": f"bb-n{len(rows):03d}",
                    "split": split_of(key),
                    "source": "template",
                    "pair": key,
                    "lesson_id": lesson,
                    "grade": cur.lesson_location(lesson).grade,
                    "label": lab,
                    "dimension": sorted(dims & {"integer_domain", "decimal_places"})[0] if lab == "out" else "numeric",
                    "culprit": None,
                    "stem": p.problem.strip(),
                    "solution": "",
                    "kind": KIND[form],
                }
            )
    return rows


def main() -> None:
    warnings.simplefilter("ignore")
    from chalkbase import Curriculum

    cur = Curriculum()
    rows = authored(cur) + numeric(cur, 70, 11)
    head = (
        "# 超纲核验评测集：由 scripts/build_boundary_bank.py 生成（见 eval/specs/produce.md §2.2）。\n"
        "# label: in（在 lesson_id 之前学过的范围内）/ out（含之后才学的内容）。\n"
    )
    path = ROOT / "eval" / "datasets" / "verify" / "boundary.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    yaml.SafeDumper.ignore_aliases = lambda self, data: True  # type: ignore[method-assign]
    with path.open("w", encoding="utf-8") as f:
        f.write(head)
        yaml.safe_dump(rows, f, allow_unicode=True, sort_keys=False, default_flow_style=None, width=140)
    from collections import Counter

    print(Counter((r["source"], r["label"]) for r in rows), Counter(r["split"] for r in rows))
    print(f"写入 {path}（{len(rows)} 项）")


if __name__ == "__main__":
    main()

"""拍照感知评测（eval/specs/perceive.md）：B4 转写、B5 知识点、B6 难度、P3～P7、D-photo、负例拒识（P5）与误拒（P6）。

只评 `perceive` 阶段（视觉模型 + 确定性后处理 + 知识点检索）。标注与图片都不入库（教辅版权）。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..core.config import Settings
from ..core.ids import new_id
from ..domain.events import LLMCall
from ..domain.perception import PageRead, PageVerdict, PerceivedItem, ReferenceSet
from ..domain.run import Attachment
from ..orchestrator import build_container
from ..stages import RunContext, run_stage
from ..stages.perceive import PerceiveIn, PerceiveStage
from ..store import Store
from ..trace import MemorySink, Tracer, use_tracer

MATCH_CER = 0.5  # 配对题的字符错误率上限：超过它就当作"没找到这道题"
FUZZY_CER = 0.15

# ---- 归一化 ----
_PUNCT = str.maketrans(
    {
        "。": ".",
        "、": ",",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "−": "-",
        "–": "-",
        "—": "-",
        "﹣": "-",
        "∶": ":",
        "＊": "×",
        "*": "×",
        "✕": "×",
        "◯": "○",
        "〇": "○",
        "□": "□",
        "▢": "□",
        "⬜": "□",
        "☐": "□",
    }
)
_BLANK_PAREN = re.compile(r"\(\s*[?？]?\s*\)")
_SUBLABEL = re.compile(r"^\(([1-9])\)(?=[∠\u4e00-\u9fff])")
_DIGITS = re.compile(r"\d+(?:\.\d+)?")


def normalize(text: str) -> str:
    s = unicodedata.normalize("NFKC", text).translate(_PUNCT)
    s = re.sub(r"\s+", "", s)
    s = _BLANK_PAREN.sub("()", s)
    s = re.sub(r"_+", "", s)  # 书写横线不影响题意：两侧都去掉
    s = s.replace("=()", "()")  # "x ( )位" 与 "x=( )位" 等价
    s = re.sub(r"\\frac\{(\d+)\}\{(\d+)\}", r"\1/\2", s)
    s = re.sub(r"[,;:\"']", "", s)  # 分隔符与引号不影响题意：两侧都去掉
    s = _SUBLABEL.sub("", s)
    s = s.replace("$", "")  # 行内公式标记不影响内容
    s = s.rstrip("=.?!")
    s = s.removesuffix("()")  # 末尾的空括号是书写空位，不是题面内容
    return s


def edit_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a or not b:
        return max(len(a), len(b))
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(gold: str, pred: str) -> float:
    return edit_distance(gold, pred) / max(len(gold), 1)


# ---- 标注 ----
@dataclass
class GoldItem:
    text: str  # 归一化
    raw: str
    question: int
    has_figure: bool
    difficulty: int | None
    kp: set[str]
    kind: str


@dataclass
class GoldPage:
    page: str
    split: str
    title: str
    kp: set[str]
    items: list[GoldItem]
    notes: str = ""


def load_gold(root: Path) -> dict[str, GoldPage]:
    out: dict[str, GoldPage] = {}
    for f in sorted((root / "eval" / "annotation" / "photos").glob("[pf]*.yaml")):
        d = yaml.safe_load(f.read_text(encoding="utf-8"))
        page_kp = set(d.get("kp") or [])
        items: list[GoldItem] = []
        for qi, q in enumerate(d["questions"]):
            for t in q["items"]:
                items.append(
                    GoldItem(
                        text=normalize(t),
                        raw=t,
                        question=qi,
                        has_figure=bool(q.get("has_figure")),
                        difficulty=q.get("difficulty"),
                        kp=set(q.get("kp") or page_kp),
                        kind=str(q.get("kind", "")),
                    )
                )
        out[d["page"]] = GoldPage(
            d["page"], d.get("split", "val"), d.get("title", ""), page_kp, items, d.get("notes", "")
        )
    return out


@dataclass
class PhotoCase:
    id: str  # 如 p008、p008~rot、neg_blank
    page: str  # 对应的标注页（负例为空）
    path: Path
    group: str  # raw | variant | negative
    split: str


def load_photo_cases(root: Path, gold: dict[str, GoldPage], groups: set[str], split: str) -> list[PhotoCase]:
    base = root / "eval" / "datasets" / "photos"
    cases: list[PhotoCase] = []
    if "raw" in groups:
        for p in sorted((base / "raw").glob("p*.jpg")):
            g = gold.get(p.stem)
            if g and split in ("all", g.split):
                cases.append(PhotoCase(p.stem, p.stem, p, "raw", g.split))
    if "variant" in groups:
        for p in sorted((base / "variants").glob("p*.jpg")):
            page = p.stem.split("~")[0]
            g = gold.get(page)
            if g and split in ("all", g.split):
                cases.append(PhotoCase(p.stem, page, p, "variant", g.split))
    if "fresh" in groups:  # 验收集：用户新拍的、从未用于调优的页
        for p in sorted((base / "fresh").glob("f*.jpg")):
            g = gold.get(p.stem)
            if g:
                cases.append(PhotoCase(p.stem, p.stem, p, "fresh", g.split))
    if "negative" in groups:
        for p in sorted((base / "negatives").glob("*.jpg")):
            cases.append(PhotoCase(p.stem, "", p, "negative", "all"))
    return cases


# ---- 配对与打分 ----
@dataclass
class Pair:
    gold: GoldItem
    pred: PerceivedItem
    cer: float

    @property
    def exact(self) -> bool:
        return self.cer == 0.0

    @property
    def digit_error(self) -> bool:
        return _DIGITS.findall(self.gold.text) != _DIGITS.findall(normalize(self.pred.text))


def match(gold: list[GoldItem], pred: list[PerceivedItem]) -> tuple[list[Pair], list[PerceivedItem]]:
    """一对一配对：先按字符错误率从小到大贪心取，错误率超过 MATCH_CER 的不配。"""
    cands: list[tuple[float, int, int]] = []
    pn = [normalize(p.text) for p in pred]
    for i, g in enumerate(gold):
        for j, p in enumerate(pn):
            if abs(len(g.text) - len(p)) > max(len(g.text), len(p)) * MATCH_CER:
                continue
            c = cer(g.text, p)
            if c <= MATCH_CER:
                cands.append((c, i, j))
    cands.sort()
    used_g: set[int] = set()
    used_p: set[int] = set()
    pairs: list[Pair] = []
    for c, i, j in cands:
        if i in used_g or j in used_p:
            continue
        used_g.add(i)
        used_p.add(j)
        pairs.append(Pair(gold[i], pred[j], c))
    extra = [p for j, p in enumerate(pred) if j not in used_p]
    return pairs, extra


@dataclass
class PageScore:
    case: str
    group: str
    page: str
    usable: bool
    verdict: str
    n_gold: int = 0
    n_pred: int = 0
    exact: int = 0
    fuzzy: int = 0
    found: int = 0
    edits: int = 0
    gold_chars: int = 0
    digit_err: int = 0
    wrong_flagged: int = 0  # 转写不完全一致且被标为不确定
    wrong: int = 0
    flagged: int = 0
    flagged_right: int = 0
    kp_top1: int = 0
    kp_top3: int = 0
    kp_n: int = 0
    diff_abs: float = 0.0
    diff_n: int = 0
    fig_ok: int = 0
    fig_n: int = 0
    extra: list[str] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)
    wrong_pairs: list[str] = field(default_factory=list)
    kp_miss: list[str] = field(default_factory=list)
    ms: float = 0.0
    cost: float = 0.0
    hints: list[str] = field(default_factory=list)
    poor: bool = False
    reason: str = ""


def score_page(case: PhotoCase, rs: ReferenceSet, gold: GoldPage | None, ms: float, cost: float) -> PageScore:
    usable = rs.usable
    verdict = rs.pages[0].verdict.value if rs.pages else "none"
    s = PageScore(case.id, case.group, case.page, usable, verdict, ms=ms, cost=cost)
    s.reason = rs.pages[0].reason if rs.pages else ""
    if rs.pages and rs.pages[0].quality:
        s.hints = rs.pages[0].quality.hints
        s.poor = rs.pages[0].quality.poor
    if gold is None:
        s.n_pred = len(rs.items)
        return s
    pred = rs.items
    pairs, extra = match(gold.items, pred)
    s.n_gold, s.n_pred, s.found = len(gold.items), len(pred), len(pairs)
    paired_g = {id(p.gold) for p in pairs}
    s.missed = [g.raw for g in gold.items if id(g) not in paired_g]
    s.extra = [p.text for p in extra]
    for p in pairs:
        s.gold_chars += len(p.gold.text)
        s.edits += round(p.cer * max(len(p.gold.text), 1))
        s.exact += p.exact
        s.fuzzy += p.cer <= FUZZY_CER
        s.digit_err += p.digit_error
        flagged = p.pred.low_confidence
        s.flagged += flagged
        if flagged and p.exact:
            s.flagged_right += 1
        if not p.exact:
            s.wrong += 1
            s.wrong_flagged += flagged
            s.wrong_pairs.append(f"{p.gold.raw}  ←→  {p.pred.text}")
        if p.gold.kp and p.pred.kp_names is not None:
            s.kp_n += 1
            names = p.pred.kp_names
            s.kp_top1 += bool(names) and names[0] in p.gold.kp
            s.kp_top3 += any(n in p.gold.kp for n in names[:3])
            if not any(n in p.gold.kp for n in names[:3]):
                s.kp_miss.append(f"{p.gold.raw[:30]} → {'、'.join(names[:3]) or '无'}")
        if p.gold.difficulty and p.pred.difficulty:
            s.diff_n += 1
            s.diff_abs += abs(p.gold.difficulty - p.pred.difficulty)
        s.fig_n += 1
        s.fig_ok += p.gold.has_figure == p.pred.has_figure
    return s


# ---- 运行 ----
def llm_usage(sink: MemorySink) -> tuple[float, float]:
    cost = sum(e.record.cost or 0.0 for e in sink.events if isinstance(e, LLMCall))
    ms = sum(e.record.total_ms for e in sink.events if isinstance(e, LLMCall))
    return cost, ms


async def run_cases(
    settings: Settings,
    cases: list[PhotoCase],
    gold: dict[str, GoldPage],
    concurrency: int = 4,
) -> tuple[list[PageScore], dict[str, ReferenceSet]]:
    c = await build_container(settings, store=await Store.open(":memory:"))
    sem = asyncio.Semaphore(concurrency)
    preds: dict[str, ReferenceSet] = {}

    async def one(case: PhotoCase) -> PageScore:
        async with sem:
            ses = await c.store.sessions.create(case.id)
            ctx = RunContext(
                run_id=new_id("run"),
                session_id=ses.id,
                settings=settings,
                llm=c.llm,
                kb=c.kb,
                store=c.store,
            )
            att = Attachment(
                id=f"att_{case.id}",
                filename=case.path.name,
                mime="image/jpeg",
                size=case.path.stat().st_size,
                sha256="",
                session_id=ses.id,
                path=str(case.path.resolve()),
                ts=time.time(),
            )
            sink = MemorySink()
            t0 = time.perf_counter()
            try:
                async with use_tracer(Tracer(ctx.run_id, sink)):
                    rs = await run_stage(ctx, PerceiveStage(), PerceiveIn(attachments=[att]))
            except Exception as e:  # 崩溃也算一次结果：不可用 + 原因
                rs = ReferenceSet(
                    pages=[
                        PageRead(
                            attachment_id=att.id,
                            verdict=PageVerdict.unreadable,
                            reason=f"崩溃：{type(e).__name__}: {str(e)[:120]}",
                        )
                    ],
                )
            ms = (time.perf_counter() - t0) * 1000
            cost, _ = llm_usage(sink)
            preds[case.id] = rs
            return score_page(case, rs, gold.get(case.page), ms, cost)

    try:
        return list(await asyncio.gather(*(one(x) for x in cases))), preds
    finally:
        await c.close()


# ---- 汇总与报告 ----
def _ratio(a: float, b: float) -> float | None:
    return a / b if b else None


def _f(x: float | None, nd: int = 3) -> str:
    return "—" if x is None else f"{x:.{nd}f}"


def summarize(scores: list[PageScore]) -> dict[str, Any]:
    real = [s for s in scores if s.group in ("raw", "variant")]
    neg = [s for s in scores if s.group == "negative"]

    def tot(rows: list[PageScore], k: str) -> float:
        return sum(getattr(r, k) for r in rows)

    out: dict[str, Any] = {}
    for name, rows in (
        ("raw", [s for s in scores if s.group == "raw"]),
        ("variant", [s for s in scores if s.group == "variant"]),
        ("fresh", [s for s in scores if s.group == "fresh"]),
        ("all", real),
    ):
        if not rows:
            continue
        g, f = tot(rows, "n_gold"), tot(rows, "found")
        out[name] = {
            "pages": len(rows),
            "b4_exact": _ratio(tot(rows, "exact"), g),
            "b4_fuzzy": _ratio(tot(rows, "fuzzy"), g),
            "recall": _ratio(f, g),
            "precision": _ratio(f, tot(rows, "n_pred")),
            "cer": _ratio(tot(rows, "edits"), tot(rows, "gold_chars")),
            "digit_err": _ratio(tot(rows, "digit_err"), f),
            "kp_top1": _ratio(tot(rows, "kp_top1"), tot(rows, "kp_n")),
            "kp_top3": _ratio(tot(rows, "kp_top3"), tot(rows, "kp_n")),
            "diff_mae": _ratio(tot(rows, "diff_abs"), tot(rows, "diff_n")),
            "fig_ok": _ratio(tot(rows, "fig_ok"), tot(rows, "fig_n")),
            "extra_per_page": tot(rows, "n_pred") / len(rows) - f / len(rows),
            "wrong_flag_recall": _ratio(tot(rows, "wrong_flagged"), tot(rows, "wrong")),
            "flag_precision_right": _ratio(tot(rows, "flagged_right"), tot(rows, "flagged")),
            "refuse_rate": 1 - sum(r.usable for r in rows) / len(rows),
        }
    if neg:
        out["negative"] = {"n": len(neg), "refused": sum(not s.usable for s in neg) / len(neg)}
    degraded = [s for s in scores if s.group == "variant" and re.search(r"~(blur|dark|hard)$", s.case)]
    clean = [s for s in real if s not in degraded]
    if degraded:
        out["poor_quality"] = {
            "degraded": sum(s.poor for s in degraded) / len(degraded),
            "n_degraded": len(degraded),
            "clean_false": sum(s.poor for s in clean) / len(clean) if clean else None,
            "n_clean": len(clean),
        }
    lat = sorted(s.ms for s in scores if s.ms)
    if lat:
        out["latency_s"] = {
            "p50": lat[len(lat) // 2] / 1000,
            "p95": lat[min(len(lat) - 1, int(len(lat) * 0.95))] / 1000,
        }
    out["cost_per_page"] = _ratio(tot(scores, "cost"), len(scores))
    return out


def render_report(scores: list[PageScore], title: str, summary: dict[str, Any]) -> str:
    L = [f"# 拍照感知评测：{title}", ""]
    for name in ("raw", "variant", "fresh", "all"):
        m = summary.get(name)
        if not m:
            continue
        label = {
            "raw": "真实照片（开发集）",
            "variant": "变体",
            "fresh": "验收集（未见页）",
            "all": "合计（开发集）",
        }[name]
        L += [
            f"## {label}（{m['pages']} 张）",
            "",
            "| 指标 | 值 | 阈值 |",
            "|---|---|---|",
            f"| **B4 题级一致（归一化后完全一致）** | {_f(m['b4_exact'])} | ≥ 0.85 |",
            f"| B4 题级一致（字符错误率 ≤ {FUZZY_CER}） | {_f(m['b4_fuzzy'])} | — |",
            f"| B4 召回（找到的标注题） | {_f(m['recall'])} | — |",
            f"| B4 精确（识别题有对应标注） | {_f(m['precision'])} | ≥ 0.90 |",
            f"| B4 字符错误率 CER | {_f(m['cer'], 4)} | — |",
            f"| B4 数字序列错误（配对题） | {_f(m['digit_err'])} | ≤ 0.02 |",
            f"| **B5 知识点 top-1 / top-3** | {_f(m['kp_top1'])} / {_f(m['kp_top3'])} | top-3 ≥ 0.85 |",
            f"| B6 难度 MAE | {_f(m['diff_mae'])} | ≤ 0.8 |",
            f"| P4 图题判定正确 | {_f(m['fig_ok'])} | ≥ 0.90 |",
            f"| P3 每页多出的题 | {m['extra_per_page']:.2f} | ≤ 0.3 |",
            f"| P7 转写出错的题被标不确定（召回） | {_f(m['wrong_flag_recall'])} | ≥ 0.6 |",
            f"| P7 被标不确定的题其实读对了（打扰度） | {_f(m['flag_precision_right'])} | 报告 |",
            f"| **P6 误拒率** | {_f(m['refuse_rate'])} | ≤ 0.03 |",
            "",
        ]
    if "negative" in summary:
        n = summary["negative"]
        L += [
            f"## 负例（{n['n']} 张）",
            "",
            f"| **P5 拒识率** | {n['refused']:.3f}（{round(n['refused'] * n['n'])}/{n['n']}） | ≥ 0.95 |",
            "|---|---|---|",
            "",
        ]
    if "poor_quality" in summary:
        pq = summary["poor_quality"]
        L += [
            f"- 画质核对（确定性）：模糊 / 过暗 / 低清变体被要求核对 {pq['degraded']:.3f}（n={pq['n_degraded']}）；清晰照片被误要求核对 {_f(pq['clean_false'])}（n={pq['n_clean']}）",
            "",
        ]
    if "latency_s" in summary:
        lt = summary["latency_s"]
        L += [
            f"- 单张识别时延 p50 / p95：{lt['p50']:.1f}s / {lt['p95']:.1f}s（目标 ≤ 15s / ≤ 25s）",
            f"- 单张成本：¥{summary['cost_per_page'] or 0:.4f}",
            "",
        ]
    L += [
        "## 逐张",
        "",
        "| 张 | 组 | 判定 | 标注/识别 | 一致 | 找到 | 数字错 | top3 | 误报 | 秒 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in scores:
        L.append(
            f"| {s.case} | {s.group} | {s.verdict}{'' if s.usable else '（拒）'} | {s.n_gold}/{s.n_pred} | {s.exact} | {s.found} | {s.digit_err} | {s.kp_top3}/{s.kp_n} | {len(s.extra)} | {s.ms / 1000:.1f} |"
        )
    L += ["", "## 错误明细（真实照片与变体）", ""]
    for s in scores:
        if s.group == "negative":
            if s.usable:
                L.append(f"- **{s.case}**（负例，却被判为可用）：{s.extra[:3] or s.reason}")
            continue
        if not (s.wrong_pairs or s.missed or s.extra) and s.usable:
            continue
        L.append(f"### {s.case}{'' if s.usable else f'（被拒：{s.verdict}，{s.reason}）'}")
        for w in s.wrong_pairs[:8]:
            L.append(f"- 读错：{w}")
        for m in s.missed[:6]:
            L.append(f"- 漏掉：{m}")
        for e in s.extra[:6]:
            L.append(f"- 多出：{e}")
        for k in s.kp_miss[:4]:
            L.append(f"- 知识点未命中：{k}")
        L.append("")
    return "\n".join(L)


def dump_predictions(path: Path, scores: list[PageScore], preds: dict[str, ReferenceSet]) -> None:
    data = {
        "scores": [asdict(s) for s in scores],
        "preds": {k: v.model_dump(mode="json") for k, v in preds.items()},
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

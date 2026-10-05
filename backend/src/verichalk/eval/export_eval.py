"""M6 导出评测（eval/specs/export.md）：用例 × 格式 × 版本，自动检测公式漏转、缺字、泄题、图片丢失、占位符残留等。

检测器本身有单元测试（`tests/test_export.py` 构造坏输出确认抓得到）；这里是把它们跑在评测集上并出报告。
不调用模型，确定、零成本；PDF / Word 的内容检查依赖 PyMuPDF（评测与开发依赖，不是服务依赖）。
"""

from __future__ import annotations

import io
import re
import statistics
import time
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..core.errors import ExportError
from ..core.ids import new_id
from ..domain.export import AnswerPlacement, ExportFormat, ExportOptions, ExportVersion
from ..domain.paper import Item, ItemKind, Paper, Section
from ..figures import FigureError, render_figure
from ..render import ExportResult, export_paper
from ..render.texcheck import compile_latex
from ..render.view import build_view
from ..verify.content import math_segments

PLACEHOLDER = re.compile(r"ZZ(?:F\d+|FIG\d+|BLANK|SEP|EMPTY)ZZ")
TEX_LEAK = re.compile(r"\\[A-Za-z]{2,}")
CJK_RUN = re.compile(r"[一-鿿]{4,}")
FORMATS = (ExportFormat.pdf, ExportFormat.docx, ExportFormat.md, ExportFormat.tex)
VERSIONS = (ExportVersion.student, ExportVersion.teacher)
W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
M_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"


@dataclass
class ExportCase:
    id: str
    tags: list[str]
    paper: Paper
    pages: tuple[int, int] | None = None
    expect_error: bool = False
    warn_ok: bool = False


def load_export_cases(path: Path) -> list[ExportCase]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    out: list[ExportCase] = []
    for c in raw:
        p = c["paper"]
        sections = []
        for s in p.get("sections", []):
            items = [
                Item.model_validate({"id": new_id("it"), "kind": "fill", **it})
                for it in (s.get("items") or [])
            ]
            sections.append(Section(id=new_id("sec"), title=s.get("title", ""), items=items))
        paper = Paper(id=c["id"], title=p.get("title", ""), rev=1, sections=sections, meta=p.get("meta", {}))
        pages = tuple(c["pages"]) if c.get("pages") else None
        out.append(
            ExportCase(
                c["id"],
                c.get("tags", []),
                paper,
                pages,
                bool(c.get("expect_error")),
                bool(c.get("warn_ok")),
            )
        )
    return out


def synthetic_paper(n: int, pid: str = "synthetic") -> Paper:
    """n 题的混合试卷（公式、选项、应用题轮换），用于长卷与延迟测试。"""
    items = []
    for i in range(n):
        k = i % 4
        if k == 0:
            it = Item(
                id=f"s{i}",
                kind=ItemKind.calc,
                stem=f"计算：${i + 2}.5\\times {i + 3}-\\frac{{{i + 1}}}{{4}}=$____",
                answer=str(i),
                solution=f"${i + 2}.5\\times{i + 3}=\\ldots$",
                score=3,
            )
        elif k == 1:
            it = Item(
                id=f"s{i}",
                kind=ItemKind.choice,
                stem=f"第 {i + 1} 题：下面哪个数最大？",
                options=["0.5", "0.49", "0.051", "0.5001"],
                answer="D",
                score=2,
            )
        elif k == 2:
            it = Item(
                id=f"s{i}",
                kind=ItemKind.application,
                stem=f"小明买了 {i + 2} 本书，每本 ${i + 1}.5$ 元，付了 ${(i + 2) * 10}$ 元，应找回多少元？",
                answer="略",
                solution="先求总价，再求找回的钱。",
                score=6,
            )
        else:
            it = Item(
                id=f"s{i}",
                kind=ItemKind.fill,
                stem=f"$\\frac{{{i}}}{{5}}+0.{i % 9 + 1}=$____",
                answer="略",
                score=3,
            )
        items.append(it)
    half = len(items) // 2
    return Paper(
        id=pid,
        title=f"综合练习（{n} 题）",
        rev=1,
        sections=[
            Section(id="a", title="基础", items=items[:half]),
            Section(id="b", title="提高", items=items[half:]),
        ],
    )


# ---- 期望与检测 ----
def expected_formulas(paper: Paper, opts: ExportOptions) -> int:
    return len(math_segments(build_view(paper, opts).text()))


def expected_figures(paper: Paper) -> int:
    n = 0
    for it in paper.all_items():
        for f in it.figures:
            try:
                render_figure(f)
                n += 1
            except FigureError:
                pass
    return n


def key_phrases(paper: Paper) -> list[str]:
    """每道题题干里第一段 ≥4 个连续汉字（去掉公式），导出文本里必须找得到。"""
    out = []
    for it in paper.all_items():
        text = re.sub(r"\$.*?\$", "", it.stem, flags=re.S)
        m = CJK_RUN.search(text)
        if m:
            out.append(m.group(0))
    return out


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s)


@dataclass
class Failure:
    code: str  # X1 公式 / X3 缺字 / X5 内容 / X6 分值 / X7 图 / X0 其他
    detail: str


def _pdf_text(data: bytes) -> tuple[str, int]:
    import pymupdf  # 评测依赖

    doc = pymupdf.open(stream=data, filetype="pdf")
    return "\n".join(str(p.get_text()) for p in doc), doc.page_count


def _docx_info(data: bytes) -> tuple[str, int, int, int]:
    """返回 (正文文本, 原生公式数, 图片数, 段落数)。"""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    text = "".join(t.text or "" for t in root.iter(f"{W_NS}t"))
    # 行内公式里的文字也在 m:t，不计入正文
    omml = sum(1 for _ in root.iter(f"{M_NS}oMath"))
    drawings = sum(1 for _ in root.iter(f"{W_NS}drawing"))
    paras = sum(1 for _ in root.iter(f"{W_NS}p"))
    return text, omml, drawings, paras


def check_result(
    case: ExportCase, opts: ExportOptions, res: ExportResult, *, tex_compile: bool = False
) -> list[Failure]:
    fails: list[Failure] = []
    paper = case.paper
    fmt, ver = opts.format, opts.version
    formulas = expected_formulas(paper, opts)
    figures = expected_figures(paper)
    phrases = key_phrases(paper)
    robustness = "robustness" in case.tags

    def leak_checks(text: str) -> None:
        if PLACEHOLDER.search(text):
            fails.append(Failure("X0", f"残留占位符：{PLACEHOLDER.search(text).group(0)}"))  # type: ignore[union-attr]
        if ver == ExportVersion.student:
            for bad in ("参考答案", "答案：", "解析："):
                if bad in text and not any(bad in it.stem for it in paper.all_items()):
                    fails.append(Failure("X5", f"学生版出现“{bad}”"))
            for it in paper.all_items():
                sol = it.solution.strip()
                if len(sol) >= 8 and _squash(sol) in _squash(text):
                    fails.append(Failure("X5", f"学生版泄露了题 {it.id} 的解析"))

    def phrase_check(text: str) -> None:
        sq = _squash(text)
        for ph in phrases:
            if ph not in sq:
                fails.append(Failure("X5", f"缺少题干文字：{ph}"))
                break

    if fmt == ExportFormat.pdf:
        text, pages = _pdf_text(res.data)
        if TEX_LEAK.search(text) and not robustness:
            fails.append(Failure("X1", f"PDF 文本里有未转换的 TeX 命令：{TEX_LEAK.search(text).group(0)}"))  # type: ignore[union-attr]
        if text.count("$") and not robustness and "special_chars" not in case.tags:
            fails.append(Failure("X1", "PDF 文本里有孤立的美元符号"))
        if case.pages:
            lo, hi = case.pages
            if not lo <= pages <= (hi + 2 if ver == ExportVersion.student else hi + 1):
                fails.append(Failure("X0", f"页数 {pages} 超出预期 {case.pages}"))
        leak_checks(text)
        phrase_check(text)
    elif fmt == ExportFormat.docx:
        text, omml, drawings, _ = _docx_info(res.data)
        if omml != formulas and not robustness:
            fails.append(Failure("X1", f"Word 原生公式数 {omml} ≠ 内容里的公式数 {formulas}"))
        if drawings != figures:
            fails.append(Failure("X7", f"Word 图片数 {drawings} ≠ 可绘制的图形数 {figures}"))
        leak_checks(text)
        phrase_check(text)
    elif fmt == ExportFormat.md:
        text = res.data.decode("utf-8")
        n_img = len(re.findall(r"!\[[^\]]*\]\(data:image/svg", text))
        if n_img != figures:
            fails.append(Failure("X7", f"Markdown 内嵌图 {n_img} ≠ 可绘制的图形数 {figures}"))
        leak_checks(text)
        phrase_check(text)
    else:  # tex（单文件）或 zip（有图）
        if res.filename.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(res.data)) as z:
                tex = z.read("paper.tex").decode("utf-8")
                n_png = sum(1 for n in z.namelist() if n.endswith(".png"))
            if n_png != figures:
                fails.append(Failure("X7", f"LaTeX 图 {n_png} ≠ 可绘制的图形数 {figures}"))
        else:
            tex = res.data.decode("utf-8")
            if figures:
                fails.append(Failure("X7", "有图形但 LaTeX 导出里没有图片文件"))
        leak_checks(tex)
        if tex.count("\\begin{") != tex.count("\\end{"):
            fails.append(Failure("X0", "LaTeX 环境 begin / end 不配对"))
        if tex_compile and not fails:
            err = compile_latex(res.data, is_zip=res.filename.endswith(".zip"))
            if err:
                fails.append(Failure("X9", err))
    if res.warnings and not case.warn_ok:
        for w in res.warnings:
            fails.append(
                Failure("X3" if "字形" in w or "字体" in w else "X6" if "分值" in w else "X0", f"警告：{w}")
            )
    return fails


@dataclass
class Row:
    case: str
    fmt: str
    version: str
    ok: bool  # 导出成功（或按预期给出了友好错误）
    failures: list[Failure] = field(default_factory=list)
    ms: float = 0.0
    size: int = 0
    error: str = ""


def run_export_eval(
    cases: list[ExportCase],
    *,
    font_dirs: tuple[str, ...] = (),
    tex_compile: bool = False,
    dump_dir: Path | None = None,
) -> list[Row]:
    rows: list[Row] = []
    for case in cases:
        for fmt in FORMATS:
            for ver in VERSIONS:
                opts = ExportOptions(format=fmt, version=ver, answers=AnswerPlacement.appendix)
                t0 = time.perf_counter()
                try:
                    res = export_paper(case.paper, opts, font_dirs=font_dirs)
                except ExportError as e:
                    rows.append(
                        Row(
                            case.id,
                            fmt.value,
                            ver.value,
                            case.expect_error,
                            [],
                            (time.perf_counter() - t0) * 1000,
                            0,
                            f"{e.user_message}",
                        )
                    )
                    continue
                except Exception as e:  # 评测要看到所有崩溃，而不是中断
                    rows.append(
                        Row(
                            case.id,
                            fmt.value,
                            ver.value,
                            False,
                            [],
                            (time.perf_counter() - t0) * 1000,
                            0,
                            f"崩溃：{type(e).__name__}: {str(e)[:200]}",
                        )
                    )
                    continue
                ms = (time.perf_counter() - t0) * 1000
                if dump_dir is not None:
                    (dump_dir / f"{case.id}__{ver.value}.{res.filename.rsplit('.', 1)[-1]}").write_bytes(
                        res.data
                    )
                fails = [] if case.expect_error else check_result(case, opts, res, tex_compile=tex_compile)
                if case.expect_error:
                    fails = [Failure("X0", "应当给出“没有题目”的错误，却导出了文件")]
                rows.append(
                    Row(case.id, fmt.value, ver.value, not case.expect_error, fails, ms, len(res.data))
                )
    return rows


def latency(n: int = 20, runs: int = 5, font_dirs: tuple[str, ...] = ()) -> dict[str, tuple[float, float]]:
    paper = synthetic_paper(n)
    out: dict[str, tuple[float, float]] = {}
    for fmt in FORMATS:
        opts = ExportOptions(format=fmt, version=ExportVersion.teacher)
        export_paper(paper, opts, font_dirs=font_dirs)  # 预热
        ts = []
        for _ in range(runs):
            t0 = time.perf_counter()
            export_paper(paper, opts, font_dirs=font_dirs)
            ts.append((time.perf_counter() - t0) * 1000)
        ts.sort()
        out[fmt.value] = (statistics.median(ts), ts[-1])
    return out


def summarize(rows: list[Row]) -> dict[str, Any]:
    total = len(rows)
    ok = sum(r.ok for r in rows)
    flawed = sum(1 for r in rows if r.ok and r.failures)
    by_code: dict[str, int] = {}
    for r in rows:
        for f in r.failures:
            by_code[f.code] = by_code.get(f.code, 0) + 1
    docx = [r for r in rows if r.fmt == "docx" and r.ok]
    x2 = (
        sum(1 for r in docx if not any("原生公式" in f.detail for f in r.failures)) / len(docx)
        if docx
        else 1.0
    )
    return {
        "total": total,
        "c4a": ok / total if total else 0.0,
        "c4b": flawed / total if total else 0.0,
        "x2": x2,
        "by_code": by_code,
    }


def render_report(rows: list[Row], s: dict[str, Any], lat: dict[str, tuple[float, float]], desc: str) -> str:
    lines = [f"# 导出评测：{desc}", ""]
    lines += [
        "| 指标 | 值 | 阈值 | 判定 |",
        "|---|---|---|---|",
        f"| C4(a) 导出成功率 | {s['c4a']:.3f}（{round(s['c4a'] * s['total'])}/{s['total']}） | ≥ 0.99 | {'✅' if s['c4a'] >= 0.99 else '❌'} |",
        f"| C4(b) 自动检测错乱率 | {s['c4b']:.3f} | ≤ 0.02 | {'✅' if s['c4b'] <= 0.02 else '❌'} |",
        f"| X2 Word 原生公式 | {s['x2']:.3f} | = 1.00 | {'✅' if s['x2'] >= 1 else '❌'} |",
    ]
    lines += [
        "",
        "按失败类别：" + (", ".join(f"{k}×{v}" for k, v in sorted(s["by_code"].items())) or "无"),
        "",
    ]
    lines += [
        "延迟（20 题试卷，预热后，ms：p50 / max）："
        + "；".join(f"{k} {a:.0f} / {b:.0f}" for k, (a, b) in lat.items()),
        "",
    ]
    bad = [r for r in rows if not r.ok or r.failures]
    if bad:
        lines += ["## 失败明细", "", "| 用例 | 格式 | 版本 | 类别 | 说明 |", "|---|---|---|---|---|"]
        for r in bad:
            if r.error and not r.ok:
                lines.append(f"| {r.case} | {r.fmt} | {r.version} | 崩溃 | {r.error} |")
            for f in r.failures:
                lines.append(f"| {r.case} | {r.fmt} | {r.version} | {f.code} | {f.detail} |")
        lines.append("")
    lines += ["## 全部用例", "", "| 用例 | 格式 | 版本 | 结果 | ms | 字节 |", "|---|---|---|---|---|---|"]
    for r in rows:
        mark = "✅" if r.ok and not r.failures else ("⚠️" if r.ok else "❌")
        lines.append(f"| {r.case} | {r.fmt} | {r.version} | {mark} | {r.ms:.0f} | {r.size} |")
    return "\n".join(lines) + "\n"

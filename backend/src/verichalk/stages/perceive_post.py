"""感知的后处理（确定性，不调用模型）：视觉模型的原始输出 → 扁平题目；知识点映射 → 学生上下文；友好的拒识回复。

与 `understand_post` 同理：把"需要可复现、可测试"的部分放在代码里，而不是提示词里。
"""

from __future__ import annotations

from collections import Counter

from ..domain.knowledge import KPHit
from ..domain.paper import ItemKind
from ..domain.perception import (
    ImageQuality,
    PageRead,
    PageVerdict,
    PerceivedItem,
    RawPage,
    ReferenceSet,
    StudentContext,
)
from .understand_post import grade_text, map_topics

LOW_CONFIDENCE = 0.6  # 整道大题的自评把握低于它：该题所有小题都需要教师确认
FLAGGED_CONFIDENCE = 0.5  # 被标了"看不清"的小题的置信度
MAX_KINDS = 2

_VERDICT_TIPS = {
    PageVerdict.not_math: "这张图看起来不是数学练习页",
    PageVerdict.beyond_primary: "这张图的内容超出了小学数学范围",
    PageVerdict.no_exercises: "这一页上没有找到需要作答的题目",
    PageVerdict.unreadable: "这张图太模糊或太暗，读不清上面的题",
}
_NEXT_STEP = "请对准一页有题目的练习册，光线充足、整页入镜后重新拍一张；也可以直接用文字告诉我想出什么题。"


def flatten(raw: RawPage, *, attachment_id: str, filename: str, page_index: int) -> PageRead:
    """把模型输出的分组题目展开成小题，并把"看不清"标记落到具体小题上。"""
    items: list[PerceivedItem] = []
    for q in raw.questions:
        whole_doubt = next((d.note for d in q.doubts if d.item == 0), "")
        low_whole = q.confidence < LOW_CONFIDENCE
        for k, text in enumerate(q.items, start=1):
            note = next((d.note for d in q.doubts if d.item == k), "") or whole_doubt
            if not note and low_whole:
                note = "整道题的把握较低，请核对"
            items.append(
                PerceivedItem(
                    id=f"r{page_index + 1}.{len(items) + 1}",
                    attachment_id=attachment_id,
                    no=q.no,
                    instruction=q.instruction,
                    text=text,
                    kind=q.kind,
                    has_figure=q.has_figure,
                    figure_desc=q.figure,
                    topic=q.topic,
                    difficulty=q.difficulty,
                    confidence=min(q.confidence, FLAGGED_CONFIDENCE) if note else q.confidence,
                    uncertain=note,
                )
            )
    verdict, reason = raw.verdict, raw.reason
    if verdict is PageVerdict.worksheet and not items:
        verdict, reason = PageVerdict.no_exercises, reason or "没有读出需要作答的题"
    if verdict is not PageVerdict.worksheet:
        items = []
    return PageRead(
        attachment_id=attachment_id,
        filename=filename,
        verdict=verdict,
        reason=reason,
        title=raw.title,
        items=items,
    )


def rejected_page(
    attachment_id: str, filename: str, reason: str, quality: ImageQuality | None = None
) -> PageRead:
    return PageRead(
        attachment_id=attachment_id,
        filename=filename,
        verdict=PageVerdict.unreadable,
        reason=reason,
        quality=quality,
    )


def assign_kps(page: PageRead, hits_by_query: dict[str, list[KPHit]], *, use_topics: bool) -> None:
    """给每道题挂知识点：优先按题自己的 `topic` 检索结果，没有就用页面标题的结果。"""
    title_hits = map_topics(hits_by_query.get(page.title, [])) if page.title else []
    for it in page.items:
        hits = map_topics(hits_by_query.get(it.topic, [])) if (use_topics and it.topic) else []
        hits = hits or title_hits
        it.kp_ids = [h.id for h in hits]
        it.kp_names = [h.name for h in hits]
    count = Counter(k for it in page.items for k in it.kp_ids)
    page.kp_ids = [k for k, _ in count.most_common()]


def build_context(pages: list[PageRead], hits_by_query: dict[str, list[KPHit]]) -> StudentContext:
    """可用页面的题 → 年级 / 学期 / 知识点 / 难度 / 题型分布。lesson_id 由调用方用知识库补（需要教学序列）。"""
    items = [it for p in pages if p.verdict.usable for it in p.items]
    if not items:
        return StudentContext()
    meta: dict[str, KPHit] = {}
    for hits in hits_by_query.values():
        for h in hits:
            meta.setdefault(h.id, h)
    count = Counter(k for it in items for k in it.kp_ids)
    names: dict[str, str] = {}
    for it in items:
        names.update(zip(it.kp_ids, it.kp_names, strict=False))
    # 学段：按题数加权的 (年级, 学期) 众数
    gs = Counter(
        (meta[k].grade, meta[k].semester)
        for it in items
        for k in it.kp_ids[:1]
        if k in meta and meta[k].grade
    )
    grade, semester = gs.most_common(1)[0][0] if gs else (None, None)
    top = [k for k, _ in count.most_common(4)]
    if grade is not None:  # 只留学段内的知识点（"总复习"之类跨学段命中不当作学生所学）
        in_stage = [k for k in top if k in meta and meta[k].grade == grade]
        top = in_stage or top
    diffs = [it.difficulty for it in items if it.difficulty]
    difficulty = None
    if diffs:
        mean = round(sum(diffs) / len(diffs))
        difficulty = [max(1, mean - 1), min(5, mean + 1)]
    qcount = Counter()
    seen: set[tuple[str, str, str]] = set()
    for it in items:  # 每道大题只数一次
        key = (it.attachment_id, it.no, it.instruction)
        if key in seen:
            continue
        seen.add(key)
        if it.kind is not None and it.kind is not ItemKind.open:
            qcount[it.kind] += 1
    kinds = [k for k, _ in qcount.most_common(MAX_KINDS)] if 0 < len(qcount) <= MAX_KINDS else []
    kp_names = [names[k] for k in top if k in names]
    place = grade_text(grade, semester) if grade else ""
    summary = " · ".join(x for x in (place, "、".join(kp_names[:3])) if x)
    return StudentContext(
        grade=grade,
        semester=semester if semester in ("a", "b") else None,
        kp_ids=top,
        kp_names=kp_names,
        difficulty=difficulty,
        kinds=kinds,
        summary=summary,
    )


def apply_edits(rs: ReferenceSet, edits: list[dict[str, object]]) -> ReferenceSet:
    """教师在确认卡片里的修改：`{"id", "text"?, "remove"?}`。确认之后所有题都不再标"看不清"。
    只改文字与去留；知识点映射沿用（改字不改考点）。"""
    by_id = {str(e.get("id")): e for e in edits if e.get("id")}
    pages: list[PageRead] = []
    for p in rs.pages:
        items: list[PerceivedItem] = []
        for it in p.items:
            e = by_id.get(it.id)
            if e and e.get("remove"):
                continue
            text = str(e.get("text", "")).strip() if e else ""
            items.append(it.model_copy(update={"text": text or it.text, "uncertain": "", "confidence": 1.0}))
        pages.append(p.model_copy(update={"items": items}))
    out = rs.model_copy(update={"pages": pages, "needs_confirm": False})
    out.usable = any(p.verdict.usable and p.items for p in pages)
    if not out.usable:
        out.message = "已把识别出的题都去掉了，没有可参考的题。可以重新拍一张，或直接用文字描述想出的题。"
    return out


def failure_message(pages: list[PageRead]) -> str:
    """没有可用页面时给教师的话：说原因 + 下一步建议（FR-2 AC：友好反馈，不乱出题）。"""
    reasons: list[str] = []
    for p in pages:
        base = _VERDICT_TIPS.get(p.verdict, "这张图没能识别")
        why = p.reason.strip().rstrip("。")
        reasons.append(f"{base}（{why}）" if why and why not in base else base)
    head = reasons[0] if len(pages) == 1 else "；".join(f"第 {i + 1} 张：{r}" for i, r in enumerate(reasons))
    hints = [h for p in pages if p.quality for h in p.quality.hints]
    tip = ("拍摄建议：" + "；".join(dict.fromkeys(hints)) + "。") if hints else ""
    return f"{head}。{tip}{_NEXT_STEP}"


def assemble_set(pages: list[PageRead], context: StudentContext) -> ReferenceSet:
    usable = [p for p in pages if p.verdict.usable and p.items]
    rs = ReferenceSet(pages=pages, context=context, usable=bool(usable))
    if not usable:
        rs.message = failure_message(pages)
    else:
        rs.needs_confirm = any(it.low_confidence for p in usable for it in p.items) or any(
            p.quality is not None and p.quality.poor for p in usable
        )
        skipped = [p for p in pages if not p.verdict.usable]
        if skipped:
            rs.message = f"有 {len(skipped)} 张图没能识别（{'；'.join(_VERDICT_TIPS.get(p.verdict, '无法识别') for p in skipped)}），已用其余照片。"
    return rs

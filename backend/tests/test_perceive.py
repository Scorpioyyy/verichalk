"""M4 感知：预处理、识别结果的后处理、学生上下文、防雷同、确认检查点、拒识与隐私（eval/specs/perceive.md 失败模式 1～14）。"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw, ImageFilter

from fakes import FakeTransport, content_chunks, usage
from verichalk.core.config import LLMMode, Settings
from verichalk.core.errors import LLMError
from verichalk.core.features import FeatureFlags
from verichalk.domain.brief import Origin
from verichalk.domain.events import PerceptionReady
from verichalk.domain.knowledge import KPHit
from verichalk.domain.paper import CheckStatus, ItemKind, Tier, VerifyStatus
from verichalk.domain.perception import (
    PageRead,
    PageVerdict,
    PerceivedItem,
    RawPage,
    RawQuestion,
    ReferenceSet,
    StudentContext,
)
from verichalk.domain.run import Attachment
from verichalk.domain.understanding import RawParse, Route
from verichalk.llm import build_gateway
from verichalk.orchestrator.pipelines import TurnInput, main_pipeline, perceive_turn
from verichalk.perception import PrepareError, prepare, thumbnail_jpeg, to_data_uri
from verichalk.stages import RunContext, run_stage
from verichalk.stages.perceive import PerceiveIn, PerceiveStage
from verichalk.stages.perceive_post import (
    apply_edits,
    assemble_set,
    assign_kps,
    build_context,
    failure_message,
    flatten,
)
from verichalk.stages.understand_post import finalize
from verichalk.trace import MemorySink, Tracer, use_tracer
from verichalk.verify import VerifyInput, verify_item
from verichalk.verify.checks import VerifyEnv, check_novelty, copy_score


# ---- 图片夹具 ----
def stripes(w: int = 1200, h: int = 1600) -> Image.Image:
    """清晰的"练习页"：满页横竖线条与小字（边缘响应大，质量检测应判为清晰）。"""
    im = Image.new("RGB", (w, h), (246, 245, 240))
    d = ImageDraw.Draw(im)
    for y in range(40, h, 24):
        d.line([(30, y), (w - 30, y)], fill=(20, 20, 20), width=2)
    for x in range(40, w, 30):
        d.line([(x, 20), (x, h - 20)], fill=(40, 40, 40), width=1)
    return im


def jpeg(im: Image.Image) -> bytes:
    b = io.BytesIO()
    im.save(b, "JPEG", quality=90)
    return b.getvalue()


# ---- 预处理 ----
def test_prepare_caps_long_side_and_reports_quality() -> None:
    p = prepare(jpeg(stripes(3000, 4000)), max_side=1600)
    im = Image.open(io.BytesIO(p.data))
    assert max(im.size) == 1600 and p.quality.orig_width == 3000
    assert not p.reject and not p.quality.poor and p.quality.hints == []
    assert to_data_uri(p).startswith("data:image/jpeg;base64,")


def test_prepare_transposes_exif_orientation() -> None:
    im = stripes(600, 800)
    exif = Image.Exif()
    exif[0x0112] = 6  # 需要顺时针旋转 90°
    b = io.BytesIO()
    im.save(b, "JPEG", exif=exif)
    p = prepare(b.getvalue())
    assert (p.quality.width, p.quality.height) == (800, 600)


def test_prepare_flags_blur_dark_and_low_resolution_but_not_clean_pages() -> None:
    clean = stripes()
    blurred = clean.filter(ImageFilter.GaussianBlur(10))
    dark = Image.eval(clean, lambda v: int(v * 0.25))
    tiny = stripes(500, 650)
    assert not prepare(jpeg(clean)).quality.poor
    for bad in (blurred, dark, tiny):
        q = prepare(jpeg(bad)).quality
        assert q.poor and q.hints, "画质差的照片要提示教师核对"
    assert any("模糊" in h for h in prepare(jpeg(blurred)).quality.hints)


def test_prepare_rejects_only_extreme_cases() -> None:
    assert "全黑" in prepare(jpeg(Image.new("RGB", (800, 1000), (5, 5, 5)))).reject
    assert "空白" in prepare(jpeg(Image.new("RGB", (800, 1000), (252, 252, 252)))).reject
    assert "太小" in prepare(jpeg(Image.new("RGB", (120, 160), (120, 120, 120)))).reject
    assert prepare(jpeg(stripes())).reject == ""


def test_prepare_rejects_non_images() -> None:
    with pytest.raises(PrepareError) as e:
        prepare(b"not an image")
    assert "图片" in e.value.user_message


def test_thumbnail_is_small_jpeg() -> None:
    t = thumbnail_jpeg(jpeg(stripes(3000, 4000)), 400)
    assert max(Image.open(io.BytesIO(t)).size) == 400


# ---- 模型输出的宽容解析与扁平化 ----
def test_raw_question_is_lenient_about_model_quirks() -> None:
    q = RawQuestion.model_validate(
        {
            "no": 1,
            "kind": "计算题",
            "difficulty": "9",
            "confidence": "0.4",
            "items": ["0.70=", "", " 3.800= "],
            "has_figure": "true",
            "doubts": ["第2个数可能是3.6"],
        }
    )
    assert q.no == "1" and q.kind == ItemKind.calc and q.difficulty == 5
    assert q.items == ["0.70=", "3.800="] and q.has_figure and q.confidence == 0.4
    assert q.doubts[0].note.startswith("第2个数") and q.doubts[0].item == 0
    assert RawQuestion.model_validate({"kind": "奇怪", "difficulty": "x"}).kind is None


def raw_page() -> RawPage:
    return RawPage.model_validate(
        {
            "verdict": "worksheet",
            "title": "小数的意义(三)(3)",
            "questions": [
                {
                    "no": "1",
                    "instruction": "化简各数。",
                    "kind": "calc",
                    "topic": "小数的性质",
                    "difficulty": 1,
                    "items": ["0.70=", "0.6050=", "3.800="],
                    "doubts": [{"item": 2, "note": "像 0.6050 也像 0.6060"}],
                },
                {"no": "2", "kind": "application", "confidence": 0.3, "items": ["食堂运来大米10.7吨……"]},
            ],
        }
    )


def test_flatten_expands_groups_and_marks_uncertain_items() -> None:
    page = flatten(raw_page(), attachment_id="att_1", filename="a.jpg", page_index=0)
    assert [it.text for it in page.items] == ["0.70=", "0.6050=", "3.800=", "食堂运来大米10.7吨……"]
    assert [it.id for it in page.items] == ["r1.1", "r1.2", "r1.3", "r1.4"]
    assert not page.items[0].low_confidence
    assert "0.6060" in page.items[1].uncertain and page.items[1].confidence <= 0.5
    assert page.items[3].low_confidence, "整题把握很低：所有小题都要核对"
    assert page.items[0].instruction == "化简各数。"


def test_flatten_non_worksheet_or_empty_has_no_items() -> None:
    raw = RawPage(verdict=PageVerdict.not_math, reason="这是一篇英文短文", questions=raw_page().questions)
    page = flatten(raw, attachment_id="a", filename="", page_index=0)
    assert page.items == [] and not page.verdict.usable
    empty = flatten(RawPage(verdict=PageVerdict.worksheet), attachment_id="a", filename="", page_index=0)
    assert empty.verdict is PageVerdict.no_exercises


# ---- 知识点映射与学生上下文 ----
def hit(kid: str, name: str, grade: int = 4, sem: str = "b", score: float = 1.0, lesson: str = "") -> KPHit:
    return KPHit(id=kid, name=name, grade=grade, semester=sem, score=score, lesson_id=lesson or None)


def test_assign_kps_prefers_item_topic_then_page_title() -> None:
    page = flatten(raw_page(), attachment_id="a", filename="", page_index=0)
    hits = {
        "小数的性质": [hit("k.zero", "小数的性质")],
        "小数的意义(三)(3)": [hit("k.title", "小数的意义")],
    }
    assign_kps(page, hits, use_topics=True)
    assert page.items[0].kp_ids == ["k.zero"]
    assert page.items[3].kp_ids == ["k.title"], "没有 topic 的题回落到页面标题的检索结果"
    assert page.kp_ids[0] == "k.zero"
    assign_kps(page, hits, use_topics=False)  # 消融：只用页面标题
    assert page.items[0].kp_ids == ["k.title"]


def test_build_context_summarizes_stage_difficulty_and_kinds() -> None:
    page = flatten(raw_page(), attachment_id="a", filename="", page_index=0)
    hits = {
        "小数的性质": [hit("k.zero", "小数的性质", lesson="L2")],
        "小数的意义(三)(3)": [hit("k.title", "小数的意义", lesson="L1")],
        "x": [hit("k.sixth", "总复习", grade=6, sem="a")],
    }
    assign_kps(page, hits, use_topics=True)
    ctx = build_context([page], hits)
    assert (ctx.grade, ctx.semester) == (4, "b")
    assert ctx.kp_ids[0] == "k.zero" and "k.sixth" not in ctx.kp_ids
    assert ctx.difficulty is not None and 1 <= ctx.difficulty[0] <= ctx.difficulty[1] <= 5
    assert ctx.kinds == [ItemKind.calc, ItemKind.application]
    assert ctx.summary.startswith("四年级下册")
    assert build_context([], {}).kp_ids == []


def test_failure_message_is_teacher_language_with_next_step() -> None:
    page = PageRead(attachment_id="a", verdict=PageVerdict.not_math, reason="这是一篇英文短文")
    msg = failure_message([page])
    assert "不是数学" in msg and "英文短文" in msg and "重新拍" in msg
    assert "{" not in msg and "verdict" not in msg
    rs = assemble_set([page], StudentContext())
    assert not rs.usable and rs.message == msg


def test_assemble_set_requests_confirmation_for_doubts_or_poor_quality() -> None:
    page = flatten(raw_page(), attachment_id="a", filename="", page_index=0)
    assert assemble_set([page], StudentContext()).needs_confirm
    clean = flatten(
        RawPage.model_validate({"questions": [{"items": ["1+1="], "confidence": 0.95}]}),
        attachment_id="a",
        filename="",
        page_index=0,
    )
    assert not assemble_set([clean], StudentContext()).needs_confirm
    clean.quality = prepare(jpeg(stripes().filter(ImageFilter.GaussianBlur(10)))).quality
    assert assemble_set([clean], StudentContext()).needs_confirm, "画质差：数字可能读错，要核对"


def test_apply_edits_changes_text_removes_items_and_clears_doubts() -> None:
    page = flatten(raw_page(), attachment_id="a", filename="", page_index=0)
    rs = assemble_set([page], StudentContext())
    out = apply_edits(rs, [{"id": "r1.2", "text": "0.6060="}, {"id": "r1.3", "remove": True}])
    texts = [it.text for it in out.items]
    assert "0.6060=" in texts and "3.800=" not in texts
    assert not out.needs_confirm and not any(it.low_confidence for it in out.items)
    gone = apply_edits(rs, [{"id": it.id, "remove": True} for it in rs.items])
    assert not gone.usable and "重新拍" in gone.message


# ---- 防雷同（确定性）----
def test_copy_score_treats_same_shape_new_numbers_as_fresh() -> None:
    assert copy_score("0.70=", "0.70=") == 1.0
    assert copy_score("3.6-1.5=", "7.7-2.1=") < 0.5, "照这页再出：同类算式换数字不是翻版"
    word = "食堂运来大米10.7吨，第一个月吃掉3.3吨，第二个月吃掉3.4吨，还剩多少吨？"
    assert copy_score(word, word.replace("食堂", "食堂")) >= 0.75
    assert copy_score("粮仓运来面粉20.5吨，第一周用去4.2吨，第二周用去5.1吨，还剩多少吨？", word) < 0.75


async def test_novelty_check_rejects_copies_and_respects_the_flag(kb_service) -> None:
    word = "食堂运来大米10.7吨，第一个月吃掉3.3吨，第二个月吃掉3.4吨，还剩多少吨？"
    inp = VerifyInput(kind="application", stem=word, answers=[], solution="", references=[word])
    res = await check_novelty(inp)
    assert res.status == CheckStatus.fail and "几乎相同" in res.detail
    fresh = VerifyInput(kind="calc", stem="8.4-2.9=", references=["3.6-1.5="])
    assert (await check_novelty(fresh)).status == CheckStatus.passed
    env = VerifyEnv(llm=None, kb=kb_service)  # type: ignore[arg-type]
    full = VerifyInput(
        kind="application", stem=word, answers=[], solution="x", references=[word], solver_code=""
    )
    ver = await verify_item(env, full, FeatureFlags.parse(""))
    assert ver.status == VerifyStatus.rejected
    assert any(c.name == "novelty" and c.status == CheckStatus.fail for c in ver.checks)
    off = await verify_item(env, full, FeatureFlags.parse("perceive.novelty,produce.blind_solve"))
    assert not any(c.name == "novelty" for c in off.checks)


# ---- 阶段与管线（假视觉模型）----
VISION_OK = json.dumps(
    {
        "verdict": "worksheet",
        "title": "小数的意义(三)(3)",
        "questions": [
            {
                "no": "1",
                "instruction": "化简各数。",
                "kind": "calc",
                "topic": "小数的性质",
                "difficulty": 1,
                "confidence": 0.95,
                "items": ["0.70=", "0.6050="],
            }
        ],
    },
    ensure_ascii=False,
)
VISION_DOUBT = VISION_OK.replace(
    '"items": ["0.70=", "0.6050="]',
    '"items": ["0.70=", "0.6050="], "doubts": [{"item": 2, "note": "像 0.6050 也像 0.6060"}]',
)
VISION_NOT_MATH = json.dumps(
    {"verdict": "not_math", "reason": "这是一篇英文短文", "questions": []}, ensure_ascii=False
)


def write_photo(tmp_path: Path, name: str = "a.jpg", im: Image.Image | None = None) -> Attachment:
    path = tmp_path / name
    path.write_bytes(jpeg(im or stripes()))
    return Attachment(
        id=f"att_{name}",
        filename=name,
        mime="image/jpeg",
        size=path.stat().st_size,
        sha256="0" * 64,
        session_id="ses",
        path=str(path),
        ts=0.0,
    )


@pytest.fixture
async def make_ctx(store, kb_service, tmp_path):
    async def make(
        *scripts: list[Any], asks: list[dict[str, Any]] | None = None
    ) -> tuple[RunContext, FakeTransport, list]:
        settings = Settings(llm_mode=LLMMode.live, cassette_dir=tmp_path / "c", data_dir=tmp_path / "data")
        transport = FakeTransport(*scripts)
        asked: list[tuple[str, str, dict[str, Any]]] = []

        async def ask(kind, prompt, options, payload):  # type: ignore[no-untyped-def]
            asked.append((kind, prompt, payload))
            return asks.pop(0) if asks else {"option": "confirm"}

        ses = await store.sessions.create("t")
        ctx = RunContext(
            run_id="run_t",
            session_id=ses.id,
            settings=settings,
            llm=build_gateway(settings, transport=transport),
            kb=kb_service,
            store=store,
            ask_fn=ask,
        )
        return ctx, transport, asked

    return make


def ok_script(text: str) -> list[Any]:
    return content_chunks(text, usage=usage(1900, 300))


async def test_perceive_stage_reads_page_maps_knowledge_and_hides_image_bytes(make_ctx, tmp_path) -> None:
    ctx, transport, _ = await make_ctx(ok_script(VISION_OK))
    sink = MemorySink()
    async with use_tracer(Tracer("run_t", sink)):
        rs = await run_stage(ctx, PerceiveStage(), PerceiveIn(attachments=[write_photo(tmp_path)]))
    assert rs.usable and [it.text for it in rs.items] == ["0.70=", "0.6050="]
    assert rs.items[0].kp_ids and "小数的性质" in rs.items[0].kp_names[0]
    assert rs.context.grade == 4 and rs.context.kp_ids and not rs.needs_confirm
    # 图片只以 data URI 进请求；调用记录里换成占位
    body = transport.bodies[0]
    parts = body["messages"][1]["content"]
    assert parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    calls = [e for e in sink.events if e.type == "llm.call"]
    assert calls and "base64" not in json.dumps(calls[0].record.messages, ensure_ascii=False)  # type: ignore[attr-defined]
    assert "[图片" in json.dumps(calls[0].record.messages, ensure_ascii=False)  # type: ignore[attr-defined]
    assert body["model"] == ctx.llm.registry.role("vision").model


async def test_perceive_stage_survives_one_failed_page(make_ctx, tmp_path) -> None:
    ctx, _, _ = await make_ctx(ok_script(VISION_OK), [LLMError("boom")])
    ctx.settings.llm_max_retries = 0
    a, b = write_photo(tmp_path, "a.jpg"), write_photo(tmp_path, "b.jpg")
    async with use_tracer(Tracer("run_t", MemorySink())):
        rs = await run_stage(ctx, PerceiveStage(), PerceiveIn(attachments=[a, b]))
    verdicts = sorted(p.verdict.value for p in rs.pages)
    assert verdicts == ["unreadable", "worksheet"] and rs.usable
    assert "暂时不可用" in next(p.reason for p in rs.pages if not p.verdict.usable)
    assert "没能识别" in rs.message


async def test_perceive_stage_rejects_extreme_images_without_calling_the_model(make_ctx, tmp_path) -> None:
    ctx, transport, _ = await make_ctx(ok_script(VISION_OK))
    black = write_photo(tmp_path, "black.jpg", Image.new("RGB", (800, 1000), (4, 4, 4)))
    async with use_tracer(Tracer("run_t", MemorySink())):
        rs = await run_stage(ctx, PerceiveStage(), PerceiveIn(attachments=[black]))
    assert not rs.usable and "全黑" in rs.message and transport.bodies == []


async def test_non_math_photo_gets_a_friendly_reply_and_no_questions(make_ctx, tmp_path) -> None:
    ctx, _, _ = await make_ctx(ok_script(VISION_NOT_MATH))
    sink = MemorySink()
    async with use_tracer(Tracer("run_t", sink)):
        res = await main_pipeline(ctx, TurnInput(text="", attachments=[write_photo(tmp_path)]))
    assert "不是数学" in res.reply_text and "重新拍" in res.reply_text
    assert not any(e.type in ("item.delivered", "understanding.ready") for e in sink.events)
    ready = [e for e in sink.events if isinstance(e, PerceptionReady)]
    assert len(ready) == 1 and not ready[0].references.usable


async def test_doubtful_transcription_pauses_for_confirmation_and_applies_edits(make_ctx, tmp_path) -> None:
    edits = [{"option": "confirm", "items": [{"id": "r1.2", "text": "0.6060="}]}]
    ctx, _, asked = await make_ctx(ok_script(VISION_DOUBT), asks=edits)
    sink = MemorySink()
    async with use_tracer(Tracer("run_t", sink)):
        rs = await perceive_turn(ctx, TurnInput(text="", attachments=[write_photo(tmp_path)]))
    assert asked and asked[0][0] == "perception" and "没能完全看清" in asked[0][1]
    assert asked[0][2]["references"]["pages"][0]["items"][1]["uncertain"]
    assert rs is not None and [it.text for it in rs.items] == ["0.70=", "0.6060="]
    assert not rs.needs_confirm
    ready = [e for e in sink.events if isinstance(e, PerceptionReady)]
    assert len(ready) == 2, "识别后一次，确认修改后再一次"


async def test_confirmation_can_be_turned_off(make_ctx, tmp_path) -> None:
    ctx, _, asked = await make_ctx(ok_script(VISION_DOUBT))
    ctx.settings = Settings(
        llm_mode=LLMMode.live, cassette_dir=tmp_path / "c", data_dir=tmp_path / "data", off="perceive.confirm"
    )
    async with use_tracer(Tracer("run_t", MemorySink())):
        rs = await perceive_turn(ctx, TurnInput(text="", attachments=[write_photo(tmp_path)]))
    assert not asked and rs is not None and rs.needs_confirm


async def test_no_attachments_means_no_perception(make_ctx) -> None:
    ctx, transport, _ = await make_ctx(ok_script(VISION_OK))
    assert await perceive_turn(ctx, TurnInput(text="出 5 道题")) is None and transport.bodies == []


# ---- 照片 → Brief ----
async def photo_context(kb_service) -> StudentContext:
    hits = await kb_service.search("小数的性质", k=3)
    top = hits[0]
    lesson = await kb_service.latest_lesson([h.lesson_id for h in hits if h.lesson_id])
    return StudentContext(
        grade=top.grade,
        semester=top.semester if top.semester in ("a", "b") else None,
        lesson_id=lesson,
        kp_ids=[top.id],
        kp_names=[top.name],
        difficulty=[2, 3],
        kinds=[ItemKind.calc],
        summary=f"四年级下册 · {top.name}",
    )


async def test_photo_supplies_scope_when_the_teacher_names_none(kb_service) -> None:
    photo = await photo_context(kb_service)
    u = await finalize(
        RawParse(route=Route.generate, count=5, explicit=["count"]), hits=[], kb=kb_service, photo=photo
    )
    b = u.brief
    assert b is not None and u.clarify is None, "照片提供范围：不再追问年级"
    assert (
        b.scope.kp_ids and b.scope.kp_ids.value == photo.kp_ids and b.scope.kp_ids.origin == Origin.inferred
    )
    assert b.scope.grade and b.scope.grade.origin == Origin.inferred
    assert b.target_lesson and b.target_lesson.value == photo.lesson_id
    assert b.difficulty and b.difficulty.value == [2, 3] and b.difficulty.origin == Origin.inferred
    assert b.kinds and b.kinds.value == [ItemKind.calc]
    assert any("照片" in a for a in b.assumptions)
    assert b.tier_mix and b.tier_mix.origin == Origin.inferred and b.tier_mix.value[Tier.integrated] == 0.2


async def test_teacher_words_beat_the_photo(kb_service) -> None:
    photo = await photo_context(kb_service)
    raw = RawParse(
        route=Route.generate,
        grade=2,
        difficulty=[4, 5],
        kinds=[ItemKind.choice],
        explicit=["grade", "difficulty", "kinds"],
    )
    u = await finalize(raw, hits=[], kb=kb_service, photo=photo)
    b = u.brief
    assert b is not None
    assert b.scope.grade and b.scope.grade.value == 2 and b.scope.grade.origin == Origin.user
    assert not b.scope.kp_ids, "用户明说了年级：范围由用户定，不套用照片的知识点"
    assert b.difficulty and b.difficulty.value == [4, 5] and b.difficulty.origin == Origin.user
    assert b.kinds and b.kinds.value == [ItemKind.choice]


async def test_without_photo_or_scope_the_teacher_is_still_asked(kb_service) -> None:
    u = await finalize(RawParse(route=Route.generate, count=5), hits=[], kb=kb_service)
    assert u.clarify is not None


def test_reference_item_round_trips_from_perceived_item() -> None:
    it = PerceivedItem(
        id="r1.1", attachment_id="att_1", no="2", instruction="化简各数。", text="0.70=", kp_ids=["k"]
    )
    ref = it.reference()
    assert ref.source == "photo:att_1#2" and ref.instruction == "化简各数。" and ref.kp_ids == ["k"]
    rs = ReferenceSet(pages=[PageRead(attachment_id="att_1", verdict=PageVerdict.worksheet, items=[it])])
    assert [r.text for r in rs.references()] == ["0.70="]

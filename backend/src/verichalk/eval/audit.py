"""审计：独立于线上核验的"裁判"（eval/specs/produce.md §2.3）。

端到端评测里 A2 / A3 / A6 不能用线上核验自己的结论打分（循环论证），所以审计走另一条路：
- A2 答案正确：两个**不同厂商**的思考模型各自独立解题，两者都与题目答案一致 → `correct`；两者一致且不同于题目答案 → `wrong`；
  其余 → `disputed`（单列，由人工复核）；
- A3 不超纲：chalkbase 自带的特征抽取（不同模型、不同提示）+ `check_item`；
- A6 题面质量：与线上同一份评审标准，但由更强的、不同于线上评审的模型执行。
审计结果缓存在 `eval/audits/*.jsonl`（随仓库提交），因此回放评测确定且无需网络；缓存键包含审计配置，换模型会重算。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.config import Settings
from ..core.errors import VerichalkError
from ..domain.blueprint import AnswerPart
from ..knowledge import KnowledgeService
from ..llm import LLMGateway, build_gateway
from ..verify import VerifyEnv, VerifyInput
from ..verify.answers import answers_equal
from ..verify.checks import check_blind, check_quality

log = logging.getLogger("verichalk.audit")

# 审计配置：改动任何一项都会使缓存失效。
AUDITOR_A = {"model": "qwen3.8-max", "thinking": True, "temperature": 0.0, "max_tokens": 8000}
AUDITOR_B = {"model": "deepseek-v4-pro", "thinking": True, "temperature": 0.0, "max_tokens": 8000}
JUDGE = {"model": "deepseek-v4-pro", "thinking": False, "temperature": 0.0, "max_tokens": 900}
AUDIT_VERSION = 1


@dataclass
class AuditItem:
    stem: str
    options: list[str]
    answers: list[str]
    solution: str
    kind: str
    grade: int | None
    lesson_id: str | None
    kp_names: list[str] = field(default_factory=list)
    tier: str = "consolidate"

    @property
    def key(self) -> str:
        raw = json.dumps(
            [self.stem, self.options, self.answers, self.solution, self.lesson_id], ensure_ascii=False
        )
        cfg = json.dumps([AUDIT_VERSION, AUDITOR_A, AUDITOR_B, JUDGE], sort_keys=True)
        return hashlib.sha1((raw + cfg).encode()).hexdigest()[:16]


@dataclass
class AuditRecord:
    key: str
    a2: str = "unavailable"  # correct / wrong / disputed / unavailable
    answers_a: list[str] = field(default_factory=list)
    answers_b: list[str] = field(default_factory=list)
    a3: str = ""  # in / borderline / out / ""（不可用）
    a3_violations: list[dict[str, Any]] = field(default_factory=list)
    quality: dict[str, Any] = field(default_factory=dict)  # 判官对各项的结论

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False)

    @classmethod
    def from_json(cls, line: str) -> AuditRecord:
        return cls(**json.loads(line))


class AuditStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.records: dict[str, AuditRecord] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = AuditRecord.from_json(line)
                    self.records[r.key] = r

    def add(self, rec: AuditRecord) -> None:
        self.records[rec.key] = rec
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(rec.to_json() + "\n")


def _gateway(settings: Settings, spec: dict[str, Any], role: str) -> LLMGateway:
    gw = build_gateway(settings)
    gw.registry = gw.registry.override(role, **spec)
    return gw


class Auditor:
    def __init__(
        self, settings: Settings, kb: KnowledgeService, store: AuditStore, *, concurrency: int = 20
    ) -> None:
        s = settings.model_copy(update={"cassette_namespace": "audit"})
        self.kb, self.store = kb, store
        self.gw_a = _gateway(s, AUDITOR_A, "solver")
        self.gw_b = _gateway(s, AUDITOR_B, "solver")
        self.gw_j = _gateway(s, JUDGE, "judge")
        self.sem = asyncio.Semaphore(concurrency)
        self.live = settings.llm_mode.value != "replay"

    async def audit(self, it: AuditItem) -> AuditRecord:
        cached = self.store.records.get(it.key)
        if cached is not None:
            return cached
        if not self.live:
            return AuditRecord(key=it.key)  # 回放模式下没有缓存：未审计
        async with self.sem:
            rec = await self._audit(it)
        self.store.add(rec)
        return rec

    async def _audit(self, it: AuditItem) -> AuditRecord:
        inp = VerifyInput(
            kind=it.kind,
            stem=it.stem,
            options=it.options,
            answers=[AnswerPart(value=a) for a in it.answers],
            solution=it.solution,
            kp_names=it.kp_names,
            tier=it.tier,
            grade=it.grade,
        )
        rec = AuditRecord(key=it.key)
        env_a, env_b, env_j = (VerifyEnv(llm=g, kb=self.kb) for g in (self.gw_a, self.gw_b, self.gw_j))
        text = "\n".join([it.stem, *it.options, it.solution])

        async def boundary() -> tuple[str, list[dict[str, Any]]]:
            if not it.lesson_id:
                return "", []
            try:
                rep = await self.kb.audit_boundary(text, it.lesson_id)
                return rep.verdict, [v.model_dump() for v in rep.violations]
            except VerichalkError as e:
                log.warning("audit boundary failed: %s", e.code)
                return "", []

        (ra, rb, (q, _), (verdict, viol)) = await asyncio.gather(
            check_blind(env_a, inp), check_blind(env_b, inp), check_quality(env_j, inp), boundary()
        )
        rec.answers_a = list(ra.evidence.get("blind", []))
        rec.answers_b = list(rb.evidence.get("blind", []))
        if ra.status.value == "skip" or rb.status.value == "skip":
            rec.a2 = "unavailable"
        else:
            a_ok = answers_equal(rec.answers_a, it.answers)
            b_ok = answers_equal(rec.answers_b, it.answers)
            if a_ok and b_ok:
                rec.a2 = "correct"
            elif not a_ok and not b_ok and answers_equal(rec.answers_a, rec.answers_b):
                rec.a2 = "wrong"
            else:
                rec.a2 = "disputed"
        rec.a3, rec.a3_violations = verdict, viol
        rec.quality = {k: v for k, v in q.evidence.items() if k != "model"}
        return rec

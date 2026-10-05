"""仓库（Repository）：面向领域模型的存取接口，SQLite 实现。

约定：时间一律 epoch 秒（float）；复杂字段存 JSON 文本；事件按 (run_id, seq) 追加，永不更新。
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from ..core.errors import Conflict, NotFound
from ..core.ids import new_id
from ..domain.events import Event, EventAdapter
from ..domain.paper import Paper, Revision
from ..domain.run import Attachment, Message, Run, Session
from .db import Database


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


class SessionRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(self, title: str = "") -> Session:
        now = time.time()
        s = Session(id=new_id("ses"), created_at=now, updated_at=now, title=title)
        await self.db.execute(
            "INSERT INTO sessions(id, created_at, updated_at, title) VALUES (?,?,?,?)",
            (s.id, s.created_at, s.updated_at, s.title),
        )
        return s

    async def get(self, session_id: str) -> Session:
        row = await self.db.fetchone("SELECT * FROM sessions WHERE id=?", (session_id,))
        if row is None:
            raise NotFound(f"会话不存在：{session_id}")
        return Session(**dict(row))

    async def touch(self, session_id: str, title: str | None = None) -> None:
        if title is None:
            await self.db.execute("UPDATE sessions SET updated_at=? WHERE id=?", (time.time(), session_id))
        else:
            await self.db.execute(
                "UPDATE sessions SET updated_at=?, title=? WHERE id=?", (time.time(), title, session_id)
            )


class MessageRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(self, m: Message) -> Message:
        await self.db.execute(
            "INSERT INTO messages(id, session_id, role, content, attachments, run_id, ts) VALUES (?,?,?,?,?,?,?)",
            (
                m.id,
                m.session_id,
                m.role.value,
                m.content,
                _dumps([a.model_dump() for a in m.attachments]),
                m.run_id,
                m.ts,
            ),
        )
        return m

    async def list(self, session_id: str) -> list[Message]:
        rows = await self.db.fetchall(
            "SELECT * FROM messages WHERE session_id=? ORDER BY ts, id", (session_id,)
        )
        return [Message(**{**dict(r), "attachments": json.loads(r["attachments"])}) for r in rows]


def _run_from_row(r: sqlite3.Row) -> Run:
    d = dict(r)
    d["error"] = json.loads(d["error"]) if d["error"] else None
    d["tags"] = json.loads(d["tags"])
    d["state"] = json.loads(d["state"])
    d["checkpoint"] = json.loads(d["checkpoint"]) if d["checkpoint"] else None
    return Run(**d)


class RunRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(self, run: Run) -> Run:
        await self.db.execute(
            "INSERT INTO runs(id, session_id, message_id, pipeline, status, created_at, started_at, finished_at,"
            " error, tags, state, checkpoint) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                run.id,
                run.session_id,
                run.message_id,
                run.pipeline,
                run.status.value,
                run.created_at,
                run.started_at,
                run.finished_at,
                _dumps(run.error.model_dump()) if run.error else None,
                _dumps(run.tags),
                _dumps(run.state),
                _dumps(run.checkpoint) if run.checkpoint else None,
            ),
        )
        return run

    async def get(self, run_id: str) -> Run:
        row = await self.db.fetchone("SELECT * FROM runs WHERE id=?", (run_id,))
        if row is None:
            raise NotFound(f"运行不存在：{run_id}")
        return _run_from_row(row)

    async def save(self, run: Run) -> None:
        await self.db.execute(
            "UPDATE runs SET status=?, started_at=?, finished_at=?, error=?, tags=?, state=?, checkpoint=? WHERE id=?",
            (
                run.status.value,
                run.started_at,
                run.finished_at,
                _dumps(run.error.model_dump()) if run.error else None,
                _dumps(run.tags),
                _dumps(run.state),
                _dumps(run.checkpoint) if run.checkpoint else None,
                run.id,
            ),
        )

    async def list(
        self, *, session_id: str | None = None, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[Run]:
        where, params = [], []
        if session_id:
            where.append("session_id=?")
            params.append(session_id)
        if status:
            where.append("status=?")
            params.append(status)
        sql = "SELECT * FROM runs" + (" WHERE " + " AND ".join(where) if where else "")
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        rows = await self.db.fetchall(sql, (*params, limit, offset))
        return [_run_from_row(r) for r in rows]

    async def mark_interrupted(self) -> int:
        """服务启动时调用：遗留的 created / running / awaiting_user 运行置为 failed(interrupted)。"""
        err = _dumps(
            {
                "code": "interrupted",
                "message": "服务重启导致运行中断",
                "retryable": True,
                "user_message": "服务刚刚重启，这次任务被中断了，请重新发起。",
                "details": {},
            }
        )

        def _do(c: sqlite3.Connection) -> int:
            cur = c.execute(
                "UPDATE runs SET status='failed', finished_at=?, error=? WHERE status IN ('created','running','awaiting_user')",
                (time.time(), err),
            )
            return cur.rowcount

        return await self.db.run(_do)


class EventRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def append(
        self,
        run_id: str,
        seq: int,
        ts: float,
        type_: str,
        span_id: str | None,
        parent_id: str | None,
        visibility: str,
        json_text: str,
    ) -> None:
        row = (run_id, seq, ts, type_, span_id, parent_id, visibility, json_text)
        # 高频追加：延迟合并提交（同连接内读取立即可见；见 Database.run）
        await self.db.run(
            lambda c: c.execute(
                "INSERT INTO events(run_id, seq, ts, type, span_id, parent_id, visibility, json) VALUES (?,?,?,?,?,?,?,?)",
                row,
            ),
            defer_commit=True,
        )

    async def append_many(self, rows: list[tuple[Any, ...]]) -> None:
        def _do(c: sqlite3.Connection) -> None:
            c.executemany(
                "INSERT INTO events(run_id, seq, ts, type, span_id, parent_id, visibility, json) VALUES (?,?,?,?,?,?,?,?)",
                rows,
            )

        await self.db.run(_do)

    async def list(
        self, run_id: str, *, after_seq: int = 0, visibility: str | None = None, limit: int | None = None
    ) -> list[Event]:
        sql = "SELECT json FROM events WHERE run_id=? AND seq>?"
        params: list[Any] = [run_id, after_seq]
        if visibility:
            sql += " AND visibility=?"
            params.append(visibility)
        sql += " ORDER BY seq"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        rows = await self.db.fetchall(sql, tuple(params))
        return [EventAdapter.validate_json(r["json"]) for r in rows]

    async def last_seq(self, run_id: str) -> int:
        row = await self.db.fetchone("SELECT COALESCE(MAX(seq),0) AS m FROM events WHERE run_id=?", (run_id,))
        return int(row["m"]) if row else 0

    async def count(self, run_id: str) -> int:
        row = await self.db.fetchone("SELECT COUNT(*) AS n FROM events WHERE run_id=?", (run_id,))
        return int(row["n"]) if row else 0


class PaperRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def get_current(self, session_id: str) -> Paper | None:
        row = await self.db.fetchone("SELECT snapshot FROM papers WHERE session_id=?", (session_id,))
        return Paper.model_validate_json(row["snapshot"]) if row else None

    async def save(self, session_id: str, paper: Paper, rev: Revision) -> None:
        """原子地写入当前试卷与一条修订；`paper.rev` 必须等于当前 rev + 1（乐观并发）。"""
        snapshot = paper.model_dump_json()

        def _do(c: sqlite3.Connection) -> None:
            cur = c.execute("SELECT rev FROM papers WHERE session_id=?", (session_id,)).fetchone()
            current = cur["rev"] if cur else 0
            if paper.rev != current + 1:
                raise Conflict(f"试卷版本冲突：当前 {current}，提交 {paper.rev}")
            c.execute(
                "INSERT INTO revisions(session_id, rev, author, run_id, ts, patch, summary, snapshot) VALUES (?,?,?,?,?,?,?,?)",
                (
                    session_id,
                    paper.rev,
                    rev.author,
                    rev.run_id,
                    rev.ts,
                    _dumps(rev.patch),
                    rev.summary,
                    snapshot,
                ),
            )
            c.execute(
                "INSERT INTO papers(session_id, paper_id, rev, snapshot, updated_at) VALUES (?,?,?,?,?) "
                "ON CONFLICT(session_id) DO UPDATE SET paper_id=excluded.paper_id, rev=excluded.rev,"
                " snapshot=excluded.snapshot, updated_at=excluded.updated_at",
                (session_id, paper.id, paper.rev, snapshot, rev.ts),
            )

        await self.db.run(_do)

    async def get_revision(self, session_id: str, rev: int) -> Paper:
        row = await self.db.fetchone(
            "SELECT snapshot FROM revisions WHERE session_id=? AND rev=?", (session_id, rev)
        )
        if row is None:
            raise NotFound(f"修订不存在：{rev}")
        return Paper.model_validate_json(row["snapshot"])

    async def list_revisions(self, session_id: str) -> list[Revision]:
        rows = await self.db.fetchall(
            "SELECT rev, author, run_id, ts, patch, summary FROM revisions WHERE session_id=? ORDER BY rev",
            (session_id,),
        )
        return [
            Revision(
                paper_id="",
                rev=r["rev"],
                author=r["author"],
                run_id=r["run_id"],
                ts=r["ts"],
                patch=json.loads(r["patch"]),
                summary=r["summary"],
            )
            for r in rows
        ]


class AttachmentRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(self, a: Attachment) -> Attachment:
        await self.db.execute(
            "INSERT INTO attachments(id, session_id, filename, mime, size, sha256, path, ts) VALUES (?,?,?,?,?,?,?,?)",
            (a.id, a.session_id, a.filename, a.mime, a.size, a.sha256, a.path, a.ts),
        )
        return a

    async def get(self, attachment_id: str) -> Attachment:
        row = await self.db.fetchone("SELECT * FROM attachments WHERE id=?", (attachment_id,))
        if row is None:
            raise NotFound(f"附件不存在：{attachment_id}")
        return Attachment(**dict(row))


class Store:
    """聚合入口：`store.sessions / messages / runs / events / papers / attachments`。"""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.sessions = SessionRepo(db)
        self.messages = MessageRepo(db)
        self.runs = RunRepo(db)
        self.events = EventRepo(db)
        self.papers = PaperRepo(db)
        self.attachments = AttachmentRepo(db)

    @classmethod
    async def open(cls, path: Any) -> Store:
        db = Database(path)
        await db.open()
        return cls(db)

    async def close(self) -> None:
        await self.db.close()

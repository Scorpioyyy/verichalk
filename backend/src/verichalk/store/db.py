"""SQLite 访问：单工作线程 + WAL。

所有读写都在同一个专用线程里串行执行：避免 SQLite 的多线程锁争用，也让"追加事件"的顺序与调用顺序一致。
本期的并发量（单机、单实例）远低于它的吞吐上限；换 Postgres 时只需新增一个实现（D12）。
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")

SCHEMA_VERSION = 1
COMMIT_DELAY_S = 0.05

_DDL = """
CREATE TABLE IF NOT EXISTS sessions(
  id TEXT PRIMARY KEY, created_at REAL NOT NULL, updated_at REAL NOT NULL, title TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS messages(
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
  attachments TEXT NOT NULL DEFAULT '[]', run_id TEXT, ts REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, ts);
CREATE TABLE IF NOT EXISTS runs(
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL, message_id TEXT, pipeline TEXT NOT NULL,
  status TEXT NOT NULL, created_at REAL NOT NULL, started_at REAL, finished_at REAL,
  error TEXT, tags TEXT NOT NULL DEFAULT '[]', state TEXT NOT NULL DEFAULT '{}', checkpoint TEXT);
CREATE INDEX IF NOT EXISTS idx_runs_session ON runs(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at);
CREATE TABLE IF NOT EXISTS events(
  run_id TEXT NOT NULL, seq INTEGER NOT NULL, ts REAL NOT NULL, type TEXT NOT NULL,
  span_id TEXT, parent_id TEXT, visibility TEXT NOT NULL, json TEXT NOT NULL,
  PRIMARY KEY(run_id, seq)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS papers(
  session_id TEXT PRIMARY KEY, paper_id TEXT NOT NULL, rev INTEGER NOT NULL,
  snapshot TEXT NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS revisions(
  session_id TEXT NOT NULL, rev INTEGER NOT NULL, author TEXT NOT NULL, run_id TEXT, ts REAL NOT NULL,
  patch TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '', snapshot TEXT NOT NULL,
  PRIMARY KEY(session_id, rev));
CREATE TABLE IF NOT EXISTS attachments(
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL, filename TEXT NOT NULL, mime TEXT NOT NULL,
  size INTEGER NOT NULL, sha256 TEXT NOT NULL, path TEXT NOT NULL, ts REAL NOT NULL);
"""


class SchemaMismatch(RuntimeError):
    pass


class Database:
    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sqlite")
        self._conn: sqlite3.Connection | None = None
        self._dirty = False  # 仅在数据库线程内读写
        self._flush_scheduled = False
        self._flush_task: asyncio.Future[None] | None = None
        self._closed = False

    async def open(self) -> None:
        await asyncio.get_running_loop().run_in_executor(self._pool, self._init)

    def _init(self) -> None:
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, SCHEMA_VERSION):
            conn.close()
            raise SchemaMismatch(f"数据库 schema 版本 {version} 与代码 {SCHEMA_VERSION} 不一致")
        conn.executescript(_DDL)
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.commit()
        self._conn = conn

    async def run(self, fn: Callable[[sqlite3.Connection], T], *, defer_commit: bool = False) -> T:
        """在数据库线程里执行 `fn(conn)`，成功则提交，异常则回滚。

        `defer_commit=True`（用于高频追加的事件）：不立即提交，最多 `COMMIT_DELAY_S` 后合并提交一次。
        同一连接内读写互相可见；任何非延迟操作会先提交挂起的写入，因此回滚不会连带丢失已追加的事件。
        """
        loop = asyncio.get_running_loop()

        def _call() -> T:
            conn = self._conn
            if conn is None:
                raise RuntimeError("数据库未打开或已关闭")
            if self._dirty and not defer_commit:
                conn.commit()
                self._dirty = False
            try:
                out = fn(conn)
            except BaseException:
                if not defer_commit:  # 失败的单条语句本身是原子的，延迟写入的事务保持打开
                    conn.rollback()
                raise
            if defer_commit:
                self._dirty = True
            else:
                conn.commit()
            return out

        out = await loop.run_in_executor(self._pool, _call)
        if defer_commit and not self._flush_scheduled:
            self._flush_scheduled = True
            loop.call_later(COMMIT_DELAY_S, self._schedule_flush)
        return out

    def _schedule_flush(self) -> None:
        if not self._closed:
            self._flush_task = asyncio.ensure_future(self._flush())

    async def _flush(self) -> None:
        self._flush_scheduled = False
        if self._closed:
            return
        await asyncio.get_running_loop().run_in_executor(self._pool, self._commit_if_dirty)

    def _commit_if_dirty(self) -> None:
        if self._conn is not None and self._dirty:
            self._conn.commit()
            self._dirty = False

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        await self.run(lambda c: c.execute(sql, params))

    async def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        return await self.run(lambda c: c.execute(sql, params).fetchall())

    async def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        return await self.run(lambda c: c.execute(sql, params).fetchone())

    async def close(self) -> None:
        self._closed = True  # 之后挂起的定时提交不再提交新任务；关闭时统一提交
        if self._flush_task is not None:
            await asyncio.gather(self._flush_task, return_exceptions=True)

        def _close() -> None:
            self._commit_if_dirty()
            if self._conn is not None:
                self._conn.close()
                self._conn = None

        await asyncio.get_running_loop().run_in_executor(self._pool, _close)
        self._pool.shutdown(wait=True)

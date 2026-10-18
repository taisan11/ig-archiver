from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


class ArchiveDB:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def _init(self) -> None:
        with self.connect() as con:
            con.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS seen (
                    kind TEXT NOT NULL,
                    item_key TEXT NOT NULL,
                    username TEXT,
                    archived_at TEXT NOT NULL,
                    path TEXT,
                    PRIMARY KEY (kind, item_key)
                );
                CREATE TABLE IF NOT EXISTS state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS targets (
                    username TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    resolved_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    detail TEXT
                );
                """
            )

    def has_seen(self, kind: str, item_key: str | int) -> bool:
        with self.connect() as con:
            row = con.execute(
                "SELECT 1 FROM seen WHERE kind=? AND item_key=?",
                (kind, str(item_key)),
            ).fetchone()
        return row is not None

    def mark_seen(self, kind: str, item_key: str | int, username: str | None, path: Path | None) -> None:
        now = datetime.now(UTC).isoformat()
        with self.connect() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO seen(kind,item_key,username,archived_at,path)
                VALUES(?,?,?,?,?)
                """,
                (kind, str(item_key), username, now, str(path) if path else None),
            )

    def get_state(self, key: str) -> str | None:
        with self.connect() as con:
            row = con.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def set_state(self, key: str, value: str | int) -> None:
        now = datetime.now(UTC).isoformat()
        with self.connect() as con:
            con.execute(
                """
                INSERT INTO state(key,value,updated_at) VALUES(?,?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, str(value), now),
            )

    def get_target_id(self, username: str) -> str | None:
        with self.connect() as con:
            row = con.execute("SELECT user_id FROM targets WHERE username=?", (username,)).fetchone()
        return None if row is None else str(row["user_id"])

    def set_target_id(self, username: str, user_id: str | int) -> None:
        now = datetime.now(UTC).isoformat()
        with self.connect() as con:
            con.execute(
                """
                INSERT INTO targets(username,user_id,resolved_at) VALUES(?,?,?)
                ON CONFLICT(username) DO UPDATE SET user_id=excluded.user_id, resolved_at=excluded.resolved_at
                """,
                (username, str(user_id), now),
            )

    def record_run(self, job: str, started: datetime, success: bool, detail: str = "") -> None:
        ended = datetime.now(UTC)
        with self.connect() as con:
            con.execute(
                "INSERT INTO runs(job,started_at,ended_at,success,detail) VALUES(?,?,?,?,?)",
                (job, started.isoformat(), ended.isoformat(), 1 if success else 0, detail[:2000]),
            )

    def latest_runs(self) -> list[sqlite3.Row]:
        with self.connect() as con:
            return list(
                con.execute(
                    """
                    SELECT r.* FROM runs r
                    JOIN (SELECT job, MAX(id) id FROM runs GROUP BY job) x ON r.id=x.id
                    ORDER BY r.job
                    """
                ).fetchall()
            )

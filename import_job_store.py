"""Persisted background jobs for long-running Web imports."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Optional


class ImportLeaseLost(RuntimeError):
    pass


class ImportOutcomeUnknown(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ImportJobStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=15.0)
        try:
            with connection:
                self._initialize(connection)
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _initialize(connection: sqlite3.Connection) -> None:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA busy_timeout=15000")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS import_jobs (
                   id TEXT PRIMARY KEY,
                   kind TEXT NOT NULL,
                   username TEXT NOT NULL,
                   status TEXT NOT NULL,
                   payload_json TEXT NOT NULL,
                   result_json TEXT,
                   error TEXT,
                   attempts INTEGER NOT NULL DEFAULT 0,
                   created_at TEXT NOT NULL,
                   updated_at TEXT NOT NULL,
                   started_at TEXT,
                   completed_at TEXT
               )"""
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS import_jobs_status_created ON import_jobs(status, created_at)"
        )
        connection.execute('''CREATE TABLE IF NOT EXISTS import_external_steps (
            job_id TEXT NOT NULL, step_key TEXT NOT NULL, attempt INTEGER NOT NULL,
            status TEXT NOT NULL, result_json TEXT,
            PRIMARY KEY(job_id, step_key))''')
        connection.execute('''CREATE TABLE IF NOT EXISTS import_checkpoints (
            job_id TEXT NOT NULL, checkpoint_key TEXT NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY(job_id, checkpoint_key))''')
        connection.commit()

    @staticmethod
    def _public(row: sqlite3.Row) -> Dict[str, Any]:
        result = json.loads(row["result_json"]) if row["result_json"] else None
        return {
            "job_id": row["id"],
            "kind": row["kind"],
            "username": row["username"],
            "status": row["status"],
            "result": result,
            "error": row["error"],
            "attempts": int(row["attempts"] or 0),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
        }

    def enqueue(self, kind: str, username: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        job_id = uuid.uuid4().hex
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO import_jobs(
                       id, kind, username, status, payload_json, created_at, updated_at
                   ) VALUES(?, ?, ?, 'queued', ?, ?, ?)""",
                (
                    job_id,
                    kind,
                    username,
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM import_jobs WHERE id=?", (job_id,)
            ).fetchone()
            return self._public(row)

    def get(self, job_id: str, username: Optional[str] = None) -> Optional[Dict[str, Any]]:
        with self._connect() as connection:
            if username is None:
                row = connection.execute(
                    "SELECT * FROM import_jobs WHERE id=?", (job_id,)
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM import_jobs WHERE id=? AND username=?",
                    (job_id, username),
                ).fetchone()
            return self._public(row) if row else None

    def claim_next(self) -> Optional[Dict[str, Any]]:
        stale_before = (
            datetime.now(timezone.utc) - timedelta(minutes=20)
        ).isoformat(timespec="seconds")
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            # A lost response may already have incurred a provider charge. Never
            # replay an unfinished paid request automatically after a crash.
            connection.execute(
                """UPDATE import_jobs SET status='failed', completed_at=?, updated_at=?,
                       error='外部请求结果未确认，已停止自动重试，请管理员核对后再处理'
                   WHERE status='running' AND updated_at < ? AND EXISTS (
                       SELECT 1 FROM import_external_steps steps
                       WHERE steps.job_id=import_jobs.id AND steps.status='started')""",
                (now, now, stale_before),
            )
            connection.execute(
                """UPDATE import_jobs SET status='queued', updated_at=?, started_at=NULL
                   WHERE status='running' AND updated_at < ?""",
                (now, stale_before),
            )
            row = connection.execute(
                """SELECT * FROM import_jobs
                   WHERE status='queued' ORDER BY created_at LIMIT 1"""
            ).fetchone()
            if not row:
                connection.commit()
                return None
            changed = connection.execute(
                """UPDATE import_jobs SET
                       status='running', attempts=attempts+1,
                       started_at=?, updated_at=?
                   WHERE id=? AND status='queued'""",
                (now, now, row["id"]),
            ).rowcount
            if changed != 1:
                connection.rollback()
                return None
            connection.commit()
            claimed = connection.execute(
                "SELECT * FROM import_jobs WHERE id=?", (row["id"],)
            ).fetchone()
            result = self._public(claimed)
            result["payload"] = json.loads(claimed["payload_json"])
            return result

    @staticmethod
    def _require_owner(connection: sqlite3.Connection, job_id: str, attempt: int) -> None:
        if not connection.execute(
            "SELECT 1 FROM import_jobs WHERE id=? AND status='running' AND attempts=?",
            (job_id, attempt),
        ).fetchone():
            raise ImportLeaseLost('导入任务已失去执行租约')

    def require_owner(self, job_id: str, attempt: int) -> None:
        with self._connect() as connection:
            self._require_owner(connection, job_id, attempt)

    def external_step(self, job_id: str, attempt: int, step_key: str,
                      execute: Callable[[], Any]) -> Any:
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            self._require_owner(connection, job_id, attempt)
            row = connection.execute(
                'SELECT status, result_json FROM import_external_steps WHERE job_id=? AND step_key=?',
                (job_id, step_key),
            ).fetchone()
            if row:
                if row['status'] == 'completed':
                    return json.loads(row['result_json'])
                raise ImportOutcomeUnknown('外部请求结果未确认，不能自动重复调用')
            connection.execute(
                "INSERT INTO import_external_steps(job_id, step_key, attempt, status) VALUES(?,?,?,'started')",
                (job_id, step_key, attempt),
            )
        result = execute()
        encoded = json.dumps(result, ensure_ascii=False, separators=(',', ':'))
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            self._require_owner(connection, job_id, attempt)
            connection.execute(
                """UPDATE import_external_steps SET status='completed', result_json=?
                   WHERE job_id=? AND step_key=? AND attempt=? AND status='started'""",
                (encoded, job_id, step_key, attempt),
            )
        return result

    def renew_lease(self, job_id: str, attempt: int) -> bool:
        with self._connect() as connection:
            changed = connection.execute(
                """UPDATE import_jobs SET updated_at=?
                   WHERE id=? AND status='running' AND attempts=?""",
                (_now(), job_id, attempt),
            ).rowcount
            return changed == 1

    def checkpoint(self, job_id: str, attempt: int, key: str, payload: dict) -> None:
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            self._require_owner(connection, job_id, attempt)
            connection.execute(
                'INSERT OR IGNORE INTO import_checkpoints(job_id, checkpoint_key, payload) VALUES(?,?,?)',
                (job_id, key, json.dumps(payload, ensure_ascii=False)),
            )

    def checkpoints(self, job_id: str, attempt: int) -> list[dict]:
        with self._connect() as connection:
            self._require_owner(connection, job_id, attempt)
            return [json.loads(row[0]) for row in connection.execute(
                'SELECT payload FROM import_checkpoints WHERE job_id=? ORDER BY checkpoint_key', (job_id,),
            )]

    def complete(self, job_id: str, result: Dict[str, Any], *, attempt: int) -> bool:
        now = _now()
        with self._connect() as connection:
            changed = connection.execute(
                """UPDATE import_jobs SET
                       status='succeeded', result_json=?, error=NULL,
                       completed_at=?, updated_at=?
                   WHERE id=? AND status='running' AND attempts=?""",
                (
                    json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                    now,
                    now,
                    job_id,
                    attempt,
                ),
            ).rowcount
            return changed == 1

    def fail(self, job_id: str, error: str, *, attempt: int) -> bool:
        now = _now()
        with self._connect() as connection:
            changed = connection.execute(
                """UPDATE import_jobs SET
                       status='failed', error=?, completed_at=?, updated_at=?
                   WHERE id=? AND status='running' AND attempts=?""",
                (str(error or "导入失败")[:1000], now, now, job_id, attempt),
            ).rowcount
            return changed == 1

    def cleanup(self, retention_days: int = 14) -> int:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=max(1, retention_days))
        ).isoformat(timespec="seconds")
        with self._connect() as connection:
            connection.execute(
                """DELETE FROM import_checkpoints WHERE job_id IN (
                    SELECT id FROM import_jobs WHERE status IN ('succeeded', 'failed') AND completed_at < ?)""",
                (cutoff,),
            )
            connection.execute(
                """DELETE FROM import_external_steps WHERE job_id IN (
                    SELECT id FROM import_jobs WHERE status IN ('succeeded', 'failed') AND completed_at < ?)""",
                (cutoff,),
            )
            cursor = connection.execute(
                """DELETE FROM import_jobs
                   WHERE status IN ('succeeded', 'failed') AND completed_at < ?""",
                (cutoff,),
            )
            return int(cursor.rowcount or 0)

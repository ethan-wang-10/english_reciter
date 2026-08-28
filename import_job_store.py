"""Persisted background jobs for long-running Web imports."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ImportJobStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=15.0)
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
        connection.commit()
        return connection

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
            connection.execute(
                """UPDATE import_jobs SET status='queued', updated_at=?, started_at=NULL
                   WHERE status='running' AND started_at < ?""",
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

    def complete(self, job_id: str, result: Dict[str, Any]) -> None:
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """UPDATE import_jobs SET
                       status='succeeded', result_json=?, error=NULL,
                       completed_at=?, updated_at=?
                   WHERE id=? AND status='running'""",
                (
                    json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                    now,
                    now,
                    job_id,
                ),
            )

    def fail(self, job_id: str, error: str) -> None:
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """UPDATE import_jobs SET
                       status='failed', error=?, completed_at=?, updated_at=?
                   WHERE id=? AND status='running'""",
                (str(error or "导入失败")[:1000], now, now, job_id),
            )

    def cleanup(self, retention_days: int = 14) -> int:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=max(1, retention_days))
        ).isoformat(timespec="seconds")
        with self._connect() as connection:
            cursor = connection.execute(
                """DELETE FROM import_jobs
                   WHERE status IN ('succeeded', 'failed') AND completed_at < ?""",
                (cutoff,),
            )
            return int(cursor.rowcount or 0)

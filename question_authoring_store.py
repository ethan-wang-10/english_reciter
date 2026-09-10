"""Durable leases and upload receipts for external question authors."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional


JOB_KINDS = ("generation", "recognition_blind", "context_blind", "feedback")


def _timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _identifier(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError(f"{field} must be a nonempty string of at most 256 characters")
    return value


def _positive_integer(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer")
    return value


class QuestionAuthoringStore:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.db_path), timeout=30.0)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA busy_timeout=30000")
            try:
                connection.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError as error:
                # Concurrent first opens may race to set WAL despite the busy timeout.
                if error.sqlite_errorcode not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                    raise
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS authoring_jobs (
                    job_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    worker_id TEXT NOT NULL,
                    request_id TEXT UNIQUE,
                    request_digest TEXT NOT NULL,
                    request_parameters_json TEXT NOT NULL DEFAULT 'null',
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active', 'released', 'expired', 'completed')),
                    items_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS authoring_jobs_status_expiry
                    ON authoring_jobs(status, expires_at);
                CREATE TABLE IF NOT EXISTS authoring_leases (
                    kind TEXT NOT NULL,
                    word_key TEXT NOT NULL,
                    job_id TEXT NOT NULL REFERENCES authoring_jobs(job_id),
                    PRIMARY KEY(kind, word_key)
                );
                CREATE INDEX IF NOT EXISTS authoring_leases_job
                    ON authoring_leases(job_id);
                CREATE TABLE IF NOT EXISTS authoring_submissions (
                    job_id TEXT NOT NULL REFERENCES authoring_jobs(job_id),
                    submission_id TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    PRIMARY KEY(job_id, submission_id)
                );
                """
            )
            if "request_parameters_json" not in {
                row["name"] for row in connection.execute("PRAGMA table_info(authoring_jobs)")
            }:
                connection.execute("BEGIN IMMEDIATE")
                if "request_parameters_json" not in {
                    row["name"] for row in connection.execute("PRAGMA table_info(authoring_jobs)")
                }:
                    connection.execute(
                        "ALTER TABLE authoring_jobs ADD COLUMN request_parameters_json TEXT NOT NULL DEFAULT 'null'"
                    )
                connection.commit()
            return connection
        except Exception:
            connection.close()
            raise

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if write:
                connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _expire(connection: sqlite3.Connection, now: str) -> None:
        connection.execute(
            """DELETE FROM authoring_leases WHERE job_id IN (
                   SELECT job_id FROM authoring_jobs WHERE status='active' AND expires_at<=?
               )""",
            (now,),
        )
        connection.execute(
            "UPDATE authoring_jobs SET status='expired' WHERE status='active' AND expires_at<=?",
            (now,),
        )

    @staticmethod
    def _public(row: sqlite3.Row, now: str) -> dict:
        status = row["status"]
        if status == "active" and row["expires_at"] <= now:
            status = "expired"
        return {
            "job_id": row["job_id"],
            "kind": row["kind"],
            "worker_id": row["worker_id"],
            "request_id": row["request_id"],
            "request_parameters": json.loads(row["request_parameters_json"]),
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "status": status,
            "items": json.loads(row["items_json"]),
        }

    def claim(
        self,
        sources: list[dict],
        *,
        now: datetime,
        ttl_seconds: int,
        limit: int,
        request_id: Optional[str] = None,
        kind: str = "generation",
        worker_id: str = "external",
        request_parameters: Optional[dict] = None,
    ) -> dict:
        now_text = _timestamp(now)
        ttl_seconds = _positive_integer(ttl_seconds, "ttl_seconds")
        limit = _positive_integer(limit, "limit")
        expires_at = _timestamp(now + timedelta(seconds=ttl_seconds))
        if request_id is not None:
            request_id = _identifier(request_id, "request_id")
        if kind not in JOB_KINDS:
            raise ValueError("unknown authoring job kind")
        worker_id = _identifier(worker_id, "worker_id")
        if request_parameters is not None and not isinstance(request_parameters, dict):
            raise ValueError("request_parameters must be an object or None")
        parameters_json = _json(request_parameters)
        if not isinstance(sources, list):
            raise ValueError("sources must be a list")
        by_key: dict[str, dict] = {}
        for source in sources:
            if not isinstance(source, dict) or not isinstance(source.get("english"), str):
                raise ValueError("each source must contain an english string")
            key = " ".join(source["english"].strip().casefold().split())
            if not key:
                raise ValueError("source english must not be empty")
            normalized = {**source, "english": key}
            if key in by_key and _json(by_key[key]) != _json(normalized):
                raise ValueError(f"conflicting sources for {key}")
            by_key.setdefault(key, normalized)
        digest_payload = {
            "sources": list(by_key.values()), "ttl_seconds": ttl_seconds,
            "limit": limit, "kind": kind, "worker_id": worker_id,
        }
        if request_parameters is not None:
            digest_payload["request_parameters"] = request_parameters
        request_digest = hashlib.sha256(_json(digest_payload).encode("utf-8")).hexdigest()
        with self._connection(write=True) as connection:
            self._expire(connection, now_text)
            if request_id is not None:
                existing = connection.execute(
                    "SELECT * FROM authoring_jobs WHERE request_id=?", (request_id,)
                ).fetchone()
                if existing is not None:
                    if existing["request_digest"] != request_digest:
                        raise ValueError("request_id already exists with different claim parameters")
                    return self._public(existing, now_text)
            selected = []
            for key, source in by_key.items():
                if connection.execute(
                    "SELECT 1 FROM authoring_leases WHERE kind=? AND word_key=?", (kind, key)
                ).fetchone() is None:
                    selected.append({**source, "item_id": uuid.uuid4().hex})
                    if len(selected) == limit:
                        break
            job_id = uuid.uuid4().hex
            connection.execute(
                """INSERT INTO authoring_jobs(
                       job_id, kind, worker_id, request_id, request_digest,
                       request_parameters_json, created_at, expires_at, status, items_json
                   ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)""",
                (job_id, kind, worker_id, request_id, request_digest, parameters_json, now_text, expires_at, _json(selected)),
            )
            connection.executemany(
                "INSERT INTO authoring_leases(kind, word_key, job_id) VALUES(?, ?, ?)",
                [(kind, source["english"], job_id) for source in selected],
            )
            row = connection.execute(
                "SELECT * FROM authoring_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            return self._public(row, now_text)

    def active_words(self, *, now: datetime, kind: str = "generation") -> set[str]:
        now_text = _timestamp(now)
        if kind not in JOB_KINDS:
            raise ValueError("unknown authoring job kind")
        with self._connection() as connection:
            return {
                row["word_key"]
                for row in connection.execute(
                    """SELECT leases.word_key FROM authoring_leases AS leases
                       JOIN authoring_jobs AS jobs ON jobs.job_id=leases.job_id
                       WHERE jobs.status='active' AND jobs.expires_at>? AND leases.kind=?""",
                    (now_text, kind),
                )
            }

    def get(self, job_id: str, *, now: datetime) -> Optional[dict]:
        now_text = _timestamp(now)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM authoring_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            return self._public(row, now_text) if row else None

    def get_request(self, request_id: str, *, now: datetime) -> Optional[dict]:
        now_text = _timestamp(now)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM authoring_jobs WHERE request_id=?", (request_id,)
            ).fetchone()
            return self._public(row, now_text) if row else None

    def release(self, job_id: str, *, now: datetime) -> dict:
        return self._finish(job_id, now=now, status="released")

    def complete(self, job_id: str, *, now: datetime) -> dict:
        return self._finish(job_id, now=now, status="completed")

    def _finish(self, job_id: str, *, now: datetime, status: str) -> dict:
        now_text = _timestamp(now)
        with self._connection(write=True) as connection:
            self._expire(connection, now_text)
            row = connection.execute(
                "SELECT * FROM authoring_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            recover_completed = (
                status == "completed" and row["status"] in ("expired", "released")
                and connection.execute(
                    "SELECT 1 FROM authoring_submissions WHERE job_id=? LIMIT 1", (job_id,)
                ).fetchone() is not None
            )
            if row["status"] == "active" or recover_completed:
                connection.execute("DELETE FROM authoring_leases WHERE job_id=?", (job_id,))
                connection.execute(
                    "UPDATE authoring_jobs SET status=? WHERE job_id=?", (status, job_id),
                )
                row = connection.execute(
                    "SELECT * FROM authoring_jobs WHERE job_id=?", (job_id,)
                ).fetchone()
            return self._public(row, now_text)

    def renew(self, job_id: str, *, now: datetime, ttl_seconds: int) -> dict:
        now_text = _timestamp(now)
        ttl_seconds = _positive_integer(ttl_seconds, "ttl_seconds")
        expires_at = _timestamp(now + timedelta(seconds=ttl_seconds))
        with self._connection(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM authoring_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["status"] != "active" or row["expires_at"] <= now_text:
                raise ValueError("only an active, unexpired job can be renewed")
            connection.execute(
                "UPDATE authoring_jobs SET expires_at=? WHERE job_id=?",
                (max(expires_at, row["expires_at"]), job_id),
            )
            row = connection.execute(
                "SELECT * FROM authoring_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            return self._public(row, now_text)

    def get_submission(self, job_id: str, submission_id: str) -> Optional[dict]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT digest, result_json FROM authoring_submissions WHERE job_id=? AND submission_id=?",
                (job_id, submission_id),
            ).fetchone()
            return {"digest": row["digest"], "result": json.loads(row["result_json"])} if row else None

    def save_submission(self, job_id: str, submission_id: str, *, digest: str, result: dict) -> dict:
        submission_id = _identifier(submission_id, "submission_id")
        digest = _identifier(digest, "digest")
        if not isinstance(result, dict):
            raise ValueError("result must be an object")
        result_json = _json(result)
        with self._connection(write=True) as connection:
            if connection.execute(
                "SELECT 1 FROM authoring_jobs WHERE job_id=?", (job_id,)
            ).fetchone() is None:
                raise KeyError(job_id)
            previous = connection.execute(
                "SELECT digest, result_json FROM authoring_submissions WHERE job_id=? AND submission_id=?",
                (job_id, submission_id),
            ).fetchone()
            if previous is not None:
                if previous["digest"] != digest:
                    raise ValueError("submission_id already exists with a different digest")
                return {"digest": digest, "result": json.loads(previous["result_json"])}
            connection.execute(
                "INSERT INTO authoring_submissions(job_id, submission_id, digest, result_json) VALUES(?, ?, ?, ?)",
                (job_id, submission_id, digest, result_json),
            )
            return {"digest": digest, "result": json.loads(result_json)}

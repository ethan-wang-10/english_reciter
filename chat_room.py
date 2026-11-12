"""Shared chat history with indexed pagination and cross-worker updates."""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List, Optional

from app_time import china_now_iso
from storage_utils import interprocess_file_lock

DATA_DIR = Path(os.getenv("ENGLISH_RECITER_DATA_DIR", "user_data_simple")).expanduser()
SHARED_DATA_DIR = DATA_DIR / "_shared"
CHAT_JSONL = SHARED_DATA_DIR / "chat_messages.jsonl"
CHAT_LOCKFILE = SHARED_DATA_DIR / ".chat_messages.jsonl.lock"

CHAT_DB = SHARED_DATA_DIR / "chat_messages.sqlite3"


def configure_data_dir(data_dir: Path | str) -> None:
    global DATA_DIR, SHARED_DATA_DIR, CHAT_JSONL, CHAT_LOCKFILE, CHAT_DB
    DATA_DIR = Path(data_dir).expanduser()
    SHARED_DATA_DIR = DATA_DIR / "_shared"
    CHAT_JSONL = SHARED_DATA_DIR / "chat_messages.jsonl"
    CHAT_LOCKFILE = SHARED_DATA_DIR / ".chat_messages.jsonl.lock"
    CHAT_DB = SHARED_DATA_DIR / "chat_messages.sqlite3"


def _iso_ts() -> str:
    return china_now_iso(timespec="seconds")


def _parse_line(line: str) -> Optional[dict]:
    try:
        message = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(message, dict):
        return None
    if not all(key in message for key in ("id", "ts", "room", "from", "body", "mentions")):
        return None
    if not isinstance(message.get("mentions"), list) or not message.get("id"):
        return None
    return message


def _migrate_legacy(connection: sqlite3.Connection) -> None:
    if connection.execute(
        "SELECT 1 FROM chat_metadata WHERE key='legacy_migrated'"
    ).fetchone():
        return
    with interprocess_file_lock(CHAT_LOCKFILE):
        connection.execute("BEGIN IMMEDIATE")
        try:
            if not connection.execute(
                "SELECT 1 FROM chat_metadata WHERE key='legacy_migrated'"
            ).fetchone():
                try:
                    legacy_source = CHAT_JSONL.open("r", encoding="utf-8")
                except FileNotFoundError:
                    legacy_source = None
                if legacy_source is not None:
                    # Stream the existing log once; large histories remain available.
                    with legacy_source:
                        for line in legacy_source:
                            message = _parse_line(line)
                            if message:
                                connection.execute(
                                    "INSERT OR IGNORE INTO chat_messages(id, payload) VALUES(?, ?)",
                                    (str(message["id"]), json.dumps(message, ensure_ascii=False)),
                                )
                connection.execute(
                    "INSERT INTO chat_metadata(key, value) VALUES('legacy_migrated', '1')"
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    CHAT_DB.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(CHAT_DB), timeout=15.0)
    try:
        connection.execute("PRAGMA busy_timeout=15000")
        try:
            connection.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError as error:
            if error.sqlite_errorcode not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS chat_messages (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL UNIQUE,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chat_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        _migrate_legacy(connection)
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_messages(
    before_id: Optional[str] = None,
    after_id: Optional[str] = None,
    limit: int = 50,
) -> List[dict]:
    limit = max(1, min(int(limit), 200))
    with _connection() as connection:
        cursor_id = after_id or before_id
        if cursor_id:
            cursor = connection.execute(
                "SELECT seq FROM chat_messages WHERE id=?", (cursor_id,)
            ).fetchone()
            if cursor is None:
                return []
            if after_id:
                rows = connection.execute(
                    "SELECT payload FROM chat_messages WHERE seq>? ORDER BY seq LIMIT ?",
                    (cursor[0], limit),
                ).fetchall()
                return [json.loads(row[0]) for row in rows]
            rows = connection.execute(
                "SELECT payload FROM chat_messages WHERE seq<? ORDER BY seq DESC LIMIT ?",
                (cursor[0], limit),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT payload FROM chat_messages ORDER BY seq DESC LIMIT ?", (limit,)
            ).fetchall()
        return [json.loads(row[0]) for row in reversed(rows)]


def append_message(sender: str, body: str, mentions: List[str]) -> dict:
    message = {
        "id": uuid.uuid4().hex,
        "ts": _iso_ts(),
        "room": "global",
        "from": sender,
        "body": body,
        "mentions": mentions,
    }
    with _connection() as connection:
        connection.execute(
            "INSERT INTO chat_messages(id, payload) VALUES(?, ?)",
            (message["id"], json.dumps(message, ensure_ascii=False)),
        )
    return message


def sse_generator():
    with _connection() as connection:
        sequence = connection.execute("SELECT COALESCE(MAX(seq), 0) FROM chat_messages").fetchone()[0]
        last_ping = time.monotonic()
        ready = json.dumps({"server_time": _iso_ts()}, ensure_ascii=False)
        yield f"event: ready\ndata: {ready}\n\n"
        while True:
            rows = connection.execute(
                "SELECT seq, payload FROM chat_messages WHERE seq>? ORDER BY seq LIMIT 200",
                (sequence,),
            ).fetchall()
            for sequence, payload in rows:
                yield f"event: message\ndata: {payload}\n\n"
            if rows:
                continue
            now = time.monotonic()
            if now - last_ping >= 25.0:
                ping = json.dumps({"t": int(time.time())}, ensure_ascii=False)
                yield f"event: ping\ndata: {ping}\n\n"
                last_ping = now
            time.sleep(1.0)

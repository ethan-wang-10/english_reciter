"""Incremental SQLite store for the generated Gaokao question bank."""

from __future__ import annotations

import json
import sqlite3
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional


NAMESPACES = ("questions", "candidates", "rejections", "failures")


def _encode(value: Any) -> bytes:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return zlib.compress(raw, level=6)


def _decode(value: bytes) -> Any:
    return json.loads(zlib.decompress(value).decode("utf-8"))


class QuestionSQLiteStore:
    def __init__(self, legacy_path: Path | str, *, empty_bank: Callable[[], dict]):
        self.legacy_path = Path(legacy_path)
        self.db_path = self.legacy_path.with_suffix(".sqlite3")
        self._empty_bank = empty_bank
        self._schema_ready = False
        self._legacy_signature_seen: Optional[tuple[int, int]] = None

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.db_path), timeout=30.0)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    @staticmethod
    def _schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS records (
                namespace TEXT NOT NULL,
                word_key TEXT NOT NULL,
                payload BLOB NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(namespace, word_key)
            );
            CREATE INDEX IF NOT EXISTS records_namespace_key
                ON records(namespace, word_key);
            CREATE TABLE IF NOT EXISTS question_ids (
                question_id TEXT PRIMARY KEY,
                word_key TEXT NOT NULL,
                question_type TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT OR IGNORE INTO metadata(key, value) VALUES('revision', '0')"
        )
        connection.commit()

    def _legacy_signature(self) -> tuple[int, int]:
        try:
            stat = self.legacy_path.stat()
            return int(stat.st_mtime_ns), int(stat.st_size)
        except OSError:
            return 0, 0

    def _read_legacy(self) -> dict:
        if not self.legacy_path.is_file():
            return self._empty_bank()
        try:
            with self.legacy_path.open("r", encoding="utf-8") as source:
                raw = json.load(source)
        except (OSError, json.JSONDecodeError):
            return self._empty_bank()
        bank = self._empty_bank()
        if isinstance(raw, dict):
            for namespace in NAMESPACES:
                value = raw.get(namespace)
                if isinstance(value, dict):
                    bank[namespace] = dict(value)
            bank["updated_at"] = raw.get("updated_at")
        return bank

    @staticmethod
    def _set_meta(connection: sqlite3.Connection, key: str, value: Any) -> None:
        connection.execute(
            """INSERT INTO metadata(key, value) VALUES(?, ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (key, str(value)),
        )

    @staticmethod
    def _refresh_question_ids(
        connection: sqlite3.Connection, word_key: str, record: Optional[dict]
    ) -> None:
        connection.execute("DELETE FROM question_ids WHERE word_key=?", (word_key,))
        if not isinstance(record, dict):
            return
        for question_type in ("recognition", "context"):
            question = record.get(question_type)
            question_id = (
                str(question.get("question_id") or "").strip()
                if isinstance(question, dict)
                else ""
            )
            if question_id:
                connection.execute(
                    """INSERT OR REPLACE INTO question_ids(
                           question_id, word_key, question_type
                       ) VALUES(?, ?, ?)""",
                    (question_id, word_key, question_type),
                )

    def _replace_all(self, connection: sqlite3.Connection, bank: dict) -> None:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        connection.execute("DELETE FROM records")
        connection.execute("DELETE FROM question_ids")
        for namespace in NAMESPACES:
            rows = bank.get(namespace)
            if not isinstance(rows, dict):
                continue
            for word_key, payload in rows.items():
                connection.execute(
                    "INSERT INTO records(namespace, word_key, payload, updated_at) VALUES(?, ?, ?, ?)",
                    (namespace, str(word_key), _encode(payload), now),
                )
                if namespace == "questions":
                    self._refresh_question_ids(connection, str(word_key), payload)
        self._set_meta(connection, "bank_updated_at", bank.get("updated_at") or now)
        connection.execute(
            "UPDATE metadata SET value=CAST(value AS INTEGER) + 1 WHERE key='revision'"
        )

    def _ensure_migrated(self, connection: sqlite3.Connection) -> None:
        if not self._schema_ready:
            self._schema(connection)
            self._schema_ready = True
        source_mtime, source_size = self._legacy_signature()
        source_signature = (source_mtime, source_size)
        if self._legacy_signature_seen == source_signature:
            return
        meta = dict(
            connection.execute(
                "SELECT key, value FROM metadata WHERE key IN ('legacy_mtime_ns', 'legacy_size')"
            ).fetchall()
        )
        stored_mtime = int(meta.get("legacy_mtime_ns", "-1"))
        stored_size = int(meta.get("legacy_size", "-1"))
        if stored_mtime == source_mtime and stored_size == source_size:
            self._legacy_signature_seen = source_signature
            return
        connection.execute("BEGIN IMMEDIATE")
        try:
            self._replace_all(connection, self._read_legacy())
            self._set_meta(connection, "legacy_mtime_ns", source_mtime)
            self._set_meta(connection, "legacy_size", source_size)
            connection.commit()
            self._legacy_signature_seen = source_signature
        except Exception:
            connection.rollback()
            raise

    def revision(self) -> int:
        with self._connect() as connection:
            self._ensure_migrated(connection)
            row = connection.execute(
                "SELECT value FROM metadata WHERE key='revision'"
            ).fetchone()
            return int(row[0]) if row else 0

    def load_all(self) -> dict:
        with self._connect() as connection:
            self._ensure_migrated(connection)
            bank = self._empty_bank()
            for namespace, word_key, payload in connection.execute(
                "SELECT namespace, word_key, payload FROM records ORDER BY namespace, word_key"
            ):
                bank[namespace][word_key] = _decode(payload)
            updated = connection.execute(
                "SELECT value FROM metadata WHERE key='bank_updated_at'"
            ).fetchone()
            bank["updated_at"] = updated[0] if updated else None
            return bank

    def load_namespace(self, namespace: str) -> Dict[str, Any]:
        if namespace not in NAMESPACES:
            return {}
        with self._connect() as connection:
            self._ensure_migrated(connection)
            return {
                word_key: _decode(payload)
                for word_key, payload in connection.execute(
                    "SELECT word_key, payload FROM records WHERE namespace=? ORDER BY word_key",
                    (namespace,),
                )
            }

    def get(self, namespace: str, word_key: str) -> Any:
        if namespace not in NAMESPACES:
            return None
        with self._connect() as connection:
            self._ensure_migrated(connection)
            row = connection.execute(
                "SELECT payload FROM records WHERE namespace=? AND word_key=?",
                (namespace, word_key),
            ).fetchone()
            return _decode(row[0]) if row else None

    def get_many(self, namespace: str, word_keys: Iterable[str]) -> Dict[str, Any]:
        keys = list(dict.fromkeys(str(key) for key in word_keys if str(key)))
        if namespace not in NAMESPACES or not keys:
            return {}
        placeholders = ",".join("?" for _ in keys)
        with self._connect() as connection:
            self._ensure_migrated(connection)
            return {
                key: _decode(payload)
                for key, payload in connection.execute(
                    f"SELECT word_key, payload FROM records WHERE namespace=? AND word_key IN ({placeholders})",
                    (namespace, *keys),
                )
            }

    def load_keys(self, word_keys: Iterable[str]) -> dict:
        keys = list(dict.fromkeys(str(key) for key in word_keys if str(key)))
        bank = self._empty_bank()
        if not keys:
            return bank
        placeholders = ",".join("?" for _ in keys)
        with self._connect() as connection:
            self._ensure_migrated(connection)
            for namespace, word_key, payload in connection.execute(
                f"""SELECT namespace, word_key, payload FROM records
                     WHERE word_key IN ({placeholders})""",
                keys,
            ):
                bank[namespace][word_key] = _decode(payload)
        return bank

    def save_keys(self, word_keys: Iterable[str], bank: dict) -> None:
        keys = list(dict.fromkeys(str(key) for key in word_keys if str(key)))

        def apply(selected: dict) -> None:
            for namespace in NAMESPACES:
                target = selected[namespace]
                source = bank.get(namespace)
                source = source if isinstance(source, dict) else {}
                for key in keys:
                    if key in source:
                        target[key] = source[key]
                    else:
                        target.pop(key, None)

        self.mutate(keys, apply)

    def get_question_by_id(self, question_id: str) -> Optional[tuple[dict, dict]]:
        with self._connect() as connection:
            self._ensure_migrated(connection)
            row = connection.execute(
                """SELECT r.payload, q.question_type
                   FROM question_ids q
                   JOIN records r ON r.namespace='questions' AND r.word_key=q.word_key
                   WHERE q.question_id=?""",
                (question_id,),
            ).fetchone()
            if not row:
                return None
            record = _decode(row[0])
            question = record.get(row[1]) if isinstance(record, dict) else None
            return (question, record) if isinstance(question, dict) else None

    def mutate(self, word_keys: Iterable[str], callback: Callable[[dict], None]) -> None:
        keys = list(dict.fromkeys(str(key) for key in word_keys if str(key)))
        if not keys:
            return
        placeholders = ",".join("?" for _ in keys)
        with self._connect() as connection:
            self._ensure_migrated(connection)
            connection.execute("BEGIN IMMEDIATE")
            try:
                before = self._empty_bank()
                for namespace, word_key, payload in connection.execute(
                    f"""SELECT namespace, word_key, payload FROM records
                         WHERE word_key IN ({placeholders})""",
                    keys,
                ):
                    before[namespace][word_key] = _decode(payload)
                after = self._empty_bank()
                for namespace in NAMESPACES:
                    after[namespace] = dict(before[namespace])
                callback(after)
                now = datetime.now().astimezone().isoformat(timespec="seconds")
                changed = False
                for namespace in NAMESPACES:
                    old_rows = before[namespace]
                    new_rows = after.get(namespace, {})
                    for key in keys:
                        old = old_rows.get(key)
                        new = new_rows.get(key)
                        if new == old:
                            continue
                        changed = True
                        if new is None:
                            connection.execute(
                                "DELETE FROM records WHERE namespace=? AND word_key=?",
                                (namespace, key),
                            )
                        else:
                            connection.execute(
                                """INSERT INTO records(namespace, word_key, payload, updated_at)
                                   VALUES(?, ?, ?, ?)
                                   ON CONFLICT(namespace, word_key) DO UPDATE SET
                                       payload=excluded.payload,
                                       updated_at=excluded.updated_at""",
                                (namespace, key, _encode(new), now),
                            )
                        if namespace == "questions":
                            self._refresh_question_ids(connection, key, new)
                if changed:
                    self._set_meta(connection, "bank_updated_at", now)
                    connection.execute(
                        "UPDATE metadata SET value=CAST(value AS INTEGER) + 1 WHERE key='revision'"
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def replace_all(self, bank: dict) -> None:
        with self._connect() as connection:
            self._ensure_migrated(connection)
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._replace_all(connection, bank)
                connection.commit()
            except Exception:
                connection.rollback()
                raise

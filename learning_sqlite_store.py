"""SQLite persistence for Web learning data.

The legacy JSON files remain the interchange and rollback source.  A user is
migrated on first access, after which normal reads and writes only touch the
SQLite database.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zlib
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


SCHEMA_VERSION = 1


class LearningStoreLoadError(RuntimeError):
    """Raised when legacy learning data cannot be migrated safely."""


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _encode(value: Any, *, compress: bool = True) -> Tuple[bytes, str]:
    raw = _json_bytes(value)
    payload = b"z" + zlib.compress(raw, level=1) if compress else b"j" + raw
    return payload, hashlib.sha256(raw).hexdigest()


def _decode(value: bytes) -> Any:
    if value.startswith(b"j"):
        raw = value[1:]
    elif value.startswith(b"z"):
        raw = zlib.decompress(value[1:])
    else:
        # Databases created before the payload prefix used zlib for every row.
        raw = zlib.decompress(value)
    return json.loads(raw.decode("utf-8"))


class LearningSQLiteStore:
    def __init__(self, db_path: Path | str, legacy_json_path: Path | str):
        self.db_path = Path(db_path)
        self.legacy_json_path = Path(legacy_json_path)
        self.sidecar_path = self.legacy_json_path.with_name(
            f"{self.legacy_json_path.stem}.learning_state_v2.json"
        )
        self._schema_ready = False

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.db_path), timeout=15.0)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA busy_timeout=15000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS words (
                row_key TEXT PRIMARY KEY,
                word_key TEXT NOT NULL,
                bucket TEXT NOT NULL CHECK (bucket IN ('pending', 'mastered')),
                position INTEGER NOT NULL,
                payload BLOB NOT NULL,
                payload_hash TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS words_bucket_position
                ON words(bucket, position);
            CREATE TABLE IF NOT EXISTS learning_state (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                payload BLOB NOT NULL,
                payload_hash TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT OR IGNORE INTO metadata(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        connection.execute(
            "INSERT OR IGNORE INTO metadata(key, value) VALUES('revision', '0')"
        )
        connection.commit()

    @staticmethod
    def _normal_state(raw: Any) -> Dict[str, Any]:
        state = dict(raw) if isinstance(raw, dict) else {}
        review_states = state.get("review_states")
        daily_task = state.get("daily_task")
        state.setdefault("version", 1)
        state["review_states"] = (
            dict(review_states) if isinstance(review_states, dict) else {}
        )
        state["daily_task"] = dict(daily_task) if isinstance(daily_task, dict) else None
        return state

    def _read_legacy(self) -> Tuple[List[dict], List[dict], Dict[str, Any]]:
        if not self.legacy_json_path.is_file():
            return [], [], self._normal_state(None)
        try:
            if self.legacy_json_path.stat().st_size == 0:
                return [], [], self._normal_state(None)
            with self.legacy_json_path.open("r", encoding="utf-8") as source:
                root = json.load(source)
            if not isinstance(root, dict):
                raise LearningStoreLoadError("学习数据根节点必须是对象")
            pending = root.get("all_words") or []
            mastered = root.get("mastered_words") or []
            if not isinstance(pending, list) or not isinstance(mastered, list):
                raise LearningStoreLoadError("学习数据单词列表格式无效")
            has_main_state = "learning_state_v2" in root
            state = root.get("learning_state_v2")
            if has_main_state and not isinstance(state, dict):
                raise LearningStoreLoadError("学习状态格式无效")
            if not has_main_state and self.sidecar_path.is_file():
                with self.sidecar_path.open("r", encoding="utf-8") as source:
                    state = json.load(source)
                if not isinstance(state, dict):
                    raise LearningStoreLoadError("学习状态 sidecar 根节点必须是对象")
            return (
                [dict(row) for row in pending if isinstance(row, dict)],
                [dict(row) for row in mastered if isinstance(row, dict)],
                self._normal_state(state),
            )
        except LearningStoreLoadError:
            raise
        except (OSError, json.JSONDecodeError) as exc:
            raise LearningStoreLoadError(f"旧学习数据无法安全迁移: {exc}") from exc

    @staticmethod
    def _word_key(row: dict) -> str:
        return str(row.get("english") or "").strip().casefold()

    @classmethod
    def _rows(cls, pending: Iterable[dict], mastered: Iterable[dict]):
        seen: Dict[str, int] = {}
        for bucket, source in (("pending", pending), ("mastered", mastered)):
            for position, row in enumerate(source):
                word_key = cls._word_key(row)
                occurrence = seen.get(word_key, 0)
                seen[word_key] = occurrence + 1
                row_key = word_key if occurrence == 0 else f"{word_key}#{occurrence + 1}"
                payload, payload_hash = _encode(row, compress=False)
                yield row_key, word_key, bucket, position, payload, payload_hash

    def _ensure_migrated(self, connection: sqlite3.Connection) -> None:
        if not self._schema_ready:
            self._create_schema(connection)
            self._schema_ready = True
        migrated = connection.execute(
            "SELECT value FROM metadata WHERE key='legacy_migrated'"
        ).fetchone()
        if migrated:
            return
        pending, mastered, state = self._read_legacy()
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.executemany(
                """INSERT INTO words(
                       row_key, word_key, bucket, position, payload, payload_hash
                   ) VALUES(?, ?, ?, ?, ?, ?)""",
                list(self._rows(pending, mastered)),
            )
            state_payload, state_hash = _encode(state)
            connection.execute(
                "INSERT INTO learning_state(singleton, payload, payload_hash) VALUES(1, ?, ?)",
                (state_payload, state_hash),
            )
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES('legacy_migrated', '1')"
            )
            connection.execute(
                "UPDATE metadata SET value='1' WHERE key='revision'"
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def load(self) -> Tuple[List[dict], List[dict], Dict[str, Any]]:
        with self._connect() as connection:
            self._ensure_migrated(connection)
            pending: List[dict] = []
            mastered: List[dict] = []
            for bucket, payload in connection.execute(
                "SELECT bucket, payload FROM words ORDER BY bucket, position"
            ):
                row = _decode(payload)
                (pending if bucket == "pending" else mastered).append(row)
            state_row = connection.execute(
                "SELECT payload FROM learning_state WHERE singleton=1"
            ).fetchone()
            state = self._normal_state(_decode(state_row[0]) if state_row else None)
            return pending, mastered, state

    def save(
        self,
        pending: Iterable[dict],
        mastered: Iterable[dict],
        state: Dict[str, Any],
    ) -> bool:
        desired = list(self._rows(pending, mastered))
        state_payload, state_hash = _encode(self._normal_state(state))
        with self._connect() as connection:
            self._ensure_migrated(connection)
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = {
                    row[0]: (row[1], row[2], row[3], row[4])
                    for row in connection.execute(
                        "SELECT row_key, bucket, position, payload_hash, word_key FROM words"
                    )
                }
                desired_keys = {row[0] for row in desired}
                changed = False
                for row_key in existing.keys() - desired_keys:
                    connection.execute("DELETE FROM words WHERE row_key=?", (row_key,))
                    changed = True
                for row in desired:
                    row_key, word_key, bucket, position, payload, payload_hash = row
                    if existing.get(row_key) == (bucket, position, payload_hash, word_key):
                        continue
                    connection.execute(
                        """INSERT INTO words(
                               row_key, word_key, bucket, position, payload, payload_hash
                           ) VALUES(?, ?, ?, ?, ?, ?)
                           ON CONFLICT(row_key) DO UPDATE SET
                               word_key=excluded.word_key,
                               bucket=excluded.bucket,
                               position=excluded.position,
                               payload=excluded.payload,
                               payload_hash=excluded.payload_hash""",
                        row,
                    )
                    changed = True
                old_state = connection.execute(
                    "SELECT payload_hash FROM learning_state WHERE singleton=1"
                ).fetchone()
                if not old_state or old_state[0] != state_hash:
                    connection.execute(
                        """INSERT INTO learning_state(singleton, payload, payload_hash)
                           VALUES(1, ?, ?)
                           ON CONFLICT(singleton) DO UPDATE SET
                               payload=excluded.payload,
                               payload_hash=excluded.payload_hash""",
                        (state_payload, state_hash),
                    )
                    changed = True
                if changed:
                    connection.execute(
                        "UPDATE metadata SET value=CAST(value AS INTEGER) + 1 WHERE key='revision'"
                    )
                connection.commit()
                return changed
            except Exception:
                connection.rollback()
                raise

    def revision(self) -> int:
        if not self.db_path.is_file():
            return 0
        try:
            with sqlite3.connect(str(self.db_path), timeout=2.0) as connection:
                row = connection.execute(
                    "SELECT value FROM metadata WHERE key='revision'"
                ).fetchone()
                return int(row[0]) if row else 0
        except (OSError, sqlite3.Error, TypeError, ValueError):
            return 0

    def summary(self) -> Dict[str, int]:
        with self._connect() as connection:
            self._ensure_migrated(connection)
            counts = dict(
                connection.execute(
                    "SELECT bucket, COUNT(*) FROM words GROUP BY bucket"
                ).fetchall()
            )
            return {
                "pending": int(counts.get("pending", 0)),
                "mastered": int(counts.get("mastered", 0)),
            }

    def backup_to(self, destination: Path | str) -> None:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as source, sqlite3.connect(str(destination)) as target:
            self._ensure_migrated(source)
            source.backup(target)


def learning_store_revision(db_path: Path | str) -> int:
    path = Path(db_path)
    if not path.is_file():
        return 0
    try:
        with sqlite3.connect(str(path), timeout=2.0) as connection:
            row = connection.execute(
                "SELECT value FROM metadata WHERE key='revision'"
            ).fetchone()
            return int(row[0]) if row else 0
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return 0

"""Transactional persistence shared by XP, challenges, and period rewards."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

import reward_schema
from storage_utils import PersistentStateLoadError, read_json_state

_local = threading.local()


def database_path(data_dir: Path) -> Path:
    return Path(data_dir) / "_shared" / "rewards.sqlite3"


def _fault_hook(stage: str, key: str) -> None:
    """Injection point for transaction durability tests."""


def _connect(data_dir: Path) -> sqlite3.Connection:
    path = database_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=30.0)
    try:
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS reward_documents (
                document_key TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS reward_events (
                username TEXT NOT NULL,
                kind TEXT NOT NULL,
                event_key TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY(username, kind, event_key)
            );
            """
        )
        return connection
    except sqlite3.DatabaseError as exc:
        connection.close()
        raise PersistentStateLoadError(f"Cannot open reward ledger: {path}") from exc
    except BaseException:
        connection.close()
        raise


def _active(data_dir: Path) -> sqlite3.Connection | None:
    active = getattr(_local, "active", None)
    if active is None:
        return None
    original, root, connection = active
    if os.fspath(data_dir) != original and root != str(Path(data_dir).resolve()):
        raise RuntimeError("Cannot nest reward transactions for different data directories")
    return connection


@contextmanager
def transaction(data_dir: Path) -> Iterator[sqlite3.Connection]:
    connection = _active(data_dir)
    if connection is not None:
        yield connection
        return
    connection = _connect(data_dir)
    try:
        connection.execute("BEGIN IMMEDIATE")
        _local.active = (os.fspath(data_dir), str(Path(data_dir).resolve()), connection)
        yield connection
        _fault_hook("before_commit", "")
        connection.commit()
    except sqlite3.DatabaseError as exc:
        connection.rollback()
        raise PersistentStateLoadError(f"Cannot update reward ledger: {database_path(data_dir)}") from exc
    except BaseException:
        connection.rollback()
        raise
    finally:
        _local.active = None
        connection.close()


@contextmanager
def savepoint(data_dir: Path) -> Iterator[Callable[[], None]]:
    with transaction(data_dir) as connection:
        sequence = getattr(_local, "savepoint_sequence", 0) + 1
        _local.savepoint_sequence = sequence
        name = f"reward_operation_{sequence}"
        connection.execute(f"SAVEPOINT {name}")

        def rollback() -> None:
            connection.execute(f"ROLLBACK TO {name}")

        try:
            yield rollback
        except BaseException:
            connection.execute(f"ROLLBACK TO {name}")
            raise
        finally:
            connection.execute(f"RELEASE {name}")


def document_key(data_dir: Path, path: Path) -> str:
    connection = _active(data_dir)
    root = Path(_local.active[1]) if connection is not None else Path(data_dir).resolve()
    return Path(path).resolve().relative_to(root).as_posix()


def _decode(payload: str, key: str, validate: Callable[[Any], None]) -> Any:
    try:
        value = json.loads(payload)
        validate(value)
        return value
    except (TypeError, ValueError) as exc:
        raise PersistentStateLoadError(f"Invalid reward state: {key}") from exc


def load_document(
    data_dir: Path,
    path: Path,
    default: Any,
    validate: Callable[[Any], None],
    *,
    on_migrate: Callable[[Any], None] | None = None,
) -> Any:
    key = document_key(data_dir, path)
    active = _active(data_dir)
    if active is None:
        connection = _connect(data_dir)
        try:
            row = connection.execute(
                "SELECT payload FROM reward_documents WHERE document_key=?", (key,),
            ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise PersistentStateLoadError(f"Cannot read reward ledger: {key}") from exc
        finally:
            connection.close()
        if row is not None:
            return _decode(row[0], key, validate)
    with transaction(data_dir) as connection:
        row = connection.execute(
            "SELECT payload FROM reward_documents WHERE document_key=?", (key,),
        ).fetchone()
        if row is not None:
            return _decode(row[0], key, validate)
        value = read_json_state(Path(path), default)
        try:
            validate(value)
            if on_migrate is not None:
                on_migrate(value)
        except (TypeError, ValueError) as exc:
            raise PersistentStateLoadError(f"Invalid legacy reward state: {path}") from exc
        connection.execute(
            "INSERT INTO reward_documents(document_key, payload) VALUES(?, ?)",
            (key, json.dumps(value, ensure_ascii=False, allow_nan=False)),
        )
        _fault_hook("after_migrate", key)
        return value


def save_document(
    data_dir: Path, path: Path, value: Any, validate: Callable[[Any], None],
) -> None:
    validate(value)
    key = document_key(data_dir, path)
    with transaction(data_dir) as connection:
        connection.execute(
            """INSERT INTO reward_documents(document_key, payload) VALUES(?, ?)
               ON CONFLICT(document_key) DO UPDATE SET
                   payload=excluded.payload, revision=reward_documents.revision+1""",
            (key, json.dumps(value, ensure_ascii=False, allow_nan=False)),
        )
        _fault_hook("after_save", key)


def _validate_event(value: Any, kind: str, event_key: str) -> None:
    if kind == "xp":
        reward_schema.gamification_state({"xp_transactions": {event_key: value}})
    elif kind == "practice":
        reward_schema.gamification_state({"practice_events": [{"event_id": event_key, "payload": value}]})
    else:
        raise ValueError(f"Unknown reward event kind: {kind}")


def get_event(data_dir: Path, username: str, kind: str, event_key: str) -> Any:
    active = _active(data_dir)
    connection = active if active is not None else _connect(data_dir)
    try:
        row = connection.execute(
            "SELECT payload FROM reward_events WHERE username=? AND kind=? AND event_key=?",
            (username, kind, event_key),
        ).fetchone()
        if row is None:
            return None
        return _decode(
            row[0], f"{username}:{kind}:{event_key}",
            lambda value: _validate_event(value, kind, event_key),
        )
    except sqlite3.DatabaseError as exc:
        raise PersistentStateLoadError(f"Cannot read reward event: {username}/{kind}") from exc
    finally:
        if active is None:
            connection.close()


def put_event(data_dir: Path, username: str, kind: str, event_key: str, payload: Any) -> None:
    _validate_event(payload, kind, event_key)
    with transaction(data_dir) as connection:
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True)
        row = connection.execute(
            "SELECT payload FROM reward_events WHERE username=? AND kind=? AND event_key=?",
            (username, kind, event_key),
        ).fetchone()
        if row is not None:
            existing = _decode(
                row[0], f"{username}:{kind}:{event_key}",
                lambda value: _validate_event(value, kind, event_key),
            )
            if existing != payload:
                raise ValueError("Reward event ID conflicts with a committed event")
            return
        connection.execute(
            "INSERT INTO reward_events(username, kind, event_key, payload) VALUES(?, ?, ?, ?)",
            (username, kind, event_key, encoded),
        )
        _fault_hook("after_event", f"{username}:{kind}:{event_key}")


def delete_event(data_dir: Path, username: str, kind: str, event_key: str) -> None:
    with transaction(data_dir) as connection:
        connection.execute(
            "DELETE FROM reward_events WHERE username=? AND kind=? AND event_key=?",
            (username, kind, event_key),
        )


def purge_user(data_dir: Path, username: str) -> None:
    with transaction(data_dir) as connection:
        connection.execute("DELETE FROM reward_events WHERE username=?", (username,))
        # Retain a migration marker so a leftover legacy file cannot revive deleted XP.
        connection.execute(
            """INSERT INTO reward_documents(document_key, payload) VALUES(?, '{}')
               ON CONFLICT(document_key) DO UPDATE SET payload='{}', revision=revision+1""",
            (f"{username}/gamification.json",),
        )

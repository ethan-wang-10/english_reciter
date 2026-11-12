import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

import chat_room


@pytest.fixture
def isolated_chat(tmp_path):
    previous_dir = chat_room.DATA_DIR
    chat_room.configure_data_dir(tmp_path)
    try:
        yield tmp_path
    finally:
        chat_room.configure_data_dir(previous_dir)


def test_history_preserves_insertion_order_and_paginates(isolated_chat, monkeypatch):
    monkeypatch.setattr(chat_room, "_iso_ts", lambda: "2026-10-29T10:00:00+08:00")
    identifiers = iter(["z", "c", "a", "x", "b"])
    monkeypatch.setattr(chat_room.uuid, "uuid4", lambda: type("Id", (), {"hex": next(identifiers)})())
    messages = [chat_room.append_message("alice", str(index), []) for index in range(5)]

    assert chat_room.get_messages(limit=2) == messages[-2:]
    assert chat_room.get_messages(before_id=messages[3]["id"], limit=2) == messages[1:3]
    assert chat_room.get_messages(after_id=messages[1]["id"], limit=2) == messages[2:4]
    assert chat_room.get_messages(after_id="missing") == []


def test_legacy_history_is_migrated_once_and_retained(isolated_chat):
    chat_room.SHARED_DATA_DIR.mkdir(parents=True)
    message = {
        "id": "old", "ts": "2026-09-01T12:00:00+08:00", "room": "global",
        "from": "alice", "body": "legacy", "mentions": [],
    }
    original = "invalid json\n" + json.dumps(message) + "\n[]\n"
    chat_room.CHAT_JSONL.write_text(original, encoding="utf-8")

    assert chat_room.get_messages() == [message]
    added = chat_room.append_message("bob", "new", ["alice"])
    assert chat_room.get_messages() == [message, added]
    assert chat_room.CHAT_JSONL.read_text(encoding="utf-8") == original
    with closing(sqlite3.connect(chat_room.CHAT_DB)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == 2


def test_large_legacy_history_does_not_disable_chat(isolated_chat):
    chat_room.SHARED_DATA_DIR.mkdir(parents=True)
    with chat_room.CHAT_JSONL.open("w", encoding="utf-8") as source:
        source.write(" " * (32 * 1024 * 1024) + "\n")
        source.write(json.dumps({
            "id": "large-history", "ts": "2026-09-01T12:00:00+08:00", "room": "global",
            "from": "alice", "body": "retained", "mentions": [],
        }) + "\n")

    assert chat_room.get_messages()[0]["body"] == "retained"
    added = chat_room.append_message("alice", "still available", [])
    assert chat_room.get_messages()[-1] == added


def test_data_directories_are_isolated(isolated_chat):
    original = chat_room.append_message("alice", "first instance", [])
    chat_room.configure_data_dir(isolated_chat / "another-instance")
    assert chat_room.get_messages() == []
    chat_room.append_message("bob", "second instance", [])
    chat_room.configure_data_dir(isolated_chat)
    assert chat_room.get_messages() == [original]


def test_another_worker_reads_history_and_stream_sees_its_write(isolated_chat):
    original = chat_room.append_message("alice", "first worker", [])
    stream = chat_room.sse_generator()
    assert next(stream).startswith("event: ready\n")
    env = dict(os.environ, ENGLISH_RECITER_DATA_DIR=str(isolated_chat))
    script = (
        "import json, chat_room; "
        "history=chat_room.get_messages(); "
        "added=chat_room.append_message('bob', 'second worker', []); "
        "print(json.dumps({'history': history, 'added': added}))"
    )
    try:
        result = json.loads(subprocess.check_output(
            [sys.executable, "-c", script], env=env, cwd=os.path.dirname(chat_room.__file__),
            text=True, timeout=20,
        ))
        assert result["history"] == [original]
        event = next(stream)
        assert event.startswith("event: message\n")
        assert json.loads(event.split("data: ", 1)[1]) == result["added"]
        assert chat_room.get_messages()[-1] == result["added"]
    finally:
        stream.close()


def test_connections_are_closed_after_requests(isolated_chat, monkeypatch):
    connections = []
    connect = sqlite3.connect

    def tracked_connect(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(chat_room.sqlite3, "connect", tracked_connect)
    chat_room.append_message("alice", "hello", [])
    chat_room.get_messages()
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


def test_unreadable_legacy_history_is_not_marked_as_migrated(isolated_chat, monkeypatch):
    chat_room.SHARED_DATA_DIR.mkdir(parents=True)
    message = {
        "id": "preserved", "ts": "2026-09-01T12:00:00+08:00", "room": "global",
        "from": "alice", "body": "retained", "mentions": [],
    }
    chat_room.CHAT_JSONL.write_text(json.dumps(message) + "\n", encoding="utf-8")
    original_open = Path.open

    def deny_legacy_read(path, *args, **kwargs):
        if path == chat_room.CHAT_JSONL:
            raise PermissionError("temporarily unreadable")
        return original_open(path, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "open", deny_legacy_read)
        with pytest.raises(PermissionError):
            chat_room.append_message("bob", "must not be stored", [])
    with closing(sqlite3.connect(chat_room.CHAT_DB)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM chat_metadata").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == 0
    assert chat_room.get_messages() == [message]

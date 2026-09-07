import json
import sqlite3
import zlib

import pytest

from question_sqlite_store import NAMESPACES, QuestionSQLiteStore


def _empty_bank() -> dict:
    return {**{namespace: {} for namespace in NAMESPACES}, "updated_at": None}


@pytest.fixture
def existing_store(tmp_path):
    legacy = tmp_path / "questions.json"
    legacy.write_text(json.dumps(_empty_bank()), encoding="utf-8")
    stat = legacy.stat()
    record = {
        "recognition": {"question_id": "apple-recognition", "answer": "apple"},
        "context": {"question_id": "apple-context", "answer": "apple"},
    }
    candidate = {"audit_checkpoint": {"recognition": {"status": "passed"}}}
    rows = [
        ("questions", "apple", record),
        ("questions", "banana", {"answer": "banana"}),
        ("candidates", "apple", candidate),
        ("failures", "pear", {"attempts": 1}),
    ]
    db_path = legacy.with_suffix(".sqlite3")
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE records (
                namespace TEXT NOT NULL,
                word_key TEXT NOT NULL,
                payload BLOB NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(namespace, word_key)
            );
            CREATE INDEX records_namespace_key ON records(namespace, word_key);
            CREATE TABLE question_ids (
                question_id TEXT PRIMARY KEY,
                word_key TEXT NOT NULL,
                question_type TEXT NOT NULL
            );
            """
        )
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES(?, ?)",
            [
                ("revision", "17"),
                ("legacy_mtime_ns", str(stat.st_mtime_ns)),
                ("legacy_size", str(stat.st_size)),
                ("bank_updated_at", "2026-09-01T01:00:00+00:00"),
            ],
        )
        connection.executemany(
            "INSERT INTO records VALUES(?, ?, ?, ?)",
            [
                (
                    namespace,
                    key,
                    zlib.compress(json.dumps(payload).encode("utf-8")),
                    "2026-09-01T01:00:00+00:00",
                )
                for namespace, key, payload in rows
            ],
        )
        connection.executemany(
            "INSERT INTO question_ids VALUES(?, ?, ?)",
            [
                ("apple-recognition", "apple", "recognition"),
                ("apple-context", "apple", "context"),
            ],
        )
    return QuestionSQLiteStore(legacy, empty_bank=_empty_bank), record, candidate


def _stored_rows(db_path):
    with sqlite3.connect(db_path) as connection:
        return {
            table: connection.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall()
            for table in ("records", "metadata", "question_ids")
        }


def test_existing_schema_adds_word_key_index_without_rewriting_data(existing_store):
    store, record, candidate = existing_store
    before = _stored_rows(store.db_path)
    legacy_bytes = store.legacy_path.read_bytes()

    selected = store.load_keys(["apple"])

    assert selected["questions"] == {"apple": record}
    assert selected["candidates"] == {"apple": candidate}
    assert selected["failures"] == {}
    assert store.revision() == 17
    assert _stored_rows(store.db_path) == before
    assert store.legacy_path.read_bytes() == legacy_bytes
    with sqlite3.connect(store.db_path) as connection:
        plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT namespace, word_key, payload "
            "FROM records WHERE word_key IN (?, ?)",
            ("apple", "banana"),
        ).fetchall()
    assert any("USING INDEX records_word_key" in row[3] for row in plan)
    assert all("SCAN records" not in row[3] for row in plan)

    reopened = QuestionSQLiteStore(store.legacy_path, empty_bank=_empty_bank)
    assert reopened.load_keys(["apple"]) == selected
    assert _stored_rows(store.db_path) == before


def test_checkpoint_mutation_preserves_existing_questions_and_other_words(existing_store):
    store, record, _ = existing_store
    before = _stored_rows(store.db_path)
    store.mutate(["apple"], lambda selected: None)
    assert store.revision() == 17
    assert _stored_rows(store.db_path) == before

    def update_checkpoint(selected):
        selected["candidates"]["apple"] = {
            "audit_checkpoint": {
                "recognition": {"status": "passed"},
                "context": {"status": "passed"},
            }
        }

    store.mutate(["apple"], update_checkpoint)

    assert store.revision() == 18
    assert store.get("questions", "apple") == record
    assert store.get("candidates", "apple")["audit_checkpoint"]["context"] == {
        "status": "passed"
    }
    after = _stored_rows(store.db_path)
    assert [row for row in after["records"] if row[:2] != ("candidates", "apple")] == [
        row for row in before["records"] if row[:2] != ("candidates", "apple")
    ]
    assert after["question_ids"] == before["question_ids"]
    assert store.get_question_by_id("apple-context") == (record["context"], record)

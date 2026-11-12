import json
import sqlite3
import zlib
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier

import pytest

import question_sqlite_store
from question_sqlite_store import NAMESPACES, QuestionSQLiteStore
from storage_utils import PersistentStateLoadError


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


def test_connections_close_after_question_reads_writes_and_errors(tmp_path, monkeypatch):
    connections = []
    original_connect = sqlite3.connect

    def connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, 'connect', connect)
    store = QuestionSQLiteStore(tmp_path / 'questions.json', empty_bank=_empty_bank)
    store.revision()
    store.replace_all(_empty_bank())
    store.load_all()
    store.load_namespace('questions')
    store.get('questions', 'apple')
    store.get_many('questions', ['apple'])
    store.load_keys(['apple'])
    store.get_question_by_id('missing')
    store.mutate(['apple'], lambda selected: None)

    def fail(_):
        raise ValueError('mutation failed')

    with pytest.raises(ValueError, match='mutation failed'):
        store.mutate(['apple'], fail)
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            connection.execute('SELECT 1')


def test_first_migration_does_not_replace_bank_twice(tmp_path, monkeypatch):
    stores = [QuestionSQLiteStore(tmp_path / 'questions.json', empty_bank=_empty_bank)
              for _ in range(2)]
    with closing(stores[0]._connect()) as connection:
        stores[0]._schema(connection)
    barrier = Barrier(2)
    replacements = []

    class ConcurrentConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql == 'BEGIN IMMEDIATE':
                barrier.wait(timeout=10)
            return super().execute(sql, *args, **kwargs)

    for store in stores:
        store._schema_ready = True
        original_replace = store._replace_all

        def replace(connection, bank, original=original_replace):
            replacements.append(True)
            return original(connection, bank)

        monkeypatch.setattr(store, '_replace_all', replace)
        monkeypatch.setattr(store, '_connect', lambda: sqlite3.connect(
            stores[0].db_path, timeout=15, factory=ConcurrentConnection))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(store.load_all) for store in stores]
        results = [future.result(timeout=15) for future in futures]
    assert results[0] == results[1]
    assert len(replacements) == 1


def test_full_bank_and_timestamp_use_one_read_snapshot(tmp_path, monkeypatch):
    reader = QuestionSQLiteStore(tmp_path / 'questions.json', empty_bank=_empty_bank)
    writer = QuestionSQLiteStore(reader.legacy_path, empty_bank=_empty_bank)
    old = {**_empty_bank(), 'questions': {'apple': {'answer': 'old'}}, 'updated_at': 'old'}
    reader.replace_all(old)
    original_decode = question_sqlite_store._decode
    changed = False

    def decode(payload):
        nonlocal changed
        value = original_decode(payload)
        if not changed:
            changed = True
            writer.replace_all({**_empty_bank(), 'updated_at': 'new'})
        return value

    monkeypatch.setattr(question_sqlite_store, '_decode', decode)
    assert reader.load_all() == old
    assert changed
    assert reader.load_all()['updated_at'] == 'new'


@pytest.mark.parametrize('invalid_source', ['{broken', '[]', '{"questions": []}'])
def test_corrupt_legacy_update_preserves_migrated_database(existing_store, invalid_source):
    store, _, _ = existing_store
    store.load_all()
    before = _stored_rows(store.db_path)
    store.legacy_path.write_text(invalid_source, encoding='utf-8')

    with pytest.raises(PersistentStateLoadError):
        store.load_all()
    assert _stored_rows(store.db_path) == before
    with pytest.raises(PersistentStateLoadError):
        store.replace_all(_empty_bank())
    assert _stored_rows(store.db_path) == before


def test_removing_migrated_legacy_source_keeps_sqlite_bank(existing_store):
    store, _, _ = existing_store
    expected = store.load_all()
    before = _stored_rows(store.db_path)
    store.legacy_path.unlink()

    assert store.load_all() == expected
    reopened = QuestionSQLiteStore(store.legacy_path, empty_bank=_empty_bank)
    assert reopened.load_all() == expected
    assert _stored_rows(store.db_path) == before


def test_legacy_read_failure_preserves_database_and_can_be_retried(existing_store, monkeypatch):
    store, _, _ = existing_store
    store.load_all()
    before = _stored_rows(store.db_path)
    store.legacy_path.write_text(json.dumps({**_empty_bank(), 'updated_at': 'changed'}), encoding='utf-8')
    original_open = type(store.legacy_path).open

    def denied(path, *args, **kwargs):
        if path == store.legacy_path:
            raise PermissionError('temporarily unavailable')
        return original_open(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(type(store.legacy_path), 'open', denied)
        with pytest.raises(PersistentStateLoadError):
            store.load_all()
    assert _stored_rows(store.db_path) == before
    assert store.load_all()['updated_at'] == 'changed'


def test_corrupt_first_migration_can_recover_after_source_repair(tmp_path):
    legacy = tmp_path / 'questions.json'
    legacy.write_text('{broken', encoding='utf-8')
    store = QuestionSQLiteStore(legacy, empty_bank=_empty_bank)
    with pytest.raises(PersistentStateLoadError):
        store.load_all()
    repaired = {**_empty_bank(), 'questions': {'apple': {'answer': 'apple'}}}
    legacy.write_text(json.dumps(repaired), encoding='utf-8')
    assert store.load_all()['questions'] == repaired['questions']


def test_public_question_read_and_mutation_propagate_corrupt_source(existing_store, monkeypatch):
    import gaokao_questions as questions

    store, _, _ = existing_store
    store.load_all()
    before = _stored_rows(store.db_path)
    store.legacy_path.write_text('{broken', encoding='utf-8')
    monkeypatch.setattr(questions, '_question_store', lambda: store)
    monkeypatch.setattr(questions, 'QUESTION_BANK_LOCK_FILE', store.db_path.with_suffix('.lock'))
    monkeypatch.setattr(questions, '_cache', None)
    monkeypatch.setattr(questions, '_cache_mtime_ns', -1)

    with pytest.raises(PersistentStateLoadError):
        questions.load_bank()
    with pytest.raises(PersistentStateLoadError):
        questions.persist_candidate_pool_result({}, {'apple': 'generation failed'})
    assert _stored_rows(store.db_path) == before

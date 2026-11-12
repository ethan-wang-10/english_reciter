import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier

import pytest

import learning_sqlite_store
from learning_sqlite_store import LearningSQLiteStore, LearningStoreLoadError, learning_store_revision


def _word(english: str, chinese: str, success_count: int = 0) -> dict:
    return {
        'english': english,
        'chinese': chinese,
        'success_count': success_count,
        'next_review_date': '2026-08-28',
        'example': None,
        'review_round': 0,
        'review_count': 0,
        'added_at': None,
    }


def test_migrates_legacy_json_without_modifying_it(tmp_path) -> None:
    legacy = tmp_path / 'learning_data.json'
    original = {
        'all_words': [_word('apple', '苹果')],
        'mastered_words': [_word('book', '书', 8)],
        'learning_state_v2': {
            'version': 1,
            'review_states': {'apple': {'memory_status': 'learning'}},
            'daily_task': None,
        },
    }
    legacy.write_text(json.dumps(original, ensure_ascii=False), encoding='utf-8')
    original_bytes = legacy.read_bytes()
    store = LearningSQLiteStore(tmp_path / 'learning.sqlite3', legacy)

    pending, mastered, state = store.load()

    assert [row['english'] for row in pending] == ['apple']
    assert [row['english'] for row in mastered] == ['book']
    assert state['review_states']['apple']['memory_status'] == 'learning'
    assert legacy.read_bytes() == original_bytes
    assert store.revision() == 1


def test_incremental_save_only_advances_revision_when_content_changes(tmp_path) -> None:
    legacy = tmp_path / 'learning_data.json'
    legacy.write_text('{}', encoding='utf-8')
    store = LearningSQLiteStore(tmp_path / 'learning.sqlite3', legacy)
    store.load()
    initial = store.revision()
    state = {'version': 1, 'review_states': {}, 'daily_task': None}

    assert store.save([_word('apple', '苹果')], [], state) is True
    changed_revision = store.revision()
    assert changed_revision == initial + 1
    assert store.save([_word('apple', '苹果')], [], state) is False
    assert store.revision() == changed_revision

    assert store.save([_word('apple', '苹果', 1)], [], state) is True
    assert store.revision() == changed_revision + 1


def test_unchanged_save_skips_compression_and_does_not_wait_for_writer(tmp_path, monkeypatch):
    store = LearningSQLiteStore(tmp_path / 'learning.sqlite3', tmp_path / 'legacy.json')
    words = [_word('apple', 'fruit')]
    state = {'review_states': {'apple': {'events': ['unchanged'] * 100}}}
    store.save(words, [], state)
    revision = store.revision()
    statements = []
    original_connect = store._connect

    def connect():
        connection = original_connect()
        connection.execute('PRAGMA busy_timeout=50')
        connection.set_trace_callback(statements.append)
        return connection

    def unexpected_compress(*args, **kwargs):
        pytest.fail('unchanged state must not be compressed again')

    monkeypatch.setattr(store, '_connect', connect)
    monkeypatch.setattr(learning_sqlite_store.zlib, 'compress', unexpected_compress)
    with closing(sqlite3.connect(str(store.db_path))) as writer:
        writer.execute('BEGIN IMMEDIATE')
        try:
            assert store.save(iter(words), iter([]), state) is False
        finally:
            writer.rollback()

    assert 'BEGIN IMMEDIATE' not in statements
    assert store.revision() == revision


def test_save_detects_nested_mutation_of_previously_saved_objects(tmp_path):
    store = LearningSQLiteStore(tmp_path / 'learning.sqlite3', tmp_path / 'legacy.json')
    words = [_word('apple', 'fruit')]
    state = {'review_states': {'apple': {'scheduler': {'active': False}}}}
    store.save(words, [], state)
    revision = store.revision()

    words[0]['review_count'] = 1
    state['review_states']['apple']['scheduler']['active'] = True
    assert store.save(words, [], state) is True

    pending, _, loaded_state = store.load()
    assert pending[0]['review_count'] == 1
    assert loaded_state['review_states']['apple']['scheduler']['active'] is True
    assert store.revision() == revision + 1


def test_save_compares_database_after_another_store_writes(tmp_path):
    store = LearningSQLiteStore(tmp_path / 'learning.sqlite3', tmp_path / 'legacy.json')
    writer = LearningSQLiteStore(store.db_path, store.legacy_json_path)
    words = [_word('apple', 'original')]
    state = {'marker': 'original'}
    store.save(words, [], state)
    writer.save([_word('apple', 'external')], [], {'marker': 'external'})
    revision = store.revision()

    assert store.save(words, [], state) is True
    pending, _, loaded_state = writer.load()
    assert pending[0]['chinese'] == 'original'
    assert loaded_state['marker'] == 'original'
    assert store.revision() == revision + 1


def test_save_rechecks_rows_and_state_when_writer_changes_read_snapshot(tmp_path, monkeypatch):
    store = LearningSQLiteStore(tmp_path / 'learning.sqlite3', tmp_path / 'legacy.json')
    writer = LearningSQLiteStore(store.db_path, store.legacy_json_path)
    state = {'marker': 'original'}
    store.save([_word('apple', 'original')], [], state)
    original_metadata = store._word_metadata
    raced = False

    def metadata(connection):
        nonlocal raced
        value = original_metadata(connection)
        if not raced:
            raced = True
            writer.save(
                [_word('apple', 'external'), _word('banana', 'external')],
                [],
                {'marker': 'external'},
            )
        return value

    monkeypatch.setattr(store, '_word_metadata', metadata)
    assert store.save([_word('apple', 'requested')], [], state) is True
    pending, _, loaded_state = store.load()
    assert raced
    assert [row['english'] for row in pending] == ['apple']
    assert pending[0]['chinese'] == 'requested'
    assert loaded_state['marker'] == 'original'
    assert store.revision() == 4


def test_corrupt_legacy_file_is_never_replaced(tmp_path) -> None:
    legacy = tmp_path / 'learning_data.json'
    legacy.write_text('{broken', encoding='utf-8')
    store = LearningSQLiteStore(tmp_path / 'learning.sqlite3', legacy)

    with pytest.raises(LearningStoreLoadError):
        store.load()

    assert legacy.read_text(encoding='utf-8') == '{broken'


def test_connections_close_after_reads_writes_backups_and_errors(tmp_path, monkeypatch):
    connections = []
    original_connect = sqlite3.connect

    def connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, 'connect', connect)
    legacy = tmp_path / 'learning_data.json'
    store = LearningSQLiteStore(tmp_path / 'learning.sqlite3', legacy)
    store.load()
    store.save([_word('apple', 'fruit')], [], {})
    store.summary()
    store.revision()
    learning_store_revision(store.db_path)
    store.backup_to(tmp_path / 'backup.sqlite3')
    legacy.write_text('{broken', encoding='utf-8')
    broken = LearningSQLiteStore(tmp_path / 'broken.sqlite3', legacy)
    with pytest.raises(LearningStoreLoadError):
        broken.load()

    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            connection.execute('SELECT 1')


def test_first_migration_is_safe_when_two_readers_arrive_together(tmp_path, monkeypatch):
    legacy = tmp_path / 'learning_data.json'
    legacy.write_text(json.dumps({'all_words': [_word('apple', 'fruit')]}), encoding='utf-8')
    stores = [LearningSQLiteStore(tmp_path / 'learning.sqlite3', legacy) for _ in range(2)]
    with closing(stores[0]._connect()) as connection:
        stores[0]._create_schema(connection)
    barrier = Barrier(2)
    for store in stores:
        store._schema_ready = True
        original_read = store._read_legacy

        def read_legacy(read=original_read):
            result = read()
            barrier.wait(timeout=10)
            return result

        monkeypatch.setattr(store, '_read_legacy', read_legacy)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(store.load) for store in stores]
        results = [future.result(timeout=15) for future in futures]
    assert results[0] == results[1]
    assert [word['english'] for word in results[0][0]] == ['apple']
    assert stores[0].revision() == 1


def test_load_reads_words_and_state_from_one_snapshot(tmp_path, monkeypatch):
    legacy = tmp_path / 'learning_data.json'
    reader = LearningSQLiteStore(tmp_path / 'learning.sqlite3', legacy)
    writer = LearningSQLiteStore(reader.db_path, legacy)
    reader.save([_word('apple', 'old')], [], {'marker': 'old'})
    original_decode = learning_sqlite_store._decode
    changed = False

    def decode(payload):
        nonlocal changed
        value = original_decode(payload)
        if isinstance(value, dict) and value.get('english') == 'apple' and not changed:
            changed = True
            writer.save([_word('apple', 'new')], [], {'marker': 'new'})
        return value

    monkeypatch.setattr(learning_sqlite_store, '_decode', decode)
    pending, _, state = reader.load()
    assert changed
    assert pending[0]['chinese'] == 'old'
    assert state['marker'] == 'old'
    assert reader.load()[2]['marker'] == 'new'

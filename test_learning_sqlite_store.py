import json

import pytest

from learning_sqlite_store import LearningSQLiteStore, LearningStoreLoadError


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


def test_corrupt_legacy_file_is_never_replaced(tmp_path) -> None:
    legacy = tmp_path / 'learning_data.json'
    legacy.write_text('{broken', encoding='utf-8')
    store = LearningSQLiteStore(tmp_path / 'learning.sqlite3', legacy)

    with pytest.raises(LearningStoreLoadError):
        store.load()

    assert legacy.read_text(encoding='utf-8') == '{broken'

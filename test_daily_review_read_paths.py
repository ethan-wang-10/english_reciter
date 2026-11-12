"""Regression coverage for indexed daily-review read operations."""

from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app_time import china_today
from reciter import Word, WordReciter


@pytest.fixture
def reciter():
    instance = WordReciter.__new__(WordReciter)
    instance.config = SimpleNamespace(
        MAX_SUCCESS_COUNT=8,
        DAILY_REVIEW_LIMIT=2,
        REVIEW_INTERVAL_DAYS=[1, 2, 4, 7, 15, 30, 60],
    )
    instance.all_words = []
    instance.mastered_words = []
    instance.learning_state_v2 = {"version": 1, "review_states": {}, "daily_task": None}
    instance.today = china_today()
    instance.current_review_round = 0
    return instance


def test_lookup_keeps_pending_first_and_duplicate_identity(reciter):
    first = Word("  APPLE  ", "first")
    second = Word("apple", "second")
    mastered = Word("Apple", "mastered")
    reciter.all_words = [first, second]
    reciter.mastered_words = [mastered]

    with reciter._review_read_scope():
        assert reciter.find_word("apple") is first
        assert reciter.find_word("apple", include_mastered=False) is first
        assert reciter.get_review_state(first)["scheduler"]["active"] is False
        assert reciter.get_review_state(mastered)["scheduler"]["active"] is True

    reciter.all_words.reverse()
    with reciter._review_read_scope():
        assert reciter.find_word("apple") is second


def test_replacement_and_rename_are_visible_on_next_read(reciter):
    original = Word("original", "old")
    reciter.all_words = [original]
    with reciter._review_read_scope():
        assert reciter.find_word("original") is original

    replacement = Word("replacement", "new")
    reciter.all_words[0] = replacement
    with reciter._review_read_scope():
        assert reciter.find_word("original") is None
        assert reciter.find_word("replacement") is replacement

    replacement.english = "renamed"
    with reciter._review_read_scope():
        assert reciter.find_word("replacement") is None
        assert reciter.find_word("renamed") is replacement


def test_bucket_move_is_visible_on_next_read(reciter):
    word = Word("move", "move")
    reciter.all_words = [word]
    with reciter._review_read_scope():
        assert reciter.get_review_state(word)["scheduler"]["active"] is False

    reciter.all_words.remove(word)
    reciter.mastered_words.append(word)
    with reciter._review_read_scope():
        assert reciter.find_word("move", include_mastered=False) is None
        assert reciter.find_word("move") is word
        assert reciter.get_review_state(word)["scheduler"]["active"] is True


def test_future_mastered_duplicate_keeps_legacy_shared_state_defaults(reciter):
    pending = Word("duplicate", "pending", next_review_date=reciter.today)
    mastered = Word(
        "DUPLICATE", "mastered", success_count=8,
        next_review_date=reciter.today + timedelta(days=30),
    )
    reciter.all_words = [pending]
    reciter.mastered_words = [mastered]
    reciter.learning_state_v2["review_states"]["duplicate"] = {
        "mastery": {
            "recognition": {"attempts": 3, "score": 0.9},
            "context": {"attempts": 2, "score": 0.9},
            "spelling": {"attempts": 2, "score": 0.1},
        },
    }

    plan = reciter.get_today_learning_plan()

    assert plan["words"] == [pending]
    assert plan["items"][0]["exercise_type"] == "spelling"
    assert reciter.learning_state_v2["review_states"]["duplicate"]["memory_status"] == "stable"


def test_future_first_mastered_duplicate_preserves_default_seeding_order(reciter):
    future = Word(
        "duplicate", "future", next_review_date=reciter.today + timedelta(days=30),
    )
    due = Word("DUPLICATE", "due", success_count=8, next_review_date=reciter.today)
    reciter.mastered_words = [future, due]
    reciter.learning_state_v2["review_states"]["duplicate"] = {
        "mastery": {
            "recognition": {"attempts": 3, "score": 0.9},
            "context": {"attempts": 2, "score": 0.4},
            "spelling": {"attempts": 2},
        },
    }

    plan = reciter.get_today_learning_plan()

    assert plan["items"][0]["exercise_type"] == "spelling"
    assert reciter.learning_state_v2["review_states"]["duplicate"]["mastery"]["spelling"]["score"] == 0.0


def test_duplicate_pending_refresh_keeps_first_progress_defaults(reciter):
    reciter.all_words = [Word("anchor", "anchor", success_count=1, next_review_date=reciter.today)]
    initial = reciter.get_today_learning_plan()
    progressed = Word("duplicate", "progressed", success_count=8, next_review_date=reciter.today)
    fresh = Word(
        "DUPLICATE", "fresh", next_review_date=reciter.today,
        added_at=reciter.today.isoformat(),
    )
    reciter.all_words.extend([progressed, fresh])

    refreshed = reciter.get_today_learning_plan()

    assert refreshed["plan"]["task_id"] == initial["plan"]["task_id"]
    duplicate_item = next(item for item in refreshed["items"] if item["word_key"] == "duplicate")
    assert duplicate_item["reason"] == "new"
    assert reciter.learning_state_v2["review_states"]["duplicate"]["mastery"]["spelling"]["score"] == 0.8
    assert reciter.learning_state_v2["review_states"]["duplicate"]["scheduler"]["interval_days"] == 60


def test_state_replacement_and_nested_mutation_are_revalidated(reciter):
    word = Word("state", "state")
    reciter.all_words = [word]
    with reciter._review_read_scope():
        initial = reciter.get_review_state(word)

    replacement = deepcopy(initial)
    replacement["scheduler"]["ease_factor"] = 100
    reciter.learning_state_v2["review_states"]["state"] = replacement
    with reciter._review_read_scope():
        normalized = reciter.get_review_state(word)
        assert normalized["scheduler"]["ease_factor"] == 3.2

    normalized["mastery"]["spelling"]["attempts"] = -1
    with reciter._review_read_scope():
        assert reciter.get_review_state(word)["mastery"]["spelling"]["attempts"] == 0


def test_read_scope_is_cleared_after_exception(reciter):
    reciter.all_words = [Word("before", "before")]
    with patch.object(reciter, "_get_today_learning_plan", side_effect=RuntimeError("read failed")):
        with pytest.raises(RuntimeError, match="read failed"):
            reciter.get_today_learning_plan()

    reciter.all_words = [Word("after", "after")]
    assert reciter.find_word("before") is None
    assert reciter.find_word("after") is reciter.all_words[0]
    assert getattr(reciter, "_review_lookup", None) is None
    assert getattr(reciter, "_normalized_review_states", None) is None
    assert reciter.get_today_learning_plan()["words"] == reciter.all_words


def test_nested_read_scope_leaves_outer_snapshot_usable(reciter):
    word = Word("nested", "nested")
    reciter.all_words = [word]
    with reciter._review_read_scope():
        outer = reciter._review_lookup
        assert reciter.count_due_words() == 1
        assert reciter._review_lookup is outer
        assert reciter.find_word("nested") is word
    assert reciter._review_lookup is None


@pytest.mark.parametrize("new_words_first", [False, True])
def test_filtered_refresh_matches_full_candidate_import_order(reciter, new_words_first):
    today = reciter.today
    due = Word("due", "due", success_count=1, review_count=1, next_review_date=today)
    old_new = Word("old-new", "old-new", next_review_date=today)
    reciter.all_words = [due, old_new]
    initial = reciter.get_today_learning_plan()
    preserved_key = "old-new" if new_words_first else "due"
    preserved_item = next(item for item in initial["items"] if item["word_key"] == preserved_key)
    preserved_item["attempts"] = 1
    imported = [
        Word("import-z", "z", next_review_date=today, added_at=today.isoformat()),
        Word("import-a", "a", next_review_date=today, added_at=today.isoformat()),
    ]
    reciter.all_words.extend(imported)
    full = reciter._today_task_candidates()
    expected_new = [
        candidate["word"] for candidate in full
        if candidate["reason"] == "new"
        and (new_words_first or candidate["imported_today"])
        and candidate["word"].english != "old-new"
    ][0]

    refreshed = reciter.get_today_learning_plan(new_words_first=new_words_first)

    assert preserved_item in refreshed["items"]
    assert {word.english for word in refreshed["words"]} == {preserved_key, expected_new.english}
    assert refreshed["plan"]["backlog_after_task"] == 2


def test_backlog_updates_when_unselected_word_moves_to_future(reciter):
    reciter.config.DAILY_REVIEW_LIMIT = 1
    reciter.all_words = [
        Word("a", "a", success_count=1, review_count=1, next_review_date=reciter.today),
        Word("b", "b", success_count=1, review_count=1, next_review_date=reciter.today),
    ]
    initial = reciter.get_today_learning_plan()
    assert initial["plan"]["backlog_after_task"] == 1
    unselected = next(word for word in reciter.all_words if word not in initial["words"])
    unselected.next_review_date = reciter.today + timedelta(days=10)

    refreshed = reciter.get_today_learning_plan()

    assert refreshed["plan"]["task_id"] == initial["plan"]["task_id"]
    assert refreshed["plan"]["backlog_after_task"] == 0


def test_next_day_rebuilds_task_from_current_word_dates(reciter):
    current = Word("today", "today", success_count=1, next_review_date=reciter.today)
    tomorrow = Word("tomorrow", "tomorrow", success_count=1, next_review_date=reciter.today + timedelta(days=1))
    reciter.all_words = [current, tomorrow]
    initial = reciter.get_today_learning_plan()
    assert initial["words"] == [current]
    reciter.today += timedelta(days=1)

    refreshed = reciter.get_today_learning_plan()

    assert refreshed["plan"]["task_id"] != initial["plan"]["task_id"]
    assert set(refreshed["words"]) == {current, tomorrow}


def test_new_first_priority_reacts_to_in_place_review_count_change(reciter):
    new = Word("new", "new", next_review_date=reciter.today)
    due = Word("due", "due", success_count=1, review_count=1, next_review_date=reciter.today)
    reciter.all_words = [due, new]
    assert reciter.get_today_learning_plan(new_words_first=True)["words"] == [new, due]
    new.review_count = 1

    assert reciter.get_today_learning_plan(new_words_first=True)["words"] == [due, new]

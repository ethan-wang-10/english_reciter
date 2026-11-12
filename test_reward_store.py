import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pytest

import challenges as ch
import gamification as gm
import leaderboard_periods as periods
import reward_store
from storage_utils import PersistentStateLoadError


@contextmanager
def _database(root):
    connection = sqlite3.connect(reward_store.database_path(root))
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _account(root, username, balance=500, daily_xp=None):
    state = gm.default_state()
    state.update(lifetime_xp=balance, xp_balance=balance, total_xp=balance)
    if daily_xp is not None:
        state["daily_xp"] = daily_xp
    gm.save_state(root, username, state)


def test_legacy_migration_is_once_and_keeps_original(tmp_path):
    source = gm.gamification_path(tmp_path, "alice")
    source.parent.mkdir()
    original = '{"total_xp": 725}'
    source.write_text(original, encoding="utf-8")

    assert gm.load_state(tmp_path, "alice")["xp_balance"] == 725
    assert source.read_text(encoding="utf-8") == original
    source.write_text('{"total_xp": 1}', encoding="utf-8")
    assert gm.apply_xp_delta(tmp_path, "alice", 10, transaction_id="reward:1")[0]
    assert gm.load_state(tmp_path, "alice")["xp_balance"] == 735
    assert source.read_text(encoding="utf-8") == '{"total_xp": 1}'


@pytest.mark.parametrize("kind,value", [
    ("game", {"daily_xp": []}),
    ("game", {"daily_xp": {"2026-07-01": "12"}}),
    ("game", {"streak_correct_by_day": {"bad-date": 5}}),
    ("game", {"xp_balance": True}),
    ("game", {"xp_account_version": 2, "total_xp": 100}),
    ("game", {"xp_account_version": 999, "total_xp": 100}),
    ("game", {"xp_gain_history": {"2026-07-01": []}}),
    ("game", {"makeup_checkins": {"2026-07-01": {"cost_xp": -1}}}),
    ("game", {"xp_transactions": {"tx": {"balance_delta": 1, "lifetime_delta": "1", "source": "manual"}}}),
    ("game", {"practice_events": [{"event_id": "review", "payload": []}]}),
    ("game", {"practice_events": [{"event_id": "review", "scope_encoding": "sha256-v1", "scope": "bad-hash", "payload": {}}]}),
    ("game", {"practice_events": [{"event_id": "review", "payload": {"makeup_checkin": []}}]}),
    ("game", {"achievements": {"first_step": {"corrupted": True}}}),
    ("game", {"unknown": float("nan")}),
    ("pool", {"months": []}),
    ("pool", {"months": {"2026-06": {}}}),
    ("pool", {"months": {"2026-06": {"participants": ["alice", "alice"], "pool_xp": 300}}}),
    ("pool", {"months": {"2026-06": {"participants": ["alice"], "counts": {"alice": []}}}}),
    ("duels", {"duels": ["corrupted"]}),
    ("duels", {"duels": [{"id": "a", "from_user": "alice", "target_user": "bob", "status": "active", "wager_xp": 50}]}),
    ("periods", {"weeks": []}),
    ("periods", {"months": {"2026-06": {}}}),
    ("periods", {"weeks": {"2026-W99": {"top": []}}}),
    ("periods", {"months": {"2026-06": {"top": [{"username": "alice", "rank": "1", "period_xp": 10, "reward_xp": 100}]}}}),
])
def test_invalid_nested_legacy_state_fails_without_migration(tmp_path, kind, value):
    if kind == "game":
        source = gm.gamification_path(tmp_path, "alice")
        load = lambda: gm.load_state(tmp_path, "alice")
    elif kind == "pool":
        source = ch.monthly_pool_path(tmp_path)
        load = lambda: ch._load_json(source, {"months": {}})
    elif kind == "duels":
        source = ch.duels_path(tmp_path)
        load = lambda: ch._load_duels(tmp_path)
    else:
        source = periods._rewards_path(tmp_path)
        load = lambda: periods._load_json(source, {"weeks": {}, "months": {}})
    source.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(value)
    source.write_text(content, encoding="utf-8")

    with pytest.raises(PersistentStateLoadError):
        load()
    assert source.read_text(encoding="utf-8") == content
    with _database(tmp_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM reward_documents").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM reward_events").fetchone()[0] == 0


@pytest.mark.parametrize("failure", ["second_account", "challenge_marker", "commit"])
def test_duel_acceptance_rolls_back_all_accounts_and_marker(tmp_path, monkeypatch, failure):
    for username in ("alice", "bob"):
        _account(tmp_path, username)
    ok, _, duel = ch.create_duel(tmp_path, "alice", "bob", wager_xp=100)
    assert ok
    saw_second = False

    def fail(stage, key):
        nonlocal saw_second
        if stage == "after_save" and key == "bob/gamification.json":
            saw_second = True
            if failure == "second_account":
                raise OSError("injected account failure")
        if saw_second and failure == "challenge_marker" and stage == "after_save" and key == "_challenges/duels.json":
            raise OSError("injected marker failure")
        if saw_second and failure == "commit" and stage == "before_commit":
            raise OSError("injected commit failure")

    monkeypatch.setattr(reward_store, "_fault_hook", fail)
    with pytest.raises(OSError):
        ch.respond_duel(tmp_path, duel["id"], "bob", True)
    monkeypatch.setattr(reward_store, "_fault_hook", lambda *_: None)
    assert [gm.load_state(tmp_path, u)["xp_balance"] for u in ("alice", "bob")] == [500, 500]
    assert ch._load_duels(tmp_path)["duels"][0]["status"] == "pending"
    assert reward_store.get_event(tmp_path, "alice", "xp", f"duel:{duel['id']}:stake:alice") is None
    assert ch.respond_duel(tmp_path, duel["id"], "bob", True)[0]
    assert [gm.load_state(tmp_path, u)["xp_balance"] for u in ("alice", "bob")] == [400, 400]


def test_pool_join_rolls_back_debit_if_membership_save_fails(tmp_path, monkeypatch):
    _account(tmp_path, "alice")
    monkeypatch.setattr(ch, "china_today", lambda: date(2026, 7, 2))

    def fail(stage, key):
        if stage == "after_save" and key == "_challenges/monthly_pool.json":
            raise OSError("injected pool save failure")

    monkeypatch.setattr(reward_store, "_fault_hook", fail)
    with pytest.raises(OSError):
        ch.join_monthly_pool(tmp_path, "alice")
    monkeypatch.setattr(reward_store, "_fault_hook", lambda *_: None)
    assert gm.load_state(tmp_path, "alice")["xp_balance"] == 500
    assert ch._load_json(ch.monthly_pool_path(tmp_path), {"months": {}}) == {"months": {}}
    assert ch.join_monthly_pool(tmp_path, "alice")[0]
    assert gm.load_state(tmp_path, "alice")["xp_balance"] == 350


def test_insufficient_second_stake_leaves_first_account_unchanged(tmp_path):
    _account(tmp_path, "alice")
    _account(tmp_path, "bob", balance=100)
    _, _, duel = ch.create_duel(tmp_path, "alice", "bob", wager_xp=100)
    before = gm.load_state(tmp_path, "alice")
    ok, message, _ = ch.respond_duel(tmp_path, duel["id"], "bob", True)
    assert not ok and "应战方" in message
    assert gm.load_state(tmp_path, "alice") == before
    assert ch._load_duels(tmp_path)["duels"][0]["status"] == "pending"


def test_bad_second_legacy_account_rolls_back_first_migration(tmp_path):
    for username, content in (("alice", '{"total_xp": 500}'), ("bob", '{"daily_xp": []}')):
        path = gm.gamification_path(tmp_path, username)
        path.parent.mkdir()
        path.write_text(content, encoding="utf-8")
    _, _, duel = ch.create_duel(tmp_path, "alice", "bob", wager_xp=100)
    with pytest.raises(PersistentStateLoadError):
        ch.respond_duel(tmp_path, duel["id"], "bob", True)
    with _database(tmp_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM reward_documents WHERE document_key IN (?, ?)",
            ("alice/gamification.json", "bob/gamification.json"),
        ).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM reward_events").fetchone()[0] == 0
    assert gm.gamification_path(tmp_path, "alice").read_text(encoding="utf-8") == '{"total_xp": 500}'


def test_pool_payout_failure_rolls_back_other_winners(tmp_path, monkeypatch):
    for username in ("alice", "bob"):
        _account(tmp_path, username)
    ch._save_json(ch.monthly_pool_path(tmp_path), {"months": {
        "2026-06": {"participants": ["alice", "bob"], "pool_xp": 300},
    }})
    monkeypatch.setattr(ch, "china_today", lambda: date(2026, 7, 1))

    def fail(stage, key):
        if stage == "after_save" and key == "bob/gamification.json":
            raise OSError("injected winner failure")

    monkeypatch.setattr(reward_store, "_fault_hook", fail)
    with pytest.raises(OSError):
        ch._settle_monthly_pool_if_needed(tmp_path)
    monkeypatch.setattr(reward_store, "_fault_hook", lambda *_: None)
    assert [gm.load_state(tmp_path, u)["xp_balance"] for u in ("alice", "bob")] == [500, 500]
    block = ch._load_json(ch.monthly_pool_path(tmp_path), {"months": {}})["months"]["2026-06"]
    assert not block.get("settled")
    ch._settle_monthly_pool_if_needed(tmp_path)
    ch._settle_monthly_pool_if_needed(tmp_path)
    assert [gm.load_state(tmp_path, u)["xp_balance"] for u in ("alice", "bob")] == [650, 650]


def test_leaderboard_failure_rolls_back_all_rewards_and_settlement(tmp_path, monkeypatch):
    for username in ("alice", "bob"):
        _account(tmp_path, username, daily_xp={"2026-07-01": 100})
    monkeypatch.setattr(periods, "china_today", lambda: date(2026, 7, 6))

    def fail(stage, key):
        if stage == "after_save" and key == "bob/gamification.json":
            raise OSError("injected podium failure")

    monkeypatch.setattr(reward_store, "_fault_hook", fail)
    with pytest.raises(OSError):
        periods.settle_periods_if_needed(tmp_path, ["alice", "bob"])
    monkeypatch.setattr(reward_store, "_fault_hook", lambda *_: None)
    assert [gm.load_state(tmp_path, u)["xp_balance"] for u in ("alice", "bob")] == [500, 500]
    assert periods._load_json(periods._rewards_path(tmp_path), {"weeks": {}, "months": {}}) == {"weeks": {}, "months": {}}
    periods.settle_periods_if_needed(tmp_path, ["alice", "bob"])
    periods.settle_periods_if_needed(tmp_path, ["alice", "bob"])
    assert [gm.load_state(tmp_path, u)["xp_balance"] for u in ("alice", "bob")] == [645, 645]


def test_xp_idempotency_survives_history_pruning(tmp_path, monkeypatch):
    monkeypatch.setattr(gm, "XP_TRANSACTION_LIMIT", 2)
    for index in range(3):
        assert gm.apply_xp_delta(tmp_path, "alice", 10, transaction_id=f"reward:{index}")[0]
    assert "reward:0" not in gm.load_state(tmp_path, "alice")["xp_transactions"]
    assert gm.apply_xp_delta(tmp_path, "alice", 10, transaction_id="reward:0")[0]
    assert gm.load_state(tmp_path, "alice")["xp_balance"] == 30
    assert not gm.apply_xp_delta(tmp_path, "alice", 20, transaction_id="reward:0")[0]


@pytest.mark.parametrize("table,key,payload", [
    ("reward_documents", "alice/gamification.json", '{"xp_balance": []}'),
    ("reward_events", "reward:0", '{"balance_delta": [], "lifetime_delta": 10, "source": "manual"}'),
])
def test_corrupt_sqlite_payload_fails_without_overwrite(tmp_path, table, key, payload):
    gm.apply_xp_delta(tmp_path, "alice", 10, transaction_id="reward:0")
    column = "document_key" if table == "reward_documents" else "event_key"
    with _database(tmp_path) as connection:
        connection.execute(f"UPDATE {table} SET payload=? WHERE {column}=?", (payload, key))
    with pytest.raises(PersistentStateLoadError):
        gm.apply_xp_delta(tmp_path, "alice", 10, transaction_id="reward:0")
    with _database(tmp_path) as connection:
        assert connection.execute(f"SELECT payload FROM {table} WHERE {column}=?", (key,)).fetchone()[0] == payload


def test_duplicate_event_insert_validates_committed_payload(tmp_path):
    event = {"balance_delta": 10, "lifetime_delta": 10, "source": "manual"}
    reward_store.put_event(tmp_path, "alice", "xp", "reward:0", event)
    with _database(tmp_path) as connection:
        connection.execute("UPDATE reward_events SET payload='null'")
    with pytest.raises(PersistentStateLoadError):
        reward_store.put_event(tmp_path, "alice", "xp", "reward:0", event)


def test_connections_close_after_successful_and_failed_transactions(tmp_path, monkeypatch):
    original_connect = sqlite3.connect
    opened = []

    def connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(reward_store.sqlite3, "connect", connect)
    assert gm.apply_xp_delta(tmp_path, "alice", 10, transaction_id="first")[0]

    def fail(stage, _key):
        if stage == "after_save":
            raise OSError("injected failure")

    monkeypatch.setattr(reward_store, "_fault_hook", fail)
    with pytest.raises(OSError):
        gm.apply_xp_delta(tmp_path, "alice", 10, transaction_id="second")
    monkeypatch.setattr(reward_store, "_fault_hook", lambda *_: None)
    assert gm.load_state(tmp_path, "alice")["xp_balance"] == 10
    assert opened
    for connection in opened:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


def test_practice_idempotency_survives_payload_cache_pruning(tmp_path, monkeypatch):
    monkeypatch.setattr(gm, "PRACTICE_EVENT_LIMIT", 2)
    kwargs = dict(bonus_practice=False, remedial=False, old_success_count=0,
                  new_success_count=1, mastered_now=False, mastered_words=0, event_scope="apple")
    first = gm.award_correct_answer(tmp_path, "alice", event_id="review:0", **kwargs)
    for index in (1, 2):
        gm.award_correct_answer(tmp_path, "alice", event_id=f"review:{index}", **kwargs)
    assert len(gm.load_state(tmp_path, "alice")["practice_events"]) == 2
    assert gm.award_correct_answer(tmp_path, "alice", event_id="review:0", **kwargs) == first
    assert gm.load_state(tmp_path, "alice")["total_correct"] == 3


def test_migration_seeds_historical_idempotency_records(tmp_path, monkeypatch):
    monkeypatch.setattr(gm, "XP_TRANSACTION_LIMIT", 1)
    source = gm.gamification_path(tmp_path, "alice")
    source.parent.mkdir()
    source.write_text(json.dumps({"total_xp": 10, "xp_transactions": {
        "old-reward": {"balance_delta": 10, "lifetime_delta": 10, "source": "manual"},
    }}), encoding="utf-8")
    gm.apply_xp_delta(tmp_path, "alice", 5, transaction_id="new-reward")
    gm.apply_xp_delta(tmp_path, "alice", 10, transaction_id="old-reward")
    assert gm.load_state(tmp_path, "alice")["xp_balance"] == 15


def test_user_purge_cannot_resurrect_legacy_account(tmp_path):
    source = gm.gamification_path(tmp_path, "alice")
    source.parent.mkdir()
    source.write_text('{"total_xp": 725}', encoding="utf-8")
    assert gm.load_state(tmp_path, "alice")["xp_balance"] == 725
    ch.purge_user_challenges_refs(tmp_path, "alice")
    assert gm.load_state(tmp_path, "alice")["xp_balance"] == 0
    assert source.read_text(encoding="utf-8") == '{"total_xp": 725}'


def test_process_crash_before_commit_leaves_no_partial_reward(tmp_path):
    _account(tmp_path, "alice")
    _account(tmp_path, "bob")
    _, _, duel = ch.create_duel(tmp_path, "alice", "bob", wager_xp=100)
    code = """
import os, sys
from pathlib import Path
import challenges as ch
import gamification as gm
import reward_store
root = Path(sys.argv[1])
with reward_store.transaction(root):
    gm.apply_xp_delta(root, 'alice', 30, transaction_id='crash-reward')
    assert ch.respond_duel(root, sys.argv[2], 'bob', True)[0]
    os._exit(17)
"""
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path), duel["id"]], cwd=Path(__file__).parent,
                            capture_output=True, timeout=30)
    assert result.returncode == 17, result.stderr.decode("utf-8", "replace")
    assert gm.load_state(tmp_path, "alice")["xp_balance"] == 500
    assert gm.load_state(tmp_path, "bob")["xp_balance"] == 500
    assert ch._load_duels(tmp_path)["duels"][0]["status"] == "pending"
    assert reward_store.get_event(tmp_path, "alice", "xp", "crash-reward") is None


def test_multiple_processes_share_atomic_idempotency(tmp_path):
    _account(tmp_path, "alice", balance=0)
    code = """
import sys
from pathlib import Path
import gamification as gm
for index in range(8):
    assert gm.apply_xp_delta(Path(sys.argv[1]), 'alice', 10, transaction_id=f'parallel:{index}')[0]
"""

    def run():
        return subprocess.run([sys.executable, "-c", code, str(tmp_path)], cwd=Path(__file__).parent,
                              capture_output=True, timeout=60, env={**os.environ, "PYTHONIOENCODING": "utf-8"})

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(lambda _: run(), range(3)))
    for result in results:
        assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    state = gm.load_state(tmp_path, "alice")
    assert state["xp_balance"] == state["lifetime_xp"] == 80

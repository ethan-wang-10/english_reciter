"""Challenge polls should scale with current participants, not PK history."""

from collections import Counter
from datetime import date

import pytest

import challenges as ch
import gamification as gm


def _duel(identifier, *, target="bob", **values):
    return {
        "id": identifier,
        "from_user": "alice",
        "target_user": target,
        "status": "active",
        "month": "2026-11",
        "wager_xp": 0,
        "created_at": "2026-11-01T12:00:00",
        "accepted_at": "2026-11-01T12:00:00",
        "settled": False,
        **values,
    }


def _save_checkins(root, username, days):
    state = gm.default_state()
    state["streak_correct_by_day"] = {day: gm.CHECKIN_MIN_CORRECT for day in days}
    gm.save_state(root, username, state)


@pytest.fixture(autouse=True)
def fixed_day(monkeypatch):
    monkeypatch.setattr(ch, "china_today", lambda: date(2026, 11, 12))


def test_settled_history_uses_payout_scores_without_loading_accounts(tmp_path, monkeypatch):
    duels = [
        _duel(str(index), month="2026-10", accepted_at="2026-10-01T12:00:00",
              settled=True, days_a=index, days_b=0, winner="alice")
        for index in range(30)
    ]
    ch._save_duels(tmp_path, {"duels": duels})
    monkeypatch.setattr(gm, "load_state", lambda *_: pytest.fail("settled scores are already persisted"))

    rows = ch.list_duels_for_user(tmp_path, "alice")

    assert len(rows) == len(duels)
    assert {row["id"]: row["pk_checkin_days"] for row in rows} == {
        str(index): {"alice": index, "bob": 0} for index in range(30)
    }


def test_active_duels_reuse_accounts_but_next_poll_sees_new_checkins(tmp_path, monkeypatch):
    _save_checkins(tmp_path, "alice", ["2026-11-01", "2026-11-02"])
    _save_checkins(tmp_path, "bob", ["2026-11-02", "2026-11-03"])
    _save_checkins(tmp_path, "carol", ["2026-11-02"])
    ch._save_duels(tmp_path, {"duels": [
        _duel("early"),
        _duel("late", accepted_at="2026-11-02T12:00:00"),
        _duel("other", target="carol"),
    ]})
    load_state = gm.load_state
    loads = Counter()

    def counted_load(root, username):
        loads[username] += 1
        return load_state(root, username)

    monkeypatch.setattr(gm, "load_state", counted_load)
    rows = {row["id"]: row for row in ch.list_duels_for_user(tmp_path, "alice")}

    assert loads == {"alice": 1, "bob": 1, "carol": 1}
    assert rows["early"]["pk_checkin_days"] == {"alice": 1, "bob": 2}
    assert rows["late"]["pk_checkin_days"] == {"alice": 0, "bob": 1}
    assert rows["other"]["pk_checkin_days"] == {"alice": 1, "carol": 1}

    _save_checkins(tmp_path, "alice", ["2026-11-02", "2026-11-03"])
    loads.clear()
    rows = {row["id"]: row for row in ch.list_duels_for_user(tmp_path, "alice")}
    assert loads == {"alice": 1, "bob": 1, "carol": 1}
    assert rows["early"]["pk_checkin_days"] == {"alice": 2, "bob": 2}
    assert rows["late"]["pk_checkin_days"] == {"alice": 1, "bob": 1}


def test_legacy_settled_duels_reconstruct_only_missing_scores(tmp_path, monkeypatch):
    _save_checkins(tmp_path, "bob", ["2026-10-02"])
    ch._save_duels(tmp_path, {"duels": [
        _duel(str(index), month="2026-10", accepted_at="2026-10-01T12:00:00",
              settled=True, days_a=0)
        for index in range(3)
    ]})
    load_state = gm.load_state
    loads = []

    def counted_load(root, username):
        loads.append(username)
        return load_state(root, username)

    monkeypatch.setattr(gm, "load_state", counted_load)
    rows = ch.list_duels_for_user(tmp_path, "alice")

    assert loads == ["bob"]
    assert all(row["pk_checkin_days"] == {"alice": 0, "bob": 1} for row in rows)


def test_month_rollover_persists_scores_and_pays_only_once(tmp_path, monkeypatch):
    _save_checkins(tmp_path, "alice", ["2026-10-02", "2026-10-03"])
    _save_checkins(tmp_path, "bob", ["2026-10-02"])
    ch._save_duels(tmp_path, {"duels": [
        _duel("last-month", month="2026-10", accepted_at="2026-10-01T12:00:00", wager_xp=50),
    ]})

    first = ch.list_duels_for_user(tmp_path, "alice")[0]
    assert first["settled"] is True
    assert first["winner"] == "alice"
    assert first["pk_checkin_days"] == {"alice": 2, "bob": 1}
    assert gm.load_state(tmp_path, "alice")["xp_balance"] == 100

    with monkeypatch.context() as scoped:
        scoped.setattr(gm, "load_state", lambda *_: pytest.fail("completed settlement must be reused"))
        assert ch.list_duels_for_user(tmp_path, "alice")[0] == first

    assert gm.load_state(tmp_path, "alice")["xp_balance"] == 100


def test_pending_and_empty_counting_ranges_do_not_load_accounts(tmp_path, monkeypatch):
    ch._save_duels(tmp_path, {"duels": [
        _duel("pending", status="pending", accepted_at=None, expires_at="2099-11-30T23:59:59"),
        _duel("too-late", accepted_at="2026-11-30T23:59:59"),
    ]})
    monkeypatch.setattr(gm, "load_state", lambda *_: pytest.fail("no PK counting days"))

    assert all(row["pk_checkin_days"] == {"alice": 0, "bob": 0}
               for row in ch.list_duels_for_user(tmp_path, "alice"))

from datetime import date
from pathlib import Path

import pytest

import challenges
import gamification
import leaderboard_periods
from storage_utils import PersistentStateLoadError, read_json_state


def _mutation(data_dir, kind, monkeypatch):
    if kind == "gamification":
        return (
            gamification.gamification_path(data_dir, "alice"),
            lambda: gamification.apply_xp_delta(data_dir, "alice", 10, source="manual"),
        )
    if kind == "duels":
        return (
            challenges.duels_path(data_dir),
            lambda: challenges.create_duel(data_dir, "alice", "bob", wager_xp=0),
        )
    if kind == "monthly_pool":
        monkeypatch.setattr(challenges, "china_today", lambda: date(2026, 5, 2))
        return (
            challenges.monthly_pool_path(data_dir),
            lambda: challenges.join_monthly_pool(data_dir, "alice"),
        )
    return (
        leaderboard_periods._rewards_path(data_dir),
        lambda: leaderboard_periods.settle_periods_if_needed(
            data_dir, ["alice"], {"alice": gamification.default_state()},
        ),
    )


@pytest.mark.parametrize("kind", ["gamification", "duels", "monthly_pool", "leaderboard"])
@pytest.mark.parametrize("content", [b'{"balance": 123', b"[]", b"null", b"\xff", b""])
def test_corrupted_state_is_preserved_when_mutation_is_attempted(tmp_path, monkeypatch, kind, content):
    path, mutate = _mutation(tmp_path, kind, monkeypatch)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)

    with pytest.raises(PersistentStateLoadError):
        mutate()

    assert path.read_bytes() == content


@pytest.mark.parametrize("kind", ["gamification", "duels", "monthly_pool", "leaderboard"])
def test_read_failure_never_initializes_or_overwrites_state(tmp_path, monkeypatch, kind):
    path, mutate = _mutation(tmp_path, kind, monkeypatch)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = b'{"preserved": true}'
    path.write_bytes(content)
    original_open = Path.open

    def deny_state_read(current_path, mode="r", *args, **kwargs):
        if current_path == path and mode == "r":
            raise PermissionError("temporarily unreadable")
        return original_open(current_path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_state_read)
    with pytest.raises(PersistentStateLoadError) as error:
        mutate()
    assert isinstance(error.value.__cause__, PermissionError)
    assert path.read_bytes() == content


def test_missing_state_still_initializes_normally(tmp_path):
    assert read_json_state(tmp_path / "missing.json", {"items": []}) == {"items": []}
    assert gamification.load_state(tmp_path, "alice") == gamification.default_state()
    assert challenges._load_duels(tmp_path) == {"duels": []}
    assert leaderboard_periods._load_json(
        tmp_path / "missing-rewards.json", {"weeks": {}, "months": {}},
    ) == {"weeks": {}, "months": {}}
    assert gamification.apply_xp_delta(tmp_path, "alice", 10, source="manual")[0]
    assert gamification.load_state(tmp_path, "alice")["xp_balance"] == 10

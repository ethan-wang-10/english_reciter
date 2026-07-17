import json
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import gamification as gm
import leaderboard_periods as periods


def _seed_accounts(data_dir: Path, username: str, xp: int) -> None:
    state = gm.default_state()
    state["lifetime_xp"] = xp
    state["xp_balance"] = xp
    state["total_xp"] = xp
    gm.save_state(data_dir, username, state)


def test_legacy_total_xp_migrates_to_lifetime_and_balance(tmp_path: Path) -> None:
    user_dir = tmp_path / "alice"
    user_dir.mkdir()
    (user_dir / "gamification.json").write_text(
        json.dumps({"total_xp": 725}), encoding="utf-8"
    )

    state = gm.load_state(tmp_path, "alice")

    assert state["lifetime_xp"] == 725
    assert state["xp_balance"] == 725
    assert state["total_xp"] == 725


def test_spending_balance_does_not_reduce_level_or_lifetime_xp(tmp_path: Path) -> None:
    _seed_accounts(tmp_path, "alice", 1000)
    level_before = gm.level_from_xp(1000)

    ok, msg, balance = gm.spend_xp_with_reserve(tmp_path, "alice", 300)

    assert ok, msg
    state = gm.load_state(tmp_path, "alice")
    assert balance == 700
    assert state["xp_balance"] == 700
    assert state["lifetime_xp"] == 1000
    assert gm.level_from_xp(state["lifetime_xp"]) == level_before


def test_transaction_id_makes_reward_retry_idempotent(tmp_path: Path) -> None:
    for _ in range(2):
        ok, msg, _ = gm.apply_xp_delta(
            tmp_path,
            "alice",
            180,
            source="weekly_reward",
            transaction_id="leaderboard:week:2026-W20:reward:alice",
        )
        assert ok, msg

    state = gm.load_state(tmp_path, "alice")
    assert state["lifetime_xp"] == 180
    assert state["xp_balance"] == 180


def test_monthly_goal_only_counts_new_valid_days(tmp_path: Path) -> None:
    today = date(2026, 7, 17)
    state = gm.default_state()
    state["streak_correct_by_day"] = {
        (date(2026, 7, 1) + timedelta(days=index)).isoformat(): gm.CHECKIN_MIN_CORRECT
        for index in range(10)
    }
    gm.save_state(tmp_path, "alice", state)

    with patch.object(gm, "china_today", return_value=today):
        result = gm.patch_settings(tmp_path, "alice", monthly_checkin_goal=1)
        assert result["monthly_goal_bonus_just_granted_xp"] == 0
        state = gm.load_state(tmp_path, "alice")
        assert state["mcheckin_goal_baseline_days"] == 10
        state["streak_correct_by_day"][today.isoformat()] = gm.CHECKIN_MIN_CORRECT
        gm.save_state(tmp_path, "alice", state)
        assert gm.try_grant_monthly_checkin_goal_bonus(tmp_path, "alice") == 30


def test_tied_period_scores_share_podium_rewards() -> None:
    states = {}
    for username in ("alice", "bob", "carl"):
        state = gm.default_state()
        state["daily_xp"] = {"2026-07-01": 100}
        states[username] = state
    rows = periods.build_period_leaderboard_from_states(
        states,
        list(states),
        viewer="",
        start=date(2026, 7, 1),
        end=date(2026, 7, 1),
    )

    allocations = periods.podium_reward_allocations(rows, periods.WEEK_REWARD_XP)

    assert [row["rank"] for row in rows] == [1, 1, 1]
    assert [reward for _, reward in allocations] == [120, 120, 120]


def test_opt_out_hides_display_but_not_settlement_eligibility(tmp_path: Path) -> None:
    opted_out = gm.default_state()
    opted_out["leaderboard_opt_in"] = False
    opted_out["daily_xp"] = {"2026-07-01": 200}
    visible = gm.default_state()
    visible["daily_xp"] = {"2026-07-01": 100}
    states = {"private": opted_out, "public": visible}
    raw = {"weeks": {}, "months": {}}

    periods._settle_one_week(
        tmp_path,
        list(states),
        week_id="2026-W27",
        mon=date(2026, 6, 29),
        sun=date(2026, 7, 5),
        raw=raw,
        states=states,
    )

    assert gm.load_state(tmp_path, "private")["xp_balance"] == periods.WEEK_REWARD_XP[0]
    assert raw["weeks"]["2026-W27"]["top"][0]["username"] == "匿名用户"

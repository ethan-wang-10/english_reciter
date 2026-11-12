"""Validate persisted reward structures before migration or mutation."""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any


def _require(condition: bool, field: str) -> None:
    if not condition:
        raise ValueError(f"Invalid reward field: {field}")


def _object(value: Any, field: str) -> dict:
    _require(isinstance(value, dict), field)
    return value


def _integer(value: Any, field: str, *, signed: bool = False) -> None:
    _require(type(value) is int and (signed or value >= 0), field)


def _text(value: Any, field: str, *, empty: bool = False) -> None:
    _require(isinstance(value, str) and (empty or bool(value.strip())), field)


def _boolean(value: Any, field: str) -> None:
    _require(type(value) is bool, field)


def _day(value: Any, field: str) -> None:
    _text(value, field)
    _require(date.fromisoformat(value).isoformat() == value, field)


def _month(value: Any, field: str) -> None:
    _text(value, field)
    _require(len(value) == 7 and date.fromisoformat(value + "-01").strftime("%Y-%m") == value, field)


def _timestamp(value: Any, field: str, *, empty: bool = False) -> None:
    _text(value, field, empty=empty)
    if value:
        datetime.fromisoformat(value)


def _json(value: Any, field: str = "root") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            _require(isinstance(key, str), field)
            _json(child, f"{field}.{key}")
    elif isinstance(value, list):
        for child in value:
            _json(child, field)
    else:
        _require(value is None or type(value) in (str, int, bool, float), field)
        if isinstance(value, float):
            _require(math.isfinite(value), field)


def _strings(value: Any, field: str) -> None:
    _require(isinstance(value, list), field)
    for item in value:
        _text(item, field)
    _require(len(set(value)) == len(value), field)


def _optional_fields(row: dict, fields: tuple[str, ...], validate, *, nullable: bool = False) -> None:
    for field in fields:
        if field in row and not (nullable and row[field] is None):
            validate(row[field], field)


def _makeup_offer(value: Any) -> None:
    row = _object(value, "makeup_checkin")
    _optional_fields(row, ("available", "eligible", "can_afford"), _boolean)
    _optional_fields(row, ("days_ago", "cost_xp", "streak_before", "current_xp", "month_makeups_used", "monthly_limit"), _integer, nullable=True)
    _optional_fields(row, ("xp_after_purchase",), lambda v, f: _integer(v, f, signed=True), nullable=True)
    _optional_fields(row, ("target_date", "prior_streak_date"), _day, nullable=True)
    _optional_fields(row, ("reason", "reason_code"), lambda v, f: _text(v, f, empty=True))
    if "rules" in row:
        rules = _object(row["rules"], "makeup_checkin.rules")
        _optional_fields(rules, ("target",), _text)
        _optional_fields(rules, ("counts_for_streak", "counts_for_monthly_goal", "counts_for_pool_or_pk"), _boolean)


def gamification_state(value: Any) -> None:
    row = _object(value, "gamification")
    _json(row)
    _optional_fields(row, (
        "xp_account_version", "lifetime_xp", "xp_balance", "total_xp", "total_correct", "streak",
    ), _integer)
    version = row.get("xp_account_version", 0)
    _require(version in (0, 1, 2), "xp_account_version")
    if version == 2:
        _integer(row.get("lifetime_xp"), "lifetime_xp")
        _integer(row.get("xp_balance"), "xp_balance")
    _optional_fields(row, ("streak_max", "mcheckin_goal", "mcheckin_goal_baseline_days"), _integer, nullable=True)
    _optional_fields(row, ("last_streak_date",), _day, nullable=True)
    _optional_fields(row, (
        "mcheckin_goal_month", "mcheckin_goal_bonus_awarded_month", "mcheckin_goal_edits_ym",
    ), _month, nullable=True)
    if "leaderboard_opt_in" in row:
        _require(type(row["leaderboard_opt_in"]) is bool, "leaderboard_opt_in")
    for field in ("daily_xp", "streak_correct_by_day"):
        if field in row:
            for day, count in _object(row[field], field).items():
                _day(day, field)
                _integer(count, field)
    if "xp_gain_history" in row:
        for day, sources in _object(row["xp_gain_history"], "xp_gain_history").items():
            _day(day, "xp_gain_history")
            for source, amount in _object(sources, "xp_gain_history.sources").items():
                _text(source, "xp_gain_history.source")
                _integer(amount, "xp_gain_history.amount", signed=True)
    if "achievements" in row:
        for name, unlocked in _object(row["achievements"], "achievements").items():
            _text(name, "achievement.id")
            _timestamp(unlocked, "achievement.unlocked_at")
    if "makeup_checkins" in row:
        for day, makeup in _object(row["makeup_checkins"], "makeup_checkins").items():
            _day(day, "makeup_checkins.day")
            _object(makeup, "makeup_checkins.entry")
            _optional_fields(makeup, ("cost_xp", "streak_before"), _integer)
            _optional_fields(makeup, ("created_at",), _timestamp, nullable=True)
            _optional_fields(makeup, ("policy",), _text)
    if "xp_transactions" in row:
        for event_key, event in _object(row["xp_transactions"], "xp_transactions").items():
            _text(event_key, "xp_transactions.id")
            _object(event, "xp_transactions.entry")
            for field in ("balance_delta", "lifetime_delta"):
                _integer(event.get(field), field, signed=field == "balance_delta")
            _text(event.get("source"), "xp_transactions.source")
            _optional_fields(event, ("created_at",), lambda v, f: _timestamp(v, f, empty=True))
    if "practice_events" in row:
        _require(isinstance(row["practice_events"], list), "practice_events")
        for event in row["practice_events"]:
            _object(event, "practice_events.entry")
            _text(event.get("event_id"), "practice_events.event_id")
            _object(event.get("payload"), "practice_events.payload")
            _optional_fields(event, ("scope", "scope_encoding"), lambda v, f: _text(v, f, empty=True))
            encoding = event.get("scope_encoding", "")
            _require(encoding in ("", "sha256-v1"), "practice_events.scope_encoding")
            if encoding == "sha256-v1":
                scope = event.get("scope", "")
                _require(len(scope) == 71 and scope.startswith("sha256:") and all(
                    char in "0123456789abcdef" for char in scope[7:]
                ), "practice_events.scope")
            payload = event["payload"]
            _optional_fields(payload, (
                "xp_gained", "answer_xp_gained", "checkin_bonus_xp", "checkin_bonus_raw_xp",
                "raw_xp", "monthly_goal_bonus_xp", "total_xp", "lifetime_xp", "xp_balance",
                "level", "xp_to_next_level", "streak", "daily_xp_today", "today_correct_count",
                "check_in_min_correct",
            ), _integer)
            _optional_fields(payload, ("check_in_done_today",), _boolean)
            if "makeup_checkin" in payload:
                _makeup_offer(payload["makeup_checkin"])
            if "new_achievements" in payload:
                _require(isinstance(payload["new_achievements"], list), "practice_events.new_achievements")
                for achievement in payload["new_achievements"]:
                    _object(achievement, "practice_events.achievement")
                    _text(achievement.get("id"), "practice_events.achievement.id")
                    _optional_fields(achievement, ("title", "desc", "icon"), _text)
                    _optional_fields(achievement, ("unlocked_at",), _timestamp)


def monthly_pool(value: Any) -> None:
    row = _object(value, "monthly_pool")
    _json(row)
    for month, block in _object(row.get("months", {}), "months").items():
        _month(month, "months.key")
        _object(block, "months.block")
        _integer(block.get("pool_xp"), "pool_xp")
        _strings(block.get("participants"), "participants")
        _optional_fields(block, ("pool_xp", "winner_days", "per_winner_xp", "remainder_xp"), _integer)
        for field in ("participants", "winners"):
            if field in block:
                _strings(block[field], field)
        if "settled" in block:
            _require(type(block["settled"]) is bool, "settled")
        _optional_fields(block, ("settled_at",), _timestamp)
        _optional_fields(block, ("settlement_note",), _text)
        for user, count in _object(block.get("counts", {}), "counts").items():
            _text(user, "counts.username")
            _integer(count, "counts.days")


def duels(value: Any) -> None:
    row = _object(value, "duels")
    _json(row)
    records = row.get("duels", [])
    _require(isinstance(records, list), "duels")
    ids = set()
    for duel in records:
        _object(duel, "duel")
        for field in ("id", "from_user", "target_user", "status"):
            _text(duel.get(field), field)
        _require(duel["id"] not in ids, "duel.id.duplicate")
        ids.add(duel["id"])
        _require(duel["from_user"] != duel["target_user"], "duel.users")
        _require(duel["status"] in {"pending", "active", "declined", "expired"}, "duel.status")
        _integer(duel.get("wager_xp"), "wager_xp")
        _timestamp(duel.get("created_at"), "created_at")
        _optional_fields(duel, ("month",), _month, nullable=True)
        _optional_fields(duel, ("escrow_xp", "days_a", "days_b"), _integer)
        _optional_fields(duel, ("created_at", "expires_at", "accepted_at", "expired_at", "settled_at"), _timestamp, nullable=True)
        _optional_fields(duel, ("pk_stats_start_date", "pk_stats_end_date"), _day)
        for field in ("settled", "tie"):
            if field in duel:
                _require(type(duel[field]) is bool, field)
        if duel.get("winner") is not None:
            _require(duel["winner"] in (duel["from_user"], duel["target_user"]), "duel.winner")
        if duel["status"] == "active":
            _month(duel.get("month"), "duel.active.month")


def period_rewards(value: Any) -> None:
    row = _object(value, "period_rewards")
    _json(row)
    for kind in ("weeks", "months"):
        for key, block in _object(row.get(kind, {}), kind).items():
            if kind == "months":
                _month(key, "period.month")
            else:
                _require(isinstance(key, str) and len(key) == 8 and key[4:6] == "-W", "period.week")
                date.fromisocalendar(int(key[:4]), int(key[6:]), 1)
            _object(block, "period.block")
            _timestamp(block.get("settled_at"), "settled_at")
            _day(block.get("period_start"), "period_start")
            _day(block.get("period_end"), "period_end")
            _require(block["period_start"] <= block["period_end"], "period.bounds")
            _optional_fields(block, ("settled_at",), _timestamp)
            _optional_fields(block, ("period_start", "period_end"), _day)
            _optional_fields(block, ("period_label",), _text)
            top = block.get("top")
            _require(isinstance(top, list), "period.top")
            for winner in top:
                _object(winner, "period.winner")
                _text(winner.get("username"), "period.username")
                for field in ("rank", "period_xp", "reward_xp"):
                    _integer(winner.get(field), field)

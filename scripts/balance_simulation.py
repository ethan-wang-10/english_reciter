"""Monte Carlo balance smoke test for gamification economy.

The model is intentionally lightweight and deterministic so it can be run in CI or
before changing balance constants:

    py -3 scripts/balance_simulation.py

It compares the old production constants with the current code-backed proposal.
The target is not to predict exact user behavior; it is to catch runaway gaps and
verify that steady check-ins stay competitive with bursty grinding.
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import gamification as gm
import leaderboard_periods as periods


CHECKIN_MIN_CORRECT = gm.CHECKIN_MIN_CORRECT


@dataclass(frozen=True)
class BalanceConfig:
    name: str
    daily_soft_cap: int
    over_cap_multiplier: float
    daily_hard_cap: Optional[int]
    checkin_completion_xp: int
    checkin_streak_xp_per_day: int
    checkin_streak_cap_days: int
    monthly_goal_xp_per_day: int
    week_rewards: Tuple[int, int, int]
    month_rewards: Tuple[int, int, int]


PERSONAS = {
    "at_risk": {"n": 220, "p": 0.32, "mean": 5, "sd": 2, "goal": 8},
    "casual": {"n": 300, "p": 0.55, "mean": 7, "sd": 2, "goal": 12},
    "steady": {"n": 300, "p": 0.88, "mean": 12, "sd": 4, "goal": 22},
    "committed": {"n": 240, "p": 0.92, "mean": 28, "sd": 8, "goal": 26},
    "weekend_burst": {
        "n": 160,
        "p": 0.25,
        "p_weekend": 0.78,
        "mean": 65,
        "sd": 18,
        "goal": 10,
    },
    "grinder": {"n": 120, "p": 0.82, "mean": 95, "sd": 25, "goal": 24},
}


ITERATIONS = [
    BalanceConfig(
        name="legacy_soft_only",
        daily_soft_cap=300,
        over_cap_multiplier=0.5,
        daily_hard_cap=None,
        checkin_completion_xp=0,
        checkin_streak_xp_per_day=0,
        checkin_streak_cap_days=0,
        monthly_goal_xp_per_day=30,
        week_rewards=(200, 120, 80),
        month_rewards=(600, 360, 240),
    ),
    BalanceConfig(
        name="iter1_hard600_over25_checkin",
        daily_soft_cap=300,
        over_cap_multiplier=0.25,
        daily_hard_cap=600,
        checkin_completion_xp=20,
        checkin_streak_xp_per_day=1,
        checkin_streak_cap_days=30,
        monthly_goal_xp_per_day=30,
        week_rewards=(200, 120, 80),
        month_rewards=(600, 360, 240),
    ),
    BalanceConfig(
        name="iter2_hard500_over20_checkin",
        daily_soft_cap=300,
        over_cap_multiplier=0.2,
        daily_hard_cap=500,
        checkin_completion_xp=20,
        checkin_streak_xp_per_day=1,
        checkin_streak_cap_days=30,
        monthly_goal_xp_per_day=30,
        week_rewards=(180, 110, 70),
        month_rewards=(540, 330, 220),
    ),
    BalanceConfig(
        name="current_code_soft1000_no_hard",
        daily_soft_cap=gm.DAILY_XP_SOFT_CAP,
        over_cap_multiplier=gm.OVER_CAP_MULTIPLIER,
        daily_hard_cap=None,
        checkin_completion_xp=gm.CHECKIN_COMPLETION_XP,
        checkin_streak_xp_per_day=gm.CHECKIN_STREAK_BONUS_XP_PER_DAY,
        checkin_streak_cap_days=gm.CHECKIN_STREAK_BONUS_CAP_DAYS,
        monthly_goal_xp_per_day=gm.CHECKIN_GOAL_XP_PER_DAY,
        week_rewards=periods.WEEK_REWARD_XP,
        month_rewards=periods.MONTH_REWARD_XP,
    ),
]


def _apply_cap(daily_so_far: int, raw_xp: int, cfg: BalanceConfig) -> int:
    if raw_xp <= 0:
        return 0
    if cfg.daily_hard_cap is not None and daily_so_far >= cfg.daily_hard_cap:
        return 0
    if daily_so_far >= cfg.daily_soft_cap:
        gain = max(1, int(raw_xp * cfg.over_cap_multiplier))
    elif daily_so_far + raw_xp <= cfg.daily_soft_cap:
        gain = raw_xp
    else:
        room = cfg.daily_soft_cap - daily_so_far
        gain = room + max(1, int((raw_xp - room) * cfg.over_cap_multiplier))
    if cfg.daily_hard_cap is None:
        return gain
    return min(gain, max(0, cfg.daily_hard_cap - daily_so_far))


def _answer_raw_xp(rng: random.Random) -> int:
    raw = gm.XP_PLAN_CORRECT
    if rng.random() < 0.68:
        raw += gm.XP_PROGRESS_STEP
    if rng.random() < 0.035:
        raw += gm.XP_MASTERED
    return raw


def _checkin_bonus_raw(streak_after_checkin: int, cfg: BalanceConfig) -> int:
    streak = min(max(0, int(streak_after_checkin)), cfg.checkin_streak_cap_days)
    return cfg.checkin_completion_xp + streak * cfg.checkin_streak_xp_per_day


def simulate(cfg: BalanceConfig, *, seed: int, days: int) -> Dict[str, Dict[str, float]]:
    rng = random.Random(seed)
    users: List[Dict[str, object]] = []
    for persona, profile in PERSONAS.items():
        for _ in range(int(profile["n"])):
            users.append(
                {
                    "persona": persona,
                    "xp": 0,
                    "valid_days": 0,
                    "current_streak": 0,
                    "max_streak": 0,
                    "last_valid_day": None,
                    "month_valid": {},
                    "month_bonus_awarded": set(),
                    "period_xp": {},
                }
            )

    start = date(2026, 1, 1)
    for day_idx in range(days):
        today = start + timedelta(days=day_idx)
        ym = today.strftime("%Y-%m")
        iso = today.isocalendar()
        week_key = f"{iso.year}-W{iso.week:02d}"

        for user in users:
            persona = str(user["persona"])
            profile = PERSONAS[persona]
            active_p = float(profile.get("p_weekend", profile["p"])) if today.weekday() >= 5 else float(profile["p"])
            correct = 0
            if rng.random() < active_p:
                correct = max(1, int(rng.gauss(float(profile["mean"]), float(profile["sd"]))))

            daily_xp = 0
            crossed_checkin = False
            for idx in range(correct):
                daily_xp += _apply_cap(daily_xp, _answer_raw_xp(rng), cfg)
                if not crossed_checkin and idx + 1 >= CHECKIN_MIN_CORRECT:
                    crossed_checkin = True
                    prev_day = today - timedelta(days=1)
                    streak_after = (
                        int(user["current_streak"]) + 1
                        if user.get("last_valid_day") == prev_day
                        else 1
                    )
                    daily_xp += _apply_cap(
                        daily_xp,
                        _checkin_bonus_raw(streak_after, cfg),
                        cfg,
                    )

            user["xp"] = int(user["xp"]) + daily_xp
            period_xp = user["period_xp"]
            assert isinstance(period_xp, dict)
            period_xp[("week", week_key)] = int(period_xp.get(("week", week_key), 0)) + daily_xp
            period_xp[("month", ym)] = int(period_xp.get(("month", ym), 0)) + daily_xp

            if correct >= CHECKIN_MIN_CORRECT:
                user["valid_days"] = int(user["valid_days"]) + 1
                month_valid = user["month_valid"]
                assert isinstance(month_valid, dict)
                month_valid[ym] = int(month_valid.get(ym, 0)) + 1
                if user.get("last_valid_day") == today - timedelta(days=1):
                    user["current_streak"] = int(user["current_streak"]) + 1
                else:
                    user["current_streak"] = 1
                user["last_valid_day"] = today
                user["max_streak"] = max(int(user["max_streak"]), int(user["current_streak"]))
            elif user.get("last_valid_day") != today - timedelta(days=1):
                user["current_streak"] = 0

            month_valid = user["month_valid"]
            awarded = user["month_bonus_awarded"]
            assert isinstance(month_valid, dict)
            assert isinstance(awarded, set)
            goal = int(profile["goal"])
            if int(month_valid.get(ym, 0)) >= goal and ym not in awarded:
                user["xp"] = int(user["xp"]) + goal * cfg.monthly_goal_xp_per_day
                awarded.add(ym)

        if today.weekday() == 6:
            _settle_period(users, ("week", week_key), cfg.week_rewards)
        next_day = today + timedelta(days=1)
        if next_day.month != today.month or day_idx == days - 1:
            _settle_period(users, ("month", ym), cfg.month_rewards)

    return _summarize(users)


def _settle_period(
    users: Iterable[Dict[str, object]],
    key: Tuple[str, str],
    rewards: Tuple[int, int, int],
) -> None:
    rows = sorted(
        users,
        key=lambda u: (-int(dict(u["period_xp"]).get(key, 0)), str(u["persona"])),
    )
    for reward, user in zip(rewards, rows[:3]):
        if int(dict(user["period_xp"]).get(key, 0)) > 0:
            user["xp"] = int(user["xp"]) + reward


def _summarize(users: List[Dict[str, object]]) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    for persona in PERSONAS:
        rows = [u for u in users if u["persona"] == persona]
        xp = [int(u["xp"]) for u in rows]
        valid = [int(u["valid_days"]) for u in rows]
        streak = [int(u["max_streak"]) for u in rows]
        out[persona] = {
            "xp_p50": statistics.median(xp),
            "xp_p90": statistics.quantiles(xp, n=10)[8],
            "valid_p50": statistics.median(valid),
            "streak_p50": statistics.median(streak),
        }
    return out


def print_report(results: Dict[str, Dict[str, Dict[str, float]]]) -> None:
    order = ["at_risk", "casual", "steady", "committed", "weekend_burst", "grinder"]
    for cfg_name, summary in results.items():
        print(f"\n== {cfg_name}")
        for persona in order:
            row = summary[persona]
            print(
                f"{persona:14s} xp50={row['xp_p50']:7.0f} "
                f"xp90={row['xp_p90']:7.0f} valid50={row['valid_p50']:4.0f} "
                f"streak50={row['streak_p50']:4.0f}"
            )
        steady = summary["steady"]["xp_p50"]
        print(
            "ratios "
            f"grinder50/steady50={summary['grinder']['xp_p50'] / steady:.2f} "
            f"grinder90/steady50={summary['grinder']['xp_p90'] / steady:.2f} "
            f"burst50/steady50={summary['weekend_burst']['xp_p50'] / steady:.2f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--days", type=int, default=90)
    args = parser.parse_args()

    results = {
        cfg.name: simulate(cfg, seed=args.seed, days=args.days)
        for cfg in ITERATIONS
    }
    print_report(results)


if __name__ == "__main__":
    main()

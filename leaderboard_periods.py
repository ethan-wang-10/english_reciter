"""
周榜 / 月榜：按自然周（周一至周日，ISO 周）与自然月汇总 daily_xp；惰性结算上期前三名奖励。
数据：user_data_simple/_leaderboard/period_rewards.json
"""

from __future__ import annotations

import json
import threading
from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app_time import china_now_iso, china_today
import gamification as gamification_mod

# 周榜 / 月榜前三名奖励 XP（第 1～3 名）
WEEK_REWARD_XP: Tuple[int, int, int] = (200, 120, 80)
MONTH_REWARD_XP: Tuple[int, int, int] = (600, 360, 240)

_period_lock = threading.RLock()


def _leaderboard_dir(data_dir: Path) -> Path:
    p = data_dir / "_leaderboard"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _rewards_path(data_dir: Path) -> Path:
    return _leaderboard_dir(data_dir) / "period_rewards.json"


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def iso_week_id(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_monday_sunday(d: date) -> Tuple[date, date]:
    y, w, _ = d.isocalendar()
    mon = date.fromisocalendar(y, w, 1)
    sun = date.fromisocalendar(y, w, 7)
    return mon, sun


def last_completed_week_sunday(today: date) -> date:
    """上一完整周的周日（若 today 为周日，则返回「上周日」）。"""
    if today.weekday() == 6:
        return today - timedelta(days=7)
    return today - timedelta(days=today.weekday() + 1)


def month_calendar_bounds(ym: str) -> Tuple[date, date]:
    parts = ym.split("-")
    if len(parts) != 2:
        raise ValueError
    y, m = int(parts[0]), int(parts[1])
    dim = monthrange(y, m)[1]
    return date(y, m, 1), date(y, m, dim)


def sum_daily_xp_in_range(state: Dict[str, Any], start: date, end: date) -> int:
    dx = state.get("daily_xp") or {}
    if not isinstance(dx, dict):
        return 0
    total = 0
    d = start
    while d <= end:
        k = d.isoformat()
        try:
            total += int(dx.get(k, 0))
        except (TypeError, ValueError):
            pass
        d += timedelta(days=1)
    return total


def period_label_cn(start: date, end: date) -> str:
    if start.year == end.year:
        if start.month == end.month:
            return f"{start.year}年{start.month}月{start.day}日—{end.day}日"
        return f"{start.year}年{start.month}月{start.day}日—{end.month}月{end.day}日"
    return (
        f"{start.year}年{start.month}月{start.day}日—"
        f"{end.year}年{end.month}月{end.day}日"
    )


def build_period_leaderboard_from_states(
    states: Dict[str, Dict[str, Any]],
    usernames: List[str],
    *,
    viewer: str,
    start: date,
    end: date,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    today = china_today()
    for un in usernames:
        st = states.get(un)
        if st is None:
            continue
        if not st.get("leaderboard_opt_in", True):
            continue
        px = sum_daily_xp_in_range(st, start, end)
        xp = int(st.get("total_xp") or 0)
        ach_n = len(st.get("achievements") or {})
        streak = gamification_mod.display_streak(st, today)
        streak_max = gamification_mod.streak_max_record_display(st, today)
        if gamification_mod.streak_v2_active(today):
            streak_max = max(streak, streak_max)
        rows.append(
            {
                "username": un,
                "total_xp": xp,
                "period_xp": px,
                "level": gamification_mod.level_from_xp(xp),
                "streak": streak,
                "streak_max_record": streak_max,
                "achievements_count": ach_n,
                "is_viewer": un == viewer,
            }
        )
    rows.sort(key=lambda r: (-r["period_xp"], r["username"]))
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
    return rows


def build_period_leaderboard(
    data_dir: Path,
    usernames: List[str],
    *,
    viewer: str,
    start: date,
    end: date,
) -> List[Dict[str, Any]]:
    states = gamification_mod.load_states_batch(data_dir, usernames)
    return build_period_leaderboard_from_states(
        states, usernames, viewer=viewer, start=start, end=end
    )


def _iter_weeks_back(last_sunday: date, max_weeks: int) -> List[Tuple[str, date, date]]:
    out: List[Tuple[str, date, date]] = []
    cur = last_sunday
    for _ in range(max_weeks):
        y, w, _ = cur.isocalendar()
        wid = f"{y}-W{w:02d}"
        mon = date.fromisocalendar(y, w, 1)
        sun = date.fromisocalendar(y, w, 7)
        out.append((wid, mon, sun))
        cur = cur - timedelta(days=7)
    return out


def _iter_months_back(today: date, max_months: int) -> List[Tuple[str, date, date]]:
    out: List[Tuple[str, date, date]] = []
    first = date(today.year, today.month, 1)
    cur_end = first - timedelta(days=1)
    for _ in range(max_months):
        y, m = cur_end.year, cur_end.month
        ym = f"{y}-{m:02d}"
        mon = date(y, m, 1)
        dim = monthrange(y, m)[1]
        last_d = date(y, m, dim)
        out.append((ym, mon, last_d))
        cur_end = mon - timedelta(days=1)
    return out


def _settle_one_week(
    data_dir: Path,
    usernames: List[str],
    *,
    week_id: str,
    mon: date,
    sun: date,
    raw: Dict[str, Any],
    states: Dict[str, Dict[str, Any]],
) -> None:
    rows = build_period_leaderboard_from_states(
        states, usernames, viewer="", start=mon, end=sun
    )
    top: List[Dict[str, Any]] = []
    for i in range(min(3, len(rows))):
        r = rows[i]
        px = int(r["period_xp"])
        un = str(r["username"])
        rank = i + 1
        reward = 0
        if px > 0:
            reward = WEEK_REWARD_XP[i]
            ok, _, _ = gamification_mod.apply_xp_delta(data_dir, un, reward)
            if not ok:
                reward = 0
        top.append(
            {
                "username": un,
                "rank": rank,
                "period_xp": px,
                "reward_xp": reward,
            }
        )
    weeks = raw.setdefault("weeks", {})
    weeks[week_id] = {
        "settled_at": china_now_iso(timespec="seconds"),
        "period_start": mon.isoformat(),
        "period_end": sun.isoformat(),
        "period_label": period_label_cn(mon, sun),
        "top": top,
    }


def _settle_one_month(
    data_dir: Path,
    usernames: List[str],
    *,
    ym: str,
    mon: date,
    last_d: date,
    raw: Dict[str, Any],
    states: Dict[str, Dict[str, Any]],
) -> None:
    rows = build_period_leaderboard_from_states(
        states, usernames, viewer="", start=mon, end=last_d
    )
    top: List[Dict[str, Any]] = []
    for i in range(min(3, len(rows))):
        r = rows[i]
        px = int(r["period_xp"])
        un = str(r["username"])
        rank = i + 1
        reward = 0
        if px > 0:
            reward = MONTH_REWARD_XP[i]
            ok, _, _ = gamification_mod.apply_xp_delta(data_dir, un, reward)
            if not ok:
                reward = 0
        top.append(
            {
                "username": un,
                "rank": rank,
                "period_xp": px,
                "reward_xp": reward,
            }
        )
    months = raw.setdefault("months", {})
    months[ym] = {
        "settled_at": china_now_iso(timespec="seconds"),
        "period_start": mon.isoformat(),
        "period_end": last_d.isoformat(),
        "period_label": period_label_cn(mon, last_d),
        "top": top,
    }


def settle_periods_if_needed(
    data_dir: Path,
    usernames: List[str],
    states: Optional[Dict[str, Dict[str, Any]]] = None,
) -> None:
    """惰性结算：从最近已结束周/月往回，连续未结算的周、月（单次请求有上限）。

    若传入 states（与 usernames 对应的 gamification 快照），则结算与排名计算不再重复读盘。
    """
    if states is None:
        states = gamification_mod.load_states_batch(data_dir, usernames)
    today = china_today()
    path = _rewards_path(data_dir)
    with _period_lock:
        raw = _load_json(path, {"weeks": {}, "months": {}})
        if not isinstance(raw, dict):
            raw = {"weeks": {}, "months": {}}
        weeks = raw.setdefault("weeks", {})
        months = raw.setdefault("months", {})
        if not isinstance(weeks, dict):
            weeks = {}
            raw["weeks"] = weeks
        if not isinstance(months, dict):
            months = {}
            raw["months"] = months

        last_sun = last_completed_week_sunday(today)
        to_week: List[Tuple[str, date, date]] = []
        for wid, mon, sun in _iter_weeks_back(last_sun, 104):
            if wid in weeks:
                break
            to_week.append((wid, mon, sun))
            if len(to_week) >= 8:
                break
        to_week.sort(key=lambda x: x[1])
        for wid, mon, sun in to_week:
            _settle_one_week(
                data_dir,
                usernames,
                week_id=wid,
                mon=mon,
                sun=sun,
                raw=raw,
                states=states,
            )
        if to_week:
            _save_json(path, raw)

        to_month: List[Tuple[str, date, date]] = []
        for ym, mon, last_d in _iter_months_back(today, 36):
            if ym in months:
                break
            to_month.append((ym, mon, last_d))
            if len(to_month) >= 6:
                break
        to_month.sort(key=lambda x: x[1])
        for ym, mon, last_d in to_month:
            _settle_one_month(
                data_dir,
                usernames,
                ym=ym,
                mon=mon,
                last_d=last_d,
                raw=raw,
                states=states,
            )
        if to_month:
            _save_json(path, raw)


def _last_settled_week_info_from_raw(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    weeks = raw.get("weeks") or {}
    if not isinstance(weeks, dict) or not weeks:
        return None
    best_key: Optional[str] = None
    best_end: Optional[str] = None
    for k, v in weeks.items():
        if not isinstance(v, dict):
            continue
        pe = v.get("period_end") or ""
        if best_end is None or pe > best_end:
            best_end = pe
            best_key = k
    if not best_key:
        return None
    block = weeks.get(best_key) or {}
    return {"period_id": best_key, **block}


def _last_settled_month_info_from_raw(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    months = raw.get("months") or {}
    if not isinstance(months, dict) or not months:
        return None
    best_key: Optional[str] = None
    best_end: Optional[str] = None
    for k, v in months.items():
        if not isinstance(v, dict):
            continue
        pe = v.get("period_end") or ""
        if best_end is None or pe > best_end:
            best_end = pe
            best_key = k
    if not best_key:
        return None
    block = months.get(best_key) or {}
    return {"period_id": best_key, **block}


def _last_settled_week_info(data_dir: Path) -> Optional[Dict[str, Any]]:
    raw = _load_json(_rewards_path(data_dir), {"weeks": {}, "months": {}})
    return _last_settled_week_info_from_raw(raw)


def _last_settled_month_info(data_dir: Path) -> Optional[Dict[str, Any]]:
    raw = _load_json(_rewards_path(data_dir), {"weeks": {}, "months": {}})
    return _last_settled_month_info_from_raw(raw)


def build_week_leaderboard_payload(
    data_dir: Path,
    usernames: List[str],
    *,
    viewer: str,
    states: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    if states is None:
        states = gamification_mod.load_states_batch(data_dir, usernames)
    today = china_today()
    mon, sun = week_monday_sunday(today)
    end = min(today, sun)
    rows = build_period_leaderboard_from_states(
        states, usernames, viewer=viewer, start=mon, end=end
    )
    wid = iso_week_id(today)
    rewards_raw = _load_json(_rewards_path(data_dir), {"weeks": {}, "months": {}})
    week_last = _last_settled_week_info_from_raw(rewards_raw)
    return {
        "scope": "week",
        "period_id": wid,
        "period_label": period_label_cn(mon, sun),
        "period_start": mon.isoformat(),
        "period_end": sun.isoformat(),
        "period_sum_end": end.isoformat(),
        "leaderboard": rows,
        "podium_last_period": week_last,
    }


def build_month_leaderboard_payload(
    data_dir: Path,
    usernames: List[str],
    *,
    viewer: str,
    states: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    if states is None:
        states = gamification_mod.load_states_batch(data_dir, usernames)
    today = china_today()
    ym = today.strftime("%Y-%m")
    mon, last_d = month_calendar_bounds(ym)
    end = min(today, last_d)
    rows = build_period_leaderboard_from_states(
        states, usernames, viewer=viewer, start=mon, end=end
    )
    rewards_raw = _load_json(_rewards_path(data_dir), {"weeks": {}, "months": {}})
    month_last = _last_settled_month_info_from_raw(rewards_raw)
    return {
        "scope": "month",
        "period_id": ym,
        "period_label": period_label_cn(mon, last_d),
        "period_start": mon.isoformat(),
        "period_end": last_d.isoformat(),
        "period_sum_end": end.isoformat(),
        "leaderboard": rows,
        "podium_last_period": month_last,
    }

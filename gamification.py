"""
学习游戏化：XP、连续打卡、成就、排行榜元数据。
数据文件：user_data_simple/<username>/gamification.json
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------- 数值与上限 ----------
XP_PLAN_CORRECT = 10
XP_PROGRESS_STEP = 5
XP_MASTERED = 40
XP_REMEDIAL = 4
XP_BONUS_PRACTICE = 3

DAILY_XP_SOFT_CAP = 300
OVER_CAP_MULTIPLIER = 0.5

MAX_LEVEL = 99

# 成就 id -> 展示信息
ACHIEVEMENT_DEFS: Dict[str, Dict[str, str]] = {
    "first_step": {"title": "第一步", "desc": "首次答对单词", "icon": "👣"},
    "streak_3": {"title": "三连击", "desc": "连续打卡 3 天", "icon": "🔥"},
    "streak_7": {"title": "一周坚持", "desc": "连续打卡 7 天", "icon": "⭐"},
    "streak_30": {"title": "月度冠军", "desc": "连续打卡 30 天", "icon": "🏆"},
    "word_master_1": {"title": "初窥门径", "desc": "累计掌握 1 个单词", "icon": "📗"},
    "word_master_10": {"title": "词汇积累", "desc": "累计掌握 10 个单词", "icon": "📚"},
    "word_master_50": {"title": "单词达人", "desc": "累计掌握 50 个单词", "icon": "🎓"},
    "xp_1k": {"title": "千分学者", "desc": "累计获得 1000 XP", "icon": "💎"},
    "xp_10k": {"title": "万分传奇", "desc": "累计获得 10000 XP", "icon": "🌟"},
}


def gamification_path(data_dir: Path, username: str) -> Path:
    return data_dir / username / "gamification.json"


def default_state() -> Dict[str, Any]:
    return {
        "total_xp": 0,
        "total_correct": 0,
        "streak": 0,
        "last_streak_date": None,
        "daily_xp": {},
        "achievements": {},
        "leaderboard_opt_in": True,
    }


def load_state(data_dir: Path, username: str) -> Dict[str, Any]:
    path = gamification_path(data_dir, username)
    if not path.exists():
        return default_state()
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return default_state()
    if not isinstance(raw, dict):
        return default_state()
    base = default_state()
    for k in base:
        if k in raw:
            base[k] = raw[k]
    if "achievements" in raw and isinstance(raw["achievements"], dict):
        base["achievements"] = dict(raw["achievements"])
    if "daily_xp" in raw and isinstance(raw["daily_xp"], dict):
        base["daily_xp"] = {str(k): int(v) for k, v in raw["daily_xp"].items()}
    return base


def save_state(data_dir: Path, username: str, state: Dict[str, Any]) -> None:
    path = gamification_path(data_dir, username)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def level_from_xp(total_xp: int) -> int:
    if total_xp <= 0:
        return 1
    lv = 1 + int(math.sqrt(total_xp / 100.0))
    return min(MAX_LEVEL, max(1, lv))


def xp_to_next_level(total_xp: int) -> Tuple[int, int]:
    """当前等级、距离下一级还需要的 XP。"""
    lv = level_from_xp(total_xp)
    if lv >= MAX_LEVEL:
        return lv, 0
    next_threshold = lv * lv * 100
    need = max(0, next_threshold - total_xp)
    return lv, need


def _apply_daily_cap(daily_so_far: int, raw_xp: int) -> int:
    if raw_xp <= 0:
        return 0
    if daily_so_far >= DAILY_XP_SOFT_CAP:
        return int(raw_xp * OVER_CAP_MULTIPLIER)
    if daily_so_far + raw_xp <= DAILY_XP_SOFT_CAP:
        return raw_xp
    room = DAILY_XP_SOFT_CAP - daily_so_far
    over = raw_xp - room
    return room + int(over * OVER_CAP_MULTIPLIER)


def _update_streak(state: Dict[str, Any], today: date) -> None:
    key = today.isoformat()
    last = state.get("last_streak_date")
    streak = int(state.get("streak") or 0)

    # 当日有答对即算打卡（含 XP 被软上限减为 0 的情况）
    by_day = state.get("streak_correct_by_day") or {}
    earned_today = int(by_day.get(key, 0)) > 0 or int(state["daily_xp"].get(key, 0)) > 0
    if not earned_today:
        return

    if not last:
        state["streak"] = 1
        state["last_streak_date"] = key
        return

    try:
        last_d = date.fromisoformat(str(last))
    except ValueError:
        state["streak"] = 1
        state["last_streak_date"] = key
        return

    if last_d == today:
        return
    if last_d == today - timedelta(days=1):
        streak += 1
        state["streak"] = streak
        state["last_streak_date"] = key
    else:
        state["streak"] = 1
        state["last_streak_date"] = key


def compute_raw_xp(
    *,
    bonus_practice: bool,
    remedial: bool,
    success_increased: bool,
    mastered_now: bool,
) -> int:
    if bonus_practice:
        return XP_BONUS_PRACTICE
    if remedial:
        return XP_REMEDIAL
    raw = XP_PLAN_CORRECT
    if success_increased:
        raw += XP_PROGRESS_STEP
    if mastered_now:
        raw += XP_MASTERED
    return raw


def _unlock_achievements(
    state: Dict[str, Any],
    *,
    mastered_words: int,
) -> List[Dict[str, Any]]:
    """根据当前状态解锁成就，返回本次新解锁列表（含 meta）。"""
    new_list: List[Dict[str, Any]] = []
    total_xp = int(state.get("total_xp") or 0)
    total_correct = int(state.get("total_correct") or 0)
    streak = int(state.get("streak") or 0)
    ach = state.setdefault("achievements", {})
    assert isinstance(ach, dict)

    def grant(aid: str) -> None:
        if aid in ach:
            return
        if aid not in ACHIEVEMENT_DEFS:
            return
        now = datetime.now().isoformat(timespec="seconds")
        ach[aid] = now
        meta = dict(ACHIEVEMENT_DEFS[aid])
        meta["id"] = aid
        meta["unlocked_at"] = now
        new_list.append(meta)

    if total_correct >= 1:
        grant("first_step")
    if streak >= 3:
        grant("streak_3")
    if streak >= 7:
        grant("streak_7")
    if streak >= 30:
        grant("streak_30")
    if mastered_words >= 1:
        grant("word_master_1")
    if mastered_words >= 10:
        grant("word_master_10")
    if mastered_words >= 50:
        grant("word_master_50")
    if total_xp >= 1000:
        grant("xp_1k")
    if total_xp >= 10000:
        grant("xp_10k")

    return new_list


def award_correct_answer(
    data_dir: Path,
    username: str,
    *,
    bonus_practice: bool,
    remedial: bool,
    old_success_count: int,
    new_success_count: int,
    mastered_now: bool,
    mastered_words: int,
) -> Dict[str, Any]:
    """
    答对后加分、更新 streak、解锁成就。在同一用户锁内调用。
    """
    state = load_state(data_dir, username)
    today = date.today()
    day_key = today.isoformat()

    success_increased = new_success_count > old_success_count
    raw = compute_raw_xp(
        bonus_practice=bonus_practice,
        remedial=remedial,
        success_increased=success_increased,
        mastered_now=mastered_now,
    )

    daily_so_far = int(state["daily_xp"].get(day_key, 0))
    xp_gain = _apply_daily_cap(daily_so_far, raw)
    sbd = state.setdefault("streak_correct_by_day", {})
    sbd[day_key] = int(sbd.get(day_key, 0)) + 1

    state["total_correct"] = int(state.get("total_correct") or 0) + 1
    if xp_gain > 0:
        state["total_xp"] = int(state.get("total_xp") or 0) + xp_gain
        state["daily_xp"][day_key] = daily_so_far + xp_gain
    _update_streak(state, today)

    new_achievements = _unlock_achievements(state, mastered_words=mastered_words)
    save_state(data_dir, username, state)

    lv = level_from_xp(int(state["total_xp"]))
    _, need_next = xp_to_next_level(int(state["total_xp"]))

    return {
        "xp_gained": xp_gain,
        "raw_xp": raw,
        "total_xp": int(state["total_xp"]),
        "level": lv,
        "xp_to_next_level": need_next,
        "streak": int(state.get("streak") or 0),
        "new_achievements": new_achievements,
        "daily_xp_today": int(state["daily_xp"].get(day_key, 0)),
    }


def sync_achievements_only(
    data_dir: Path,
    username: str,
    *,
    mastered_words: int,
) -> List[Dict[str, Any]]:
    """不加分，仅根据已掌握数等补发成就（老用户首次打开）。"""
    state = load_state(data_dir, username)
    before = set(state.get("achievements", {}).keys())
    new = _unlock_achievements(state, mastered_words=mastered_words)
    after = set(state.get("achievements", {}).keys())
    if after != before:
        save_state(data_dir, username, state)
    return new


def public_profile(data_dir: Path, username: str, *, mastered_words: int) -> Dict[str, Any]:
    """GET /api/gamification 用；会补同步成就。"""
    sync_achievements_only(data_dir, username, mastered_words=mastered_words)
    state = load_state(data_dir, username)
    total_xp = int(state.get("total_xp") or 0)
    lv, need = xp_to_next_level(total_xp)
    ach = state.get("achievements") or {}
    unlocked: List[Dict[str, Any]] = []
    for aid, ts in sorted(ach.items(), key=lambda x: x[1]):
        if aid in ACHIEVEMENT_DEFS:
            row = dict(ACHIEVEMENT_DEFS[aid])
            row["id"] = aid
            row["unlocked_at"] = ts
            unlocked.append(row)

    all_defs: List[Dict[str, Any]] = []
    for aid, meta in ACHIEVEMENT_DEFS.items():
        row = dict(meta)
        row["id"] = aid
        row["unlocked"] = aid in ach
        row["unlocked_at"] = ach.get(aid)
        all_defs.append(row)

    return {
        "total_xp": total_xp,
        "level": lv,
        "xp_to_next_level": need,
        "streak": int(state.get("streak") or 0),
        "last_streak_date": state.get("last_streak_date"),
        "total_correct": int(state.get("total_correct") or 0),
        "leaderboard_opt_in": bool(state.get("leaderboard_opt_in", True)),
        "achievements_unlocked": unlocked,
        "achievements_all": all_defs,
        "daily_xp_today": int(state.get("daily_xp", {}).get(date.today().isoformat(), 0)),
    }


def patch_settings(data_dir: Path, username: str, leaderboard_opt_in: Optional[bool]) -> Dict[str, Any]:
    state = load_state(data_dir, username)
    if leaderboard_opt_in is not None:
        state["leaderboard_opt_in"] = bool(leaderboard_opt_in)
    save_state(data_dir, username, state)
    return {"leaderboard_opt_in": state["leaderboard_opt_in"]}


def build_leaderboard(
    data_dir: Path,
    usernames: List[str],
    *,
    viewer: str,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for un in usernames:
        st = load_state(data_dir, un)
        if not st.get("leaderboard_opt_in", True):
            continue
        xp = int(st.get("total_xp") or 0)
        ach_n = len(st.get("achievements") or {})
        rows.append(
            {
                "username": un,
                "total_xp": xp,
                "level": level_from_xp(xp),
                "streak": int(st.get("streak") or 0),
                "achievements_count": ach_n,
                "is_viewer": un == viewer,
            }
        )
    rows.sort(key=lambda r: (-r["total_xp"], r["username"]))
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
    return rows

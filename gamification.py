"""
学习游戏化：XP、连续打卡、成就、排行榜元数据。
数据文件：user_data_simple/<username>/gamification.json
"""

from __future__ import annotations

import json
import math
import calendar
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

# 当日答对次数 ≥ 此值才算「有效打卡」，并参与连续打卡统计
CHECKIN_MIN_CORRECT = 5

# 完成本月打卡目标时的一次性奖励：目标天数 × 此值（XP）
CHECKIN_GOAL_XP_PER_DAY = 30

# 成就 id -> 展示信息
ACHIEVEMENT_DEFS: Dict[str, Dict[str, str]] = {
    "first_step": {"title": "第一步", "desc": "首次答对单词", "icon": "👣"},
    "streak_3": {"title": "三连击", "desc": "连续打卡 3 天", "icon": "🔥"},
    "streak_7": {"title": "一周坚持", "desc": "连续打卡 7 天", "icon": "⭐"},
    "streak_14": {"title": "双周之约", "desc": "连续打卡 14 天", "icon": "🌙"},
    "streak_30": {"title": "月度冠军", "desc": "连续打卡 30 天", "icon": "🏆"},
    "streak_60": {"title": "季度恒心", "desc": "连续打卡 60 天", "icon": "🗓️"},
    "streak_100": {"title": "百日筑基", "desc": "连续打卡 100 天", "icon": "💠"},
    "word_master_1": {"title": "初窥门径", "desc": "累计掌握 1 个单词", "icon": "📗"},
    "word_master_10": {"title": "词汇积累", "desc": "累计掌握 10 个单词", "icon": "📚"},
    "word_master_50": {"title": "单词达人", "desc": "累计掌握 50 个单词", "icon": "🎓"},
    "word_master_100": {"title": "百词在手", "desc": "累计掌握 100 个单词", "icon": "📖"},
    "word_master_300": {"title": "三百成章", "desc": "累计掌握 300 个单词", "icon": "📘"},
    "word_master_600": {"title": "六百精进", "desc": "累计掌握 600 个单词", "icon": "📙"},
    "word_master_1000": {"title": "千词在胸", "desc": "累计掌握 1000 个单词", "icon": "📕"},
    "word_master_2000": {"title": "两千纵横", "desc": "累计掌握 2000 个单词", "icon": "🗂️"},
    "word_master_4000": {"title": "词海纵横", "desc": "累计掌握 4000 个单词", "icon": "🌊"},
    "xp_1k": {"title": "千分学者", "desc": "累计获得 1000 XP", "icon": "💎"},
    "xp_10k": {"title": "万分传奇", "desc": "累计获得 10000 XP", "icon": "🌟"},
    "xp_25k": {"title": "二万五千里", "desc": "累计获得 25000 XP", "icon": "✨"},
    "xp_50k": {"title": "五万星辰", "desc": "累计获得 50000 XP", "icon": "🌌"},
    "xp_100k": {"title": "十万伏特", "desc": "累计获得 100000 XP", "icon": "⚡"},
    "correct_100": {"title": "百答不倦", "desc": "累计答对 100 次", "icon": "✅"},
    "correct_500": {"title": "五百回合", "desc": "累计答对 500 次", "icon": "🎯"},
    "correct_2000": {"title": "两千连击", "desc": "累计答对 2000 次", "icon": "🎪"},
    "correct_10000": {"title": "万次笃行", "desc": "累计答对 10000 次", "icon": "🎖️"},
    "daily_xp_cap": {"title": "满载而归", "desc": "单日获得 XP 达到当日软上限", "icon": "📈"},
    "monthly_goal_met": {"title": "月度守约", "desc": "本月有效打卡天数达到所设目标", "icon": "🤝"},
    "pk_debut": {"title": "擂台首秀", "desc": "参加过 1v1 PK：赢了的别嚣张，输了的……下次记得打卡", "icon": "🥊"},
    "pk_duel_winner": {"title": "这次我赢了", "desc": "PK 赢过至少一次——对面同学，承让承让（下次还约）", "icon": "🦅"},
    "pk_wins_3": {"title": "三连击·心理战", "desc": "累计 3 胜：建议对手把你拉黑前先复盘打卡天数", "icon": "🎪"},
    "pk_wins_10": {"title": "劝分大师", "desc": "累计 10 胜：你不是来背单词的，你是来批发胜利的", "icon": "👑"},
}


def gamification_path(data_dir: Path, username: str) -> Path:
    return data_dir / username / "gamification.json"


def default_state() -> Dict[str, Any]:
    return {
        "total_xp": 0,
        "total_correct": 0,
        "streak": 0,
        "last_streak_date": None,
        "streak_correct_by_day": {},
        "daily_xp": {},
        "achievements": {},
        "leaderboard_opt_in": True,
        # 本月打卡天数目标：与 mcheckin_goal_month 同时有效
        "mcheckin_goal": None,
        "mcheckin_goal_month": None,
        # 已为哪个月份发放过「完成打卡目标」一次性奖励（YYYY-MM）
        "mcheckin_goal_bonus_awarded_month": None,
        # 自然月内是否已修改过打卡目标（YYYY-MM）；与 mcheckin_goal 不同月时视为新月份可改
        "mcheckin_goal_edits_ym": None,
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
    if "streak_correct_by_day" in raw and isinstance(raw["streak_correct_by_day"], dict):
        sbd: Dict[str, int] = {}
        for dk, cnt in raw["streak_correct_by_day"].items():
            try:
                sbd[str(dk)] = int(cnt)
            except (TypeError, ValueError):
                continue
        base["streak_correct_by_day"] = sbd
    if base.get("mcheckin_goal") is not None:
        try:
            base["mcheckin_goal"] = int(base["mcheckin_goal"])
        except (TypeError, ValueError):
            base["mcheckin_goal"] = None
    if base.get("mcheckin_goal_bonus_awarded_month") is not None:
        base["mcheckin_goal_bonus_awarded_month"] = str(base["mcheckin_goal_bonus_awarded_month"])
    if base.get("mcheckin_goal_edits_ym") is not None:
        base["mcheckin_goal_edits_ym"] = str(base["mcheckin_goal_edits_ym"])
    # 旧数据：本月已有目标但未记录「已编辑」时，视为已用掉当月一次修改机会
    _ym = date.today().strftime("%Y-%m")
    _gm = base.get("mcheckin_goal_month")
    if (
        base.get("mcheckin_goal_edits_ym") is None
        and _gm == _ym
        and base.get("mcheckin_goal") is not None
    ):
        base["mcheckin_goal_edits_ym"] = _ym
    return base


def save_state(data_dir: Path, username: str, state: Dict[str, Any]) -> None:
    path = gamification_path(data_dir, username)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def days_inclusive_today_through_month_end(today: date) -> int:
    """从今天到当月末日（含首尾）的天数，用于默认打卡目标建议值。"""
    _, last_d = calendar.monthrange(today.year, today.month)
    last = date(today.year, today.month, last_d)
    return (last - today).days + 1


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

    # 当日累计答对 ≥ CHECKIN_MIN_CORRECT 才算有效打卡日（连续 streak 仅统计有效日）
    by_day = state.get("streak_correct_by_day") or {}
    valid_checkin_today = int(by_day.get(key, 0)) >= CHECKIN_MIN_CORRECT
    if not valid_checkin_today:
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
    pk_wins: int = 0,
    pk_matches: int = 0,
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
    if streak >= 14:
        grant("streak_14")
    if streak >= 30:
        grant("streak_30")
    if streak >= 60:
        grant("streak_60")
    if streak >= 100:
        grant("streak_100")
    if mastered_words >= 1:
        grant("word_master_1")
    if mastered_words >= 10:
        grant("word_master_10")
    if mastered_words >= 50:
        grant("word_master_50")
    if mastered_words >= 100:
        grant("word_master_100")
    if mastered_words >= 300:
        grant("word_master_300")
    if mastered_words >= 600:
        grant("word_master_600")
    if mastered_words >= 1000:
        grant("word_master_1000")
    if mastered_words >= 2000:
        grant("word_master_2000")
    if mastered_words >= 4000:
        grant("word_master_4000")
    if total_xp >= 1000:
        grant("xp_1k")
    if total_xp >= 10000:
        grant("xp_10k")
    if total_xp >= 25000:
        grant("xp_25k")
    if total_xp >= 50000:
        grant("xp_50k")
    if total_xp >= 100000:
        grant("xp_100k")
    if total_correct >= 100:
        grant("correct_100")
    if total_correct >= 500:
        grant("correct_500")
    if total_correct >= 2000:
        grant("correct_2000")
    if total_correct >= 10000:
        grant("correct_10000")

    dx = state.get("daily_xp") or {}
    if isinstance(dx, dict):
        for v in dx.values():
            try:
                if int(v) >= DAILY_XP_SOFT_CAP:
                    grant("daily_xp_cap")
                    break
            except (TypeError, ValueError):
                continue

    today = date.today()
    ym = today.strftime("%Y-%m")
    if state.get("mcheckin_goal_month") == ym and state.get("mcheckin_goal") is not None:
        try:
            goal_n = int(state["mcheckin_goal"])
        except (TypeError, ValueError):
            goal_n = 0
        if goal_n >= 1 and valid_checkin_days_in_month(state, ym) >= goal_n:
            grant("monthly_goal_met")

    try:
        pm = int(pk_matches)
    except (TypeError, ValueError):
        pm = 0
    try:
        pw = int(pk_wins)
    except (TypeError, ValueError):
        pw = 0
    if pm >= 1:
        grant("pk_debut")
    if pw >= 1:
        grant("pk_duel_winner")
    if pw >= 3:
        grant("pk_wins_3")
    if pw >= 10:
        grant("pk_wins_10")

    return new_list


def valid_checkin_days_in_month(state: Dict[str, Any], year_month: str) -> int:
    """自然月内「有效打卡」天数：当日答对次数 ≥ CHECKIN_MIN_CORRECT 的日期数。"""
    sbd = state.get("streak_correct_by_day") or {}
    ym = year_month.strip()
    if len(ym) != 7:
        return 0
    n = 0
    for day_key, cnt in sbd.items():
        if not isinstance(day_key, str) or not day_key.startswith(ym):
            continue
        try:
            date.fromisoformat(day_key[:10])
        except ValueError:
            continue
        if int(cnt or 0) >= CHECKIN_MIN_CORRECT:
            n += 1
    return n


def valid_checkin_days_in_month_from_day(
    state: Dict[str, Any], year_month: str, min_day_of_month: int
) -> int:
    """
    自然月内，仅统计「日号 ≥ min_day_of_month」的有效打卡天数。
    用于月度群体挑战：第 1～5 日为准备期，第 6 日起计入比赛进度与结算。
    """
    sbd = state.get("streak_correct_by_day") or {}
    ym = year_month.strip()
    if len(ym) != 7:
        return 0
    n = 0
    for day_key, cnt in sbd.items():
        if not isinstance(day_key, str) or not day_key.startswith(ym):
            continue
        try:
            d = date.fromisoformat(day_key[:10])
        except ValueError:
            continue
        if d.day < int(min_day_of_month):
            continue
        if int(cnt or 0) >= CHECKIN_MIN_CORRECT:
            n += 1
    return n


def valid_checkin_days_in_range(state: Dict[str, Any], start_date: date, end_date: date) -> int:
    """统计闭区间 [start_date, end_date] 内的有效打卡天数。"""
    if start_date > end_date:
        return 0
    sbd = state.get("streak_correct_by_day") or {}
    n = 0
    for day_key, cnt in sbd.items():
        if not isinstance(day_key, str):
            continue
        try:
            d = date.fromisoformat(day_key[:10])
        except ValueError:
            continue
        if d < start_date or d > end_date:
            continue
        if int(cnt or 0) >= CHECKIN_MIN_CORRECT:
            n += 1
    return n


def try_grant_monthly_checkin_goal_bonus(data_dir: Path, username: str) -> int:
    """
    在已设目标且当月有效打卡天数已达标时，发放一次性「目标天数 × CHECKIN_GOAL_XP_PER_DAY」。
    用于保存目标后立刻达标、或补发。返回本次发放的 XP（0 表示未发放）。
    """
    state = load_state(data_dir, username)
    today = date.today()
    ym = today.strftime("%Y-%m")
    if state.get("mcheckin_goal_month") != ym or state.get("mcheckin_goal") is None:
        return 0
    if state.get("mcheckin_goal_bonus_awarded_month") == ym:
        return 0
    try:
        g = int(state["mcheckin_goal"])
    except (TypeError, ValueError):
        return 0
    if valid_checkin_days_in_month(state, ym) < g:
        return 0
    bonus = g * CHECKIN_GOAL_XP_PER_DAY
    state["mcheckin_goal_bonus_awarded_month"] = ym
    state["total_xp"] = int(state.get("total_xp") or 0) + bonus
    save_state(data_dir, username, state)
    return bonus


def apply_xp_delta(data_dir: Path, username: str, delta: int) -> Tuple[bool, str, int]:
    """
    调整 total_xp（可为负）。成功返回 (True, "", new_total)；失败 (False, 错误信息, 当前 total)。
    """
    state = load_state(data_dir, username)
    cur = int(state.get("total_xp") or 0)
    new_total = cur + int(delta)
    if new_total < 0:
        return False, "积分不足", cur
    state["total_xp"] = new_total
    save_state(data_dir, username, state)
    return True, "", new_total


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
    pk_wins: int = 0,
    pk_matches: int = 0,
) -> Dict[str, Any]:
    """
    答对后加分、更新 streak、解锁成就。在同一用户锁内调用。
    若已设本月打卡目标且当月有效打卡天数已达目标，且尚未发放过，则一次性发放「目标天数 × CHECKIN_GOAL_XP_PER_DAY」额外奖励（不影响日常练习 XP）。
    """
    state = load_state(data_dir, username)
    today = date.today()
    day_key = today.isoformat()
    ym = today.strftime("%Y-%m")

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
    _update_streak(state, today)

    monthly_bonus_xp = 0
    if (
        state.get("mcheckin_goal_month") == ym
        and state.get("mcheckin_goal") is not None
        and state.get("mcheckin_goal_bonus_awarded_month") != ym
    ):
        g = int(state["mcheckin_goal"])
        if valid_checkin_days_in_month(state, ym) >= g:
            monthly_bonus_xp = g * CHECKIN_GOAL_XP_PER_DAY
            state["mcheckin_goal_bonus_awarded_month"] = ym

    if xp_gain > 0:
        state["total_xp"] = int(state.get("total_xp") or 0) + xp_gain
        state["daily_xp"][day_key] = daily_so_far + xp_gain
    if monthly_bonus_xp > 0:
        state["total_xp"] = int(state.get("total_xp") or 0) + monthly_bonus_xp

    new_achievements = _unlock_achievements(
        state,
        mastered_words=mastered_words,
        pk_wins=pk_wins,
        pk_matches=pk_matches,
    )
    save_state(data_dir, username, state)

    lv = level_from_xp(int(state["total_xp"]))
    _, need_next = xp_to_next_level(int(state["total_xp"]))
    today_correct = int(sbd.get(day_key, 0))
    check_in_done = today_correct >= CHECKIN_MIN_CORRECT

    return {
        "xp_gained": xp_gain,
        "raw_xp": raw,
        "monthly_goal_bonus_xp": monthly_bonus_xp,
        "total_xp": int(state["total_xp"]),
        "level": lv,
        "xp_to_next_level": need_next,
        "streak": int(state.get("streak") or 0),
        "new_achievements": new_achievements,
        "daily_xp_today": int(state["daily_xp"].get(day_key, 0)),
        "today_correct_count": today_correct,
        "check_in_done_today": check_in_done,
        "check_in_min_correct": CHECKIN_MIN_CORRECT,
    }


def sync_achievements_only(
    data_dir: Path,
    username: str,
    *,
    mastered_words: int,
    pk_wins: int = 0,
    pk_matches: int = 0,
) -> List[Dict[str, Any]]:
    """不加分，仅根据已掌握数等补发成就（老用户首次打开）。"""
    state = load_state(data_dir, username)
    before = set(state.get("achievements", {}).keys())
    new = _unlock_achievements(
        state,
        mastered_words=mastered_words,
        pk_wins=pk_wins,
        pk_matches=pk_matches,
    )
    after = set(state.get("achievements", {}).keys())
    if after != before:
        save_state(data_dir, username, state)
    return new


def public_profile(
    data_dir: Path,
    username: str,
    *,
    mastered_words: int,
    pk_wins: int = 0,
    pk_matches: int = 0,
) -> Dict[str, Any]:
    """GET /api/gamification 用；会补同步成就。"""
    sync_achievements_only(
        data_dir,
        username,
        mastered_words=mastered_words,
        pk_wins=pk_wins,
        pk_matches=pk_matches,
    )
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

    today = date.today()
    day_key = today.isoformat()
    ym = today.strftime("%Y-%m")
    sbd = state.get("streak_correct_by_day") or {}
    today_correct = int(sbd.get(day_key, 0))
    month_days = valid_checkin_days_in_month(state, ym)
    goal = state.get("mcheckin_goal")
    goal_month = state.get("mcheckin_goal_month")
    if goal_month != ym:
        goal = None

    bonus_total = int(goal) * CHECKIN_GOAL_XP_PER_DAY if goal is not None else None

    dim = calendar.monthrange(today.year, today.month)[1]
    suggested_days = max(1, min(dim, days_inclusive_today_through_month_end(today)))
    can_edit_goal = state.get("mcheckin_goal_edits_ym") != ym

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
        "daily_xp_today": int(state.get("daily_xp", {}).get(day_key, 0)),
        "today_correct_count": today_correct,
        "check_in_done_today": today_correct >= CHECKIN_MIN_CORRECT,
        "check_in_min_correct": CHECKIN_MIN_CORRECT,
        "month_key": ym,
        "month_valid_checkin_days": month_days,
        "month_days_in_month": dim,
        "monthly_checkin_goal": goal,
        "monthly_checkin_goal_month": goal_month,
        "monthly_checkin_goal_suggested_days": suggested_days,
        "monthly_checkin_goal_can_edit": can_edit_goal,
        "monthly_goal_completion_bonus_xp": bonus_total,
        "monthly_goal_bonus_awarded_this_month": state.get("mcheckin_goal_bonus_awarded_month") == ym,
        "checkin_goal_xp_per_day": CHECKIN_GOAL_XP_PER_DAY,
    }


def patch_settings(
    data_dir: Path,
    username: str,
    leaderboard_opt_in: Optional[bool] = None,
    monthly_checkin_goal: Optional[int] = None,
    *,
    clear_monthly_goal: bool = False,
) -> Dict[str, Any]:
    state = load_state(data_dir, username)
    if leaderboard_opt_in is not None:
        state["leaderboard_opt_in"] = bool(leaderboard_opt_in)
    today = date.today()
    ym = today.strftime("%Y-%m")
    dim = calendar.monthrange(today.year, today.month)[1]

    def _effective_goal_for_month() -> Optional[int]:
        g = state.get("mcheckin_goal")
        gm = state.get("mcheckin_goal_month")
        if gm != ym or g is None:
            return None
        try:
            return int(g)
        except (TypeError, ValueError):
            return None

    goal_update = False
    goal_new: Optional[int] = None
    if clear_monthly_goal:
        goal_update = True
        goal_new = None
    elif monthly_checkin_goal is not None:
        g = int(monthly_checkin_goal)
        if g < 1 or g > dim:
            raise ValueError(f"本月目标须在 1～{dim} 之间")
        goal_update = True
        goal_new = g

    if goal_update:
        goal_old = _effective_goal_for_month()
        if goal_old != goal_new:
            if state.get("mcheckin_goal_edits_ym") == ym:
                raise ValueError("本月已修改过打卡目标，下月再试。")
            if goal_new is None:
                state["mcheckin_goal"] = None
                state["mcheckin_goal_month"] = None
            else:
                state["mcheckin_goal"] = goal_new
                state["mcheckin_goal_month"] = ym
            state["mcheckin_goal_edits_ym"] = ym

    save_state(data_dir, username, state)
    bonus_granted = try_grant_monthly_checkin_goal_bonus(data_dir, username)
    state = load_state(data_dir, username)
    return {
        "leaderboard_opt_in": state["leaderboard_opt_in"],
        "monthly_checkin_goal": state.get("mcheckin_goal") if state.get("mcheckin_goal_month") == ym else None,
        "monthly_checkin_goal_month": state.get("mcheckin_goal_month"),
        "monthly_goal_bonus_just_granted_xp": bonus_granted,
    }


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

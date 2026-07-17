"""
学习游戏化：XP、连续打卡、成就、排行榜元数据。
数据文件：user_data_simple/<username>/gamification.json
"""

from __future__ import annotations

import hashlib
import json
import math
import calendar
import os
import sys
import tempfile
import threading
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

from app_time import china_now_iso, china_today

# ---------- 数值与上限 ----------
XP_PLAN_CORRECT = 10
XP_PROGRESS_STEP = 5
XP_MASTERED = 40
XP_REMEDIAL = 4
XP_BONUS_PRACTICE = 3

DAILY_XP_SOFT_CAP = 300
DAILY_XP_HARD_CAP = 500
PRACTICE_EVENT_LIMIT = 100
XP_TRANSACTION_LIMIT = 2000
# Soft-cap 后仍给少量收益，避免高强度学习完全没有反馈。
OVER_CAP_MULTIPLIER = 0.2

MAX_LEVEL = 99

XP_ACCOUNT_VERSION = 2
NON_EARNING_CREDIT_SOURCES = {"duel_refund"}


def _practice_event_scope_key(value: Any) -> str:
    raw = str(value or '').strip().casefold()
    if not raw:
        return ''
    return f"sha256:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def _stored_practice_event_scope_key(value: Any, encoding: Any = '') -> str:
    raw = str(value or '').strip().casefold()
    if not raw:
        return ''
    if str(encoding or '').strip().casefold() == 'sha256-v1':
        digest = raw[7:]
        if (
            len(raw) == 71
            and raw.startswith('sha256:')
            and all(char in '0123456789abcdef' for char in digest)
        ):
            return raw
        return ''
    return _practice_event_scope_key(raw)

# 当日答对次数 ≥ 此值才算「有效打卡」，并参与连续打卡统计
CHECKIN_MIN_CORRECT = 5
# 兼容早期数据：少数日期的答对次数丢失，但 daily_xp 明显很高。仅用于历史有效打卡推断。
LEGACY_CHECKIN_MIN_DAILY_XP = 100

# 今日首次达成有效打卡时发放；连续天数只给小额加成，鼓励不断档但不制造滚雪球。
CHECKIN_COMPLETION_XP = 20
CHECKIN_STREAK_BONUS_XP_PER_DAY = 1
CHECKIN_STREAK_BONUS_CAP_DAYS = 30

# 完成本月打卡目标时的一次性奖励：目标天数 × 此值（XP）
CHECKIN_GOAL_XP_PER_DAY = 30

# 补打卡只用于挽救连续火苗：不发放 XP、不计入月目标/奖池/PK 的真实打卡天数。
MAKEUP_CHECKIN_BASE_XP = 120
MAKEUP_CHECKIN_STREAK_XP_PER_DAY = 10
MAKEUP_CHECKIN_STREAK_COST_CAP_DAYS = 30
MAKEUP_CHECKIN_AGE_XP_PER_DAY = 90
MAKEUP_CHECKIN_MONTH_SURCHARGE_XP = 80
MAKEUP_CHECKIN_MAX_COST_XP = 1200
MAKEUP_CHECKIN_MONTHLY_LIMIT = 3
MAKEUP_CHECKINS_KEY = "makeup_checkins"

XP_HISTORY_DAYS = 62
XP_HISTORY_SOURCE_LABELS: Dict[str, str] = {
    "practice": "练习答题",
    "monthly_goal_bonus": "打卡目标奖励",
    "weekly_reward": "周榜奖励",
    "monthly_reward": "月榜奖励",
    "monthly_pool_reward": "月度奖池奖励",
    "monthly_pool_fee": "加入月度奖池",
    "duel_reward": "PK 奖励",
    "duel_refund": "PK 退还",
    "duel_stake": "PK 押注",
    "makeup_checkin": "补打卡扣除",
    "spend": "XP 消耗",
    "manual": "XP 调整",
    "manual_deduct": "XP 调整扣除",
}

# 连续火苗「当前连续 + 最高连续」规则生效日（含当日）起：对外 streak 为当前连续（断档未再打卡前即显示 0），
# 并持久化 streak_max；此前仍沿用旧逻辑（直接读 gamification.json 中的 streak 字段）。
# 部署时请改为「上线次日」。
STREAK_V2_EFFECTIVE_DATE = date(2026, 4, 18)

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


_process_locks_guard = threading.Lock()
_process_locks: Dict[str, threading.RLock] = {}


def _state_process_lock(data_dir: Path, username: str) -> threading.RLock:
    key = str((data_dir / username).resolve())
    with _process_locks_guard:
        lock = _process_locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _process_locks[key] = lock
        return lock


@contextmanager
def _locked_user_state(data_dir: Path, username: str) -> Generator[None, None, None]:
    """同一用户 gamification.json 的跨线程/跨进程写锁。"""
    lock = _state_process_lock(data_dir, username)
    with lock:
        lock_path = data_dir / username / ".gamification.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        f = open(lock_path, "a+b", buffering=0)
        try:
            if sys.platform == "win32":
                import msvcrt

                f.seek(0, os.SEEK_END)
                if f.tell() == 0:
                    f.write(b"\0")
                    f.flush()
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            if sys.platform == "win32":
                import msvcrt

                try:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl

                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            try:
                f.close()
            except OSError:
                pass


def default_state() -> Dict[str, Any]:
    return {
        "xp_account_version": XP_ACCOUNT_VERSION,
        "lifetime_xp": 0,
        "xp_balance": 0,
        # Backward-compatible API/storage alias. New code must use lifetime_xp or xp_balance.
        "total_xp": 0,
        "total_correct": 0,
        "streak": 0,
        "last_streak_date": None,
        # 历史最高连续有效打卡天数（仅 STREAK_V2 起维护）；None 表示尚未惰性初始化
        "streak_max": None,
        "streak_correct_by_day": {},
        # 付费补救的火苗日。只参与连续 streak，不参与真实打卡天数、月目标、奖池或 PK。
        MAKEUP_CHECKINS_KEY: {},
        "daily_xp": {},
        "xp_gain_history": {},
        "xp_transactions": {},
        "practice_events": [],
        "achievements": {},
        "leaderboard_opt_in": True,
        # 本月打卡天数目标：与 mcheckin_goal_month 同时有效
        "mcheckin_goal": None,
        "mcheckin_goal_month": None,
        # Number of already-valid days when the current goal was created.
        "mcheckin_goal_baseline_days": None,
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

    # V1 used total_xp as both experience and currency. Preserve the current value in
    # both accounts on first load; future spending only changes xp_balance.
    try:
        account_version = int(raw.get("xp_account_version") or 0)
    except (TypeError, ValueError):
        account_version = 0
    if account_version < XP_ACCOUNT_VERSION:
        try:
            legacy_xp = max(0, int(raw.get("total_xp") or 0))
        except (TypeError, ValueError):
            legacy_xp = 0
        base["lifetime_xp"] = legacy_xp
        base["xp_balance"] = legacy_xp
        base["xp_account_version"] = XP_ACCOUNT_VERSION
    else:
        try:
            base["lifetime_xp"] = max(0, int(raw.get("lifetime_xp") or 0))
        except (TypeError, ValueError):
            base["lifetime_xp"] = 0
        try:
            base["xp_balance"] = max(0, int(raw.get("xp_balance") or 0))
        except (TypeError, ValueError):
            base["xp_balance"] = 0
    base["total_xp"] = int(base["lifetime_xp"])
    if "practice_events" in raw and isinstance(raw["practice_events"], list):
        events_reversed: List[Dict[str, Any]] = []
        scope_counts: Dict[str, int] = {}
        for event in reversed(raw["practice_events"]):
            if not isinstance(event, dict) or not isinstance(event.get("payload"), dict):
                continue
            event_id = str(event.get("event_id") or "").strip()[:96]
            if not event_id:
                continue
            event_scope = _stored_practice_event_scope_key(
                event.get("scope"),
                event.get("scope_encoding"),
            )
            if scope_counts.get(event_scope, 0) >= PRACTICE_EVENT_LIMIT:
                continue
            normalized_event = {"event_id": event_id, "payload": dict(event["payload"])}
            if event_scope:
                normalized_event["scope"] = event_scope
                normalized_event["scope_encoding"] = "sha256-v1"
            events_reversed.append(normalized_event)
            scope_counts[event_scope] = scope_counts.get(event_scope, 0) + 1
        base["practice_events"] = list(reversed(events_reversed))
    if "daily_xp" in raw and isinstance(raw["daily_xp"], dict):
        base["daily_xp"] = {str(k): int(v) for k, v in raw["daily_xp"].items()}
    if "xp_gain_history" in raw and isinstance(raw["xp_gain_history"], dict):
        hist: Dict[str, Dict[str, int]] = {}
        for dk, sources in raw["xp_gain_history"].items():
            if not isinstance(sources, dict):
                continue
            clean_sources: Dict[str, int] = {}
            for source, amount in sources.items():
                try:
                    n = int(amount)
                except (TypeError, ValueError):
                    continue
                if n != 0:
                    clean_sources[str(source)] = n
            if clean_sources:
                hist[str(dk)] = clean_sources
        base["xp_gain_history"] = hist
    if "xp_transactions" in raw and isinstance(raw["xp_transactions"], dict):
        clean_transactions: Dict[str, Dict[str, Any]] = {}
        for tx_id, tx in list(raw["xp_transactions"].items())[-XP_TRANSACTION_LIMIT:]:
            key = str(tx_id or "").strip()[:160]
            if not key or not isinstance(tx, dict):
                continue
            try:
                balance_delta = int(tx.get("balance_delta") or 0)
                lifetime_delta = int(tx.get("lifetime_delta") or 0)
            except (TypeError, ValueError):
                continue
            clean_transactions[key] = {
                "balance_delta": balance_delta,
                "lifetime_delta": lifetime_delta,
                "source": str(tx.get("source") or "manual")[:64],
                "created_at": str(tx.get("created_at") or "")[:32],
            }
        base["xp_transactions"] = clean_transactions
    if "streak_correct_by_day" in raw and isinstance(raw["streak_correct_by_day"], dict):
        sbd: Dict[str, int] = {}
        for dk, cnt in raw["streak_correct_by_day"].items():
            try:
                sbd[str(dk)] = int(cnt)
            except (TypeError, ValueError):
                continue
        base["streak_correct_by_day"] = sbd
    if MAKEUP_CHECKINS_KEY in raw and isinstance(raw[MAKEUP_CHECKINS_KEY], dict):
        makeups: Dict[str, Dict[str, Any]] = {}
        for dk, meta in raw[MAKEUP_CHECKINS_KEY].items():
            try:
                day_key = date.fromisoformat(str(dk)[:10]).isoformat()
            except ValueError:
                continue
            row = dict(meta) if isinstance(meta, dict) else {}
            try:
                row["cost_xp"] = max(0, int(row.get("cost_xp") or 0))
            except (TypeError, ValueError):
                row["cost_xp"] = 0
            try:
                row["streak_before"] = max(0, int(row.get("streak_before") or 0))
            except (TypeError, ValueError):
                row["streak_before"] = 0
            if row.get("created_at") is not None:
                row["created_at"] = str(row["created_at"])
            makeups[day_key] = row
        base[MAKEUP_CHECKINS_KEY] = makeups
    if base.get("mcheckin_goal") is not None:
        try:
            base["mcheckin_goal"] = int(base["mcheckin_goal"])
        except (TypeError, ValueError):
            base["mcheckin_goal"] = None
    if base.get("mcheckin_goal_baseline_days") is not None:
        try:
            base["mcheckin_goal_baseline_days"] = max(
                0, int(base["mcheckin_goal_baseline_days"])
            )
        except (TypeError, ValueError):
            base["mcheckin_goal_baseline_days"] = None
    if base.get("mcheckin_goal_bonus_awarded_month") is not None:
        base["mcheckin_goal_bonus_awarded_month"] = str(base["mcheckin_goal_bonus_awarded_month"])
    if base.get("mcheckin_goal_edits_ym") is not None:
        base["mcheckin_goal_edits_ym"] = str(base["mcheckin_goal_edits_ym"])
    if base.get("streak_max") is not None:
        try:
            base["streak_max"] = int(base["streak_max"])
        except (TypeError, ValueError):
            base["streak_max"] = None
    # 旧数据：本月已有目标但未记录「已编辑」时，视为已用掉当月一次修改机会
    _ym = china_today().strftime("%Y-%m")
    _gm = base.get("mcheckin_goal_month")
    if (
        base.get("mcheckin_goal_edits_ym") is None
        and _gm == _ym
        and base.get("mcheckin_goal") is not None
    ):
        base["mcheckin_goal_edits_ym"] = _ym
    return base


def _save_state_unlocked(data_dir: Path, username: str, state: Dict[str, Any]) -> None:
    state["xp_account_version"] = XP_ACCOUNT_VERSION
    state["lifetime_xp"] = max(0, int(state.get("lifetime_xp") or 0))
    state["xp_balance"] = max(0, int(state.get("xp_balance") or 0))
    state["total_xp"] = int(state["lifetime_xp"])
    path = gamification_path(data_dir, username)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".json", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_state(data_dir: Path, username: str, state: Dict[str, Any]) -> None:
    with _locked_user_state(data_dir, username):
        _save_state_unlocked(data_dir, username, state)


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
    if daily_so_far >= DAILY_XP_HARD_CAP:
        return 0
    if daily_so_far >= DAILY_XP_SOFT_CAP:
        gain = max(1, int(raw_xp * OVER_CAP_MULTIPLIER))
        return min(gain, DAILY_XP_HARD_CAP - daily_so_far)
    if daily_so_far + raw_xp <= DAILY_XP_SOFT_CAP:
        return min(raw_xp, DAILY_XP_HARD_CAP - daily_so_far)
    room = DAILY_XP_SOFT_CAP - daily_so_far
    over = raw_xp - room
    gain = room + max(1, int(over * OVER_CAP_MULTIPLIER))
    return min(gain, DAILY_XP_HARD_CAP - daily_so_far)


def checkin_completion_bonus_raw(streak_after_checkin: int) -> int:
    """首次完成今日有效打卡时的原始奖励；连续加成封顶，避免长期用户过度滚雪球。"""
    streak_days = min(
        max(0, int(streak_after_checkin)),
        CHECKIN_STREAK_BONUS_CAP_DAYS,
    )
    return CHECKIN_COMPLETION_XP + streak_days * CHECKIN_STREAK_BONUS_XP_PER_DAY


def _record_xp_flow_unlocked(
    state: Dict[str, Any],
    source: str,
    amount: int,
    day_key: Optional[str] = None,
) -> None:
    n = int(amount or 0)
    if n == 0:
        return
    dk = day_key or china_today().isoformat()
    hist = state.setdefault("xp_gain_history", {})
    if not isinstance(hist, dict):
        hist = {}
        state["xp_gain_history"] = hist
    day = hist.setdefault(dk, {})
    if not isinstance(day, dict):
        day = {}
        hist[dk] = day
    if source in XP_HISTORY_SOURCE_LABELS:
        key = source
    else:
        key = "manual_deduct" if n < 0 else "manual"
    if key == "manual" and n < 0:
        key = "manual_deduct"
    day[key] = int(day.get(key, 0) or 0) + n


def _account_values(state: Dict[str, Any]) -> Tuple[int, int]:
    lifetime = max(0, int(state.get("lifetime_xp") or state.get("total_xp") or 0))
    balance = max(0, int(state.get("xp_balance") or 0))
    return lifetime, balance


def _set_account_values(state: Dict[str, Any], lifetime: int, balance: int) -> None:
    state["xp_account_version"] = XP_ACCOUNT_VERSION
    state["lifetime_xp"] = max(0, int(lifetime))
    state["xp_balance"] = max(0, int(balance))
    state["total_xp"] = int(state["lifetime_xp"])


def _transaction_matches(
    state: Dict[str, Any],
    transaction_id: str,
    *,
    balance_delta: int,
    lifetime_delta: int,
    source: str,
) -> Optional[bool]:
    tx_id = str(transaction_id or "").strip()[:160]
    if not tx_id:
        return None
    transactions = state.setdefault("xp_transactions", {})
    if not isinstance(transactions, dict):
        transactions = {}
        state["xp_transactions"] = transactions
    existing = transactions.get(tx_id)
    if existing is None:
        return None
    if not isinstance(existing, dict):
        return False
    return (
        int(existing.get("balance_delta") or 0) == int(balance_delta)
        and int(existing.get("lifetime_delta") or 0) == int(lifetime_delta)
        and str(existing.get("source") or "") == str(source)
    )


def _record_transaction_unlocked(
    state: Dict[str, Any],
    transaction_id: str,
    *,
    balance_delta: int,
    lifetime_delta: int,
    source: str,
) -> None:
    tx_id = str(transaction_id or "").strip()[:160]
    if not tx_id:
        return
    transactions = state.setdefault("xp_transactions", {})
    if not isinstance(transactions, dict):
        transactions = {}
        state["xp_transactions"] = transactions
    transactions[tx_id] = {
        "balance_delta": int(balance_delta),
        "lifetime_delta": int(lifetime_delta),
        "source": str(source)[:64],
        "created_at": china_now_iso(timespec="seconds"),
    }
    while len(transactions) > XP_TRANSACTION_LIMIT:
        transactions.pop(next(iter(transactions)))


def xp_history_from_state(
    state: Dict[str, Any],
    *,
    today: Optional[date] = None,
    days: int = XP_HISTORY_DAYS,
) -> Dict[str, Any]:
    """最近一段时间的 XP 收支历史，按天汇总，供弹窗紧凑展示。"""
    end = today or china_today()
    span = max(1, int(days or XP_HISTORY_DAYS))
    start = end - timedelta(days=span - 1)
    daily_xp = state.get("daily_xp") or {}
    raw_hist = state.get("xp_gain_history") or {}
    makeup_spend_by_day: Dict[str, int] = {}
    raw_makeups = state.get(MAKEUP_CHECKINS_KEY) or {}
    if isinstance(raw_makeups, dict):
        for target_day_key, meta in raw_makeups.items():
            row = meta if isinstance(meta, dict) else {}
            try:
                cost = int(row.get("cost_xp") or 0)
            except (TypeError, ValueError):
                continue
            if cost <= 0:
                continue
            created_key = str(row.get("created_at") or "")[:10]
            if not created_key:
                created_key = str(target_day_key)[:10]
            try:
                date.fromisoformat(created_key)
            except ValueError:
                continue
            makeup_spend_by_day[created_key] = makeup_spend_by_day.get(created_key, 0) + cost
    entries: List[Dict[str, Any]] = []

    for offset in range(span):
        d = end - timedelta(days=offset)
        dk = d.isoformat()
        sources: Dict[str, int] = {}
        source_block = raw_hist.get(dk) if isinstance(raw_hist, dict) else None
        if isinstance(source_block, dict):
            for source, amount in source_block.items():
                try:
                    n = int(amount)
                except (TypeError, ValueError):
                    continue
                if n != 0:
                    key = (
                        str(source)
                        if str(source) in XP_HISTORY_SOURCE_LABELS
                        else ("manual_deduct" if n < 0 else "manual")
                    )
                    if key == "manual" and n < 0:
                        key = "manual_deduct"
                    sources[key] = sources.get(key, 0) + n

        try:
            daily_practice_xp = int(daily_xp.get(dk, 0)) if isinstance(daily_xp, dict) else 0
        except (TypeError, ValueError):
            daily_practice_xp = 0
        if daily_practice_xp > int(sources.get("practice", 0) or 0):
            sources["practice"] = daily_practice_xp
        makeup_cost = int(makeup_spend_by_day.get(dk, 0) or 0)
        if makeup_cost > abs(min(0, int(sources.get("makeup_checkin", 0) or 0))):
            sources["makeup_checkin"] = -makeup_cost

        sources = {k: v for k, v in sources.items() if int(v or 0) != 0}
        if not sources:
            continue
        income = sum(v for v in sources.values() if v > 0)
        expense = -sum(v for v in sources.values() if v < 0)
        net = income - expense
        entries.append(
            {
                "date": dk,
                "xp": net,
                "net_xp": net,
                "income_xp": income,
                "expense_xp": expense,
                "sources": [
                    {
                        "source": source,
                        "label": XP_HISTORY_SOURCE_LABELS.get(source, XP_HISTORY_SOURCE_LABELS["manual"]),
                        "xp": amount,
                    }
                    for source, amount in sorted(sources.items(), key=lambda item: (-abs(item[1]), item[0]))
                ],
            }
        )

    total_income_xp = sum(int(row["income_xp"]) for row in entries)
    total_expense_xp = sum(int(row["expense_xp"]) for row in entries)
    net_xp = total_income_xp - total_expense_xp
    best = (
        max(entries, key=lambda row: int(row["income_xp"]), default=None)
        if total_income_xp > 0
        else None
    )
    largest_expense = (
        max(entries, key=lambda row: int(row["expense_xp"]), default=None)
        if total_expense_xp > 0
        else None
    )
    lifetime_xp, xp_balance = _account_values(state)
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "days": span,
        "total_xp": total_income_xp,
        "total_income_xp": total_income_xp,
        "total_expense_xp": total_expense_xp,
        "net_xp": net_xp,
        "lifetime_xp": lifetime_xp,
        "xp_balance": xp_balance,
        "active_days": len(entries),
        "best_day": best,
        "largest_expense_day": largest_expense,
        "entries": entries,
    }


def xp_history_recent(data_dir: Path, username: str, days: int = XP_HISTORY_DAYS) -> Dict[str, Any]:
    state = load_state(data_dir, username)
    return xp_history_from_state(state, days=days)


def streak_v2_active(today: date) -> bool:
    return today >= STREAK_V2_EFFECTIVE_DATE


def _actual_valid_checkin_day(state: Dict[str, Any], day: date) -> bool:
    key = day.isoformat()
    sbd = state.get("streak_correct_by_day") or {}
    try:
        if int(sbd.get(key, 0) or 0) >= CHECKIN_MIN_CORRECT:
            return True
    except (TypeError, ValueError):
        pass
    dx = state.get("daily_xp") or {}
    try:
        return int(dx.get(key, 0) or 0) >= LEGACY_CHECKIN_MIN_DAILY_XP
    except (TypeError, ValueError):
        return False


def _makeup_checkin_map(state: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    raw = state.get(MAKEUP_CHECKINS_KEY) or {}
    return raw if isinstance(raw, dict) else {}


def _makeup_checkin_days(state: Dict[str, Any]) -> List[date]:
    days: List[date] = []
    for day_key in _makeup_checkin_map(state):
        try:
            days.append(date.fromisoformat(str(day_key)[:10]))
        except ValueError:
            continue
    return days


def _streak_valid_checkin_day(state: Dict[str, Any], day: date) -> bool:
    """连续火苗的有效日：真实打卡或已购买补救的火苗日。"""
    if _actual_valid_checkin_day(state, day):
        return True
    return day.isoformat() in _makeup_checkin_map(state)


def _streak_run_ending_on(state: Dict[str, Any], end_day: date) -> int:
    run = 0
    cur = end_day
    while _streak_valid_checkin_day(state, cur):
        run += 1
        cur -= timedelta(days=1)
    return run


def _latest_streak_valid_day_before(state: Dict[str, Any], before_day: date) -> Optional[date]:
    candidates = set()
    sbd = state.get("streak_correct_by_day") or {}
    dx = state.get("daily_xp") or {}
    for day_key in set(sbd.keys()) | set(dx.keys()):
        try:
            d = date.fromisoformat(str(day_key)[:10])
        except ValueError:
            continue
        if d < before_day and _actual_valid_checkin_day(state, d):
            candidates.add(d)
    for d in _makeup_checkin_days(state):
        if d < before_day:
            candidates.add(d)
    return max(candidates) if candidates else None


def _next_makeup_target_date(state: Dict[str, Any], today: date) -> date:
    """
    从离今天最近的断点开始补；已有今天/昨天连续段时，继续向更早的断点推进。
    """
    if _streak_valid_checkin_day(state, today):
        anchor = today
    elif _streak_valid_checkin_day(state, today - timedelta(days=1)):
        anchor = today - timedelta(days=1)
    else:
        return today - timedelta(days=1)

    start = anchor
    while _streak_valid_checkin_day(state, start - timedelta(days=1)):
        start -= timedelta(days=1)
    return start - timedelta(days=1)


def _recompute_current_streak_from_history(state: Dict[str, Any], today: date) -> None:
    """
    用真实打卡 + 补救火苗日重算当前连续，解决「今天已打卡后再补昨天」的断档修复。
    """
    if not streak_v2_active(today):
        return
    if _streak_valid_checkin_day(state, today):
        anchor = today
    elif _streak_valid_checkin_day(state, today - timedelta(days=1)):
        anchor = today - timedelta(days=1)
    else:
        return
    streak_value = _streak_run_ending_on(state, anchor)
    state["streak"] = streak_value
    state["last_streak_date"] = anchor.isoformat()
    _bump_streak_max(state, streak_value)


def longest_valid_streak_from_history(state: Dict[str, Any]) -> int:
    """根据真实打卡与补救火苗日，计算历史上最长连续天数。"""
    sbd = state.get("streak_correct_by_day") or {}
    dx = state.get("daily_xp") or {}
    days_set = set()
    for dk in set(sbd.keys()) | set(dx.keys()):
        try:
            d = date.fromisoformat(str(dk)[:10])
        except ValueError:
            continue
        if _actual_valid_checkin_day(state, d):
            days_set.add(d)
    for makeup_day in _makeup_checkin_days(state):
        days_set.add(makeup_day)
    days = sorted(days_set)
    if not days:
        return 0
    best = 1
    run = 1
    for i in range(1, len(days)):
        if days[i] == days[i - 1]:
            continue
        if days[i] == days[i - 1] + timedelta(days=1):
            run += 1
            best = max(best, run)
        else:
            run = 1
    return best


def effective_current_streak(state: Dict[str, Any], today: date) -> int:
    """
    当前连续有效打卡天数：从真实打卡/补救火苗历史即时推算，避免旧的 streak 存量值偏小。
    末次有效日在「今天或昨天」才算当前连续；否则视为已断档（0）。
    """
    if _streak_valid_checkin_day(state, today):
        return _streak_run_ending_on(state, today)
    yesterday = today - timedelta(days=1)
    if _streak_valid_checkin_day(state, yesterday):
        return _streak_run_ending_on(state, yesterday)
    last = state.get("last_streak_date")
    streak = int(state.get("streak") or 0)
    if not last:
        return 0
    try:
        last_d = date.fromisoformat(str(last))
    except ValueError:
        return 0
    if last_d == today or last_d == yesterday:
        return streak
    return 0


def display_streak(state: Dict[str, Any], today: date) -> int:
    """对 API / 排行榜展示的连续火苗数字。"""
    if not streak_v2_active(today):
        return int(state.get("streak") or 0)
    return effective_current_streak(state, today)


def streak_max_record_display(state: Dict[str, Any], today: date) -> int:
    """历史最高连续（展示用，不在 GET 时写盘）。streak_max 未写入前按历史推算。"""
    if not streak_v2_active(today):
        return 0
    try:
        raw = int(state.get("streak_max") or 0)
    except (TypeError, ValueError):
        raw = 0
    return max(raw, longest_valid_streak_from_history(state), effective_current_streak(state, today))


def streak_diagnostics(state: Dict[str, Any], today: date) -> Dict[str, Any]:
    """解释当前连续的日期区间，并指出当前连续之前的第一个断点。"""
    current = display_streak(state, today)
    longest_history = longest_valid_streak_from_history(state) if streak_v2_active(today) else 0
    max_record = streak_max_record_display(state, today)
    try:
        stored_max = int(state.get("streak_max") or 0)
    except (TypeError, ValueError):
        stored_max = 0
    current_end: Optional[date] = None
    history_backed = False
    if _streak_valid_checkin_day(state, today):
        current_end = today
        history_backed = True
    elif _streak_valid_checkin_day(state, today - timedelta(days=1)):
        current_end = today - timedelta(days=1)
        history_backed = True
    else:
        last = state.get("last_streak_date")
        try:
            last_d = date.fromisoformat(str(last)) if last else None
        except ValueError:
            last_d = None
        if last_d in (today, today - timedelta(days=1)):
            current_end = last_d

    current_start: Optional[date] = None
    gap_day: Optional[date] = None
    gap_correct_count: Optional[int] = None
    gap_daily_xp: Optional[int] = None
    gap_has_makeup = False
    if current > 0 and current_end is not None:
        current_start = current_end - timedelta(days=current - 1)
        gap_day = current_start - timedelta(days=1)
        gap_key = gap_day.isoformat()
        sbd = state.get("streak_correct_by_day") or {}
        dx = state.get("daily_xp") or {}
        try:
            gap_correct_count = int(sbd.get(gap_key, 0) or 0)
        except (TypeError, ValueError):
            gap_correct_count = 0
        try:
            gap_daily_xp = int(dx.get(gap_key, 0) or 0)
        except (TypeError, ValueError):
            gap_daily_xp = 0
        gap_has_makeup = gap_key in _makeup_checkin_map(state)

    inferred_from_daily_xp = (
        gap_daily_xp is not None
        and gap_daily_xp >= LEGACY_CHECKIN_MIN_DAILY_XP
        and (gap_correct_count or 0) < CHECKIN_MIN_CORRECT
    )

    return {
        "current_streak": current,
        "current_start_date": current_start.isoformat() if current_start else None,
        "current_end_date": current_end.isoformat() if current_end else None,
        "current_history_backed": history_backed,
        "longest_history_streak": longest_history,
        "stored_streak_max": stored_max,
        "max_record": max_record,
        "gap_before_current_date": gap_day.isoformat() if gap_day else None,
        "gap_before_current_correct_count": gap_correct_count,
        "gap_before_current_daily_xp": gap_daily_xp,
        "gap_before_current_has_makeup": gap_has_makeup,
        "gap_before_current_inferred_from_daily_xp": inferred_from_daily_xp,
        "check_in_min_correct": CHECKIN_MIN_CORRECT,
        "legacy_checkin_min_daily_xp": LEGACY_CHECKIN_MIN_DAILY_XP,
    }


def _ensure_streak_max_initialized(state: Dict[str, Any], today: date) -> None:
    if not streak_v2_active(today):
        return
    if state.get("streak_max") is not None:
        return
    hist = longest_valid_streak_from_history(state)
    cur = int(state.get("streak") or 0)
    state["streak_max"] = max(hist, cur)


def _bump_streak_max(state: Dict[str, Any], streak_value: int) -> None:
    prev = int(state.get("streak_max") or 0)
    if streak_value > prev:
        state["streak_max"] = streak_value


def _update_streak(state: Dict[str, Any], today: date) -> None:
    key = today.isoformat()
    last = state.get("last_streak_date")
    streak = int(state.get("streak") or 0)

    # 当日累计答对 ≥ CHECKIN_MIN_CORRECT 才算有效打卡日（连续 streak 仅统计有效日）
    by_day = state.get("streak_correct_by_day") or {}
    valid_checkin_today = int(by_day.get(key, 0)) >= CHECKIN_MIN_CORRECT
    if not valid_checkin_today:
        return

    v2 = streak_v2_active(today)
    if v2:
        _ensure_streak_max_initialized(state, today)
        _recompute_current_streak_from_history(state, today)
        return

    if not last:
        state["streak"] = 1
        state["last_streak_date"] = key
        if v2:
            _bump_streak_max(state, 1)
        return

    try:
        last_d = date.fromisoformat(str(last))
    except ValueError:
        state["streak"] = 1
        state["last_streak_date"] = key
        if v2:
            _bump_streak_max(state, 1)
        return

    if last_d == today:
        return
    if last_d == today - timedelta(days=1):
        streak += 1
        state["streak"] = streak
        state["last_streak_date"] = key
        if v2:
            _bump_streak_max(state, streak)
    else:
        if v2:
            _bump_streak_max(state, streak)
        state["streak"] = 1
        state["last_streak_date"] = key
        if v2:
            _bump_streak_max(state, 1)


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
    total_xp = int(state.get("lifetime_xp") or state.get("total_xp") or 0)
    total_correct = int(state.get("total_correct") or 0)
    streak = display_streak(state, china_today())
    ach = state.setdefault("achievements", {})
    assert isinstance(ach, dict)

    def grant(aid: str) -> None:
        if aid in ach:
            return
        if aid not in ACHIEVEMENT_DEFS:
            return
        now = china_now_iso(timespec="seconds")
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

    today = china_today()
    ym = today.strftime("%Y-%m")
    if state.get("mcheckin_goal_month") == ym and state.get("mcheckin_goal") is not None:
        try:
            goal_n = int(state["mcheckin_goal"])
        except (TypeError, ValueError):
            goal_n = 0
        if goal_n >= 1 and monthly_goal_progress_days(state, ym) >= goal_n:
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


def monthly_goal_progress_days(state: Dict[str, Any], year_month: str) -> int:
    """Valid check-in days earned after the current monthly goal was created."""
    total = valid_checkin_days_in_month(state, year_month)
    try:
        baseline = max(0, int(state.get("mcheckin_goal_baseline_days") or 0))
    except (TypeError, ValueError):
        baseline = 0
    return max(0, total - baseline)


def monthly_goal_max_new_days(state: Dict[str, Any], today: date) -> int:
    remaining = days_inclusive_today_through_month_end(today)
    if _actual_valid_checkin_day(state, today):
        remaining -= 1
    return max(0, remaining)


def try_grant_monthly_checkin_goal_bonus(data_dir: Path, username: str) -> int:
    """
    在已设目标且当月有效打卡天数已达标时，发放一次性「目标天数 × CHECKIN_GOAL_XP_PER_DAY」。
    用于保存目标后立刻达标、或补发。返回本次发放的 XP（0 表示未发放）。
    """
    with _locked_user_state(data_dir, username):
        state = load_state(data_dir, username)
        today = china_today()
        ym = today.strftime("%Y-%m")
        if state.get("mcheckin_goal_month") != ym or state.get("mcheckin_goal") is None:
            return 0
        if state.get("mcheckin_goal_bonus_awarded_month") == ym:
            return 0
        try:
            g = int(state["mcheckin_goal"])
        except (TypeError, ValueError):
            return 0
        if monthly_goal_progress_days(state, ym) < g:
            return 0
        bonus = g * CHECKIN_GOAL_XP_PER_DAY
        state["mcheckin_goal_bonus_awarded_month"] = ym
        lifetime, balance = _account_values(state)
        _set_account_values(state, lifetime + bonus, balance + bonus)
        _record_xp_flow_unlocked(state, "monthly_goal_bonus", bonus, today.isoformat())
        _save_state_unlocked(data_dir, username, state)
        return bonus


def apply_xp_delta(
    data_dir: Path,
    username: str,
    delta: int,
    *,
    source: str = "manual",
    lifetime_delta: Optional[int] = None,
    transaction_id: str = "",
) -> Tuple[bool, str, int]:
    """
    Adjust spendable XP. Positive earned credits may also increase lifetime XP.
    A transaction_id makes retries idempotent.
    """
    with _locked_user_state(data_dir, username):
        state = load_state(data_dir, username)
        lifetime, balance = _account_values(state)
        balance_delta = int(delta)
        if lifetime_delta is None:
            earned_delta = (
                max(0, balance_delta)
                if source not in NON_EARNING_CREDIT_SOURCES
                else 0
            )
        else:
            earned_delta = max(0, int(lifetime_delta))
        duplicate = _transaction_matches(
            state,
            transaction_id,
            balance_delta=balance_delta,
            lifetime_delta=earned_delta,
            source=source,
        )
        if duplicate is True:
            return True, "", balance
        if duplicate is False:
            return False, "交易标识与既有 XP 流水冲突", balance
        new_balance = balance + balance_delta
        if new_balance < 0:
            return False, "积分不足", balance
        _set_account_values(state, lifetime + earned_delta, new_balance)
        _record_xp_flow_unlocked(state, source, balance_delta)
        _record_transaction_unlocked(
            state,
            transaction_id,
            balance_delta=balance_delta,
            lifetime_delta=earned_delta,
            source=source,
        )
        _save_state_unlocked(data_dir, username, state)
        return True, "", new_balance


def spend_xp_with_reserve(
    data_dir: Path,
    username: str,
    cost_xp: int,
    reserve_xp: int = 0,
    *,
    source: str = "spend",
    transaction_id: str = "",
) -> Tuple[bool, str, int]:
    """
    原子扣除 XP，并要求扣除后仍保留 reserve_xp。
    用于带风险玩法，避免用户被一次性扣到接近 0。
    """
    cost = max(0, int(cost_xp))
    reserve = max(0, int(reserve_xp))
    with _locked_user_state(data_dir, username):
        state = load_state(data_dir, username)
        lifetime, balance = _account_values(state)
        duplicate = _transaction_matches(
            state,
            transaction_id,
            balance_delta=-cost,
            lifetime_delta=0,
            source=source,
        )
        if duplicate is True:
            return True, "", balance
        if duplicate is False:
            return False, "交易标识与既有 XP 流水冲突", balance
        if balance < cost + reserve:
            return (
                False,
                f"积分不足：需要 {cost} XP，且至少保留 {reserve} XP 安全余量",
                balance,
            )
        new_total = balance - cost
        _set_account_values(state, lifetime, new_total)
        _record_xp_flow_unlocked(state, source, -cost)
        _record_transaction_unlocked(
            state,
            transaction_id,
            balance_delta=-cost,
            lifetime_delta=0,
            source=source,
        )
        _save_state_unlocked(data_dir, username, state)
        return True, "", new_total


def rollback_spend_transaction(
    data_dir: Path,
    username: str,
    transaction_id: str,
    *,
    refund_source: str,
) -> Tuple[bool, str, int]:
    """Undo an idempotent debit so the same transaction ID can be attempted again."""
    tx_id = str(transaction_id or "").strip()[:160]
    if not tx_id:
        return False, "缺少交易标识", 0
    with _locked_user_state(data_dir, username):
        state = load_state(data_dir, username)
        lifetime, balance = _account_values(state)
        transactions = state.get("xp_transactions") or {}
        tx = transactions.get(tx_id) if isinstance(transactions, dict) else None
        if tx is None:
            return True, "", balance
        if not isinstance(tx, dict):
            return False, "XP 流水损坏", balance
        debit = int(tx.get("balance_delta") or 0)
        if debit >= 0 or int(tx.get("lifetime_delta") or 0) != 0:
            return False, "只能回退余额扣款流水", balance
        refund = -debit
        _set_account_values(state, lifetime, balance + refund)
        _record_xp_flow_unlocked(state, refund_source, refund)
        del transactions[tx_id]
        _save_state_unlocked(data_dir, username, state)
        return True, "", balance + refund


def _makeup_checkin_month_count(state: Dict[str, Any], year_month: str) -> int:
    ym = year_month.strip()
    if len(ym) != 7:
        return 0
    n = 0
    for day_key in _makeup_checkin_map(state):
        if isinstance(day_key, str) and day_key.startswith(ym):
            n += 1
    return n


def _makeup_checkin_cost(streak_before: int, month_makeups: int, days_ago: int) -> int:
    streak_part = min(
        max(0, int(streak_before)),
        MAKEUP_CHECKIN_STREAK_COST_CAP_DAYS,
    ) * MAKEUP_CHECKIN_STREAK_XP_PER_DAY
    age_part = max(0, int(days_ago) - 1) * MAKEUP_CHECKIN_AGE_XP_PER_DAY
    surcharge = max(0, int(month_makeups)) * MAKEUP_CHECKIN_MONTH_SURCHARGE_XP
    return min(
        MAKEUP_CHECKIN_MAX_COST_XP,
        MAKEUP_CHECKIN_BASE_XP + streak_part + age_part + surcharge,
    )


def makeup_checkin_offer(state: Dict[str, Any], today: Optional[date] = None) -> Dict[str, Any]:
    """
    返回「按顺序向过去补打卡」的资格与价格。
    补打卡仅挽救连续 streak，不算真实有效打卡，避免影响月目标、奖池与 PK 公平性。
    """
    cur_today = today or china_today()
    target = _next_makeup_target_date(state, cur_today)
    target_key = target.isoformat()
    days_ago = (cur_today - target).days
    total_xp = int(state.get("xp_balance") or 0)
    month_key = target.strftime("%Y-%m")
    month_makeups = _makeup_checkin_month_count(state, month_key)
    prior_anchor = _latest_streak_valid_day_before(state, target)
    streak_before = _streak_run_ending_on(state, prior_anchor) if prior_anchor is not None else 0
    cost = (
        _makeup_checkin_cost(streak_before, month_makeups, days_ago)
        if days_ago >= 1 and prior_anchor is not None and streak_before > 0
        else None
    )

    def out(
        *,
        available: bool,
        eligible: bool,
        reason: str,
        reason_code: str,
        can_afford: bool = False,
    ) -> Dict[str, Any]:
        return {
            "available": available,
            "eligible": eligible,
            "can_afford": can_afford,
            "reason": reason,
            "reason_code": reason_code,
            "target_date": target_key,
            "days_ago": days_ago,
            "cost_xp": cost,
            "streak_before": streak_before,
            "prior_streak_date": prior_anchor.isoformat() if prior_anchor is not None else None,
            "current_xp": total_xp,
            "xp_after_purchase": total_xp - int(cost or 0) if cost is not None else None,
            "month_makeups_used": month_makeups,
            "monthly_limit": MAKEUP_CHECKIN_MONTHLY_LIMIT,
            "rules": {
                "target": "sequential_backward_from_yesterday",
                "counts_for_streak": True,
                "counts_for_monthly_goal": False,
                "counts_for_pool_or_pk": False,
            },
        }

    if not streak_v2_active(cur_today):
        return out(
            available=False,
            eligible=False,
            reason="当前连续火苗规则尚未启用，暂不能补打卡。",
            reason_code="streak_v2_inactive",
        )
    if days_ago < 1:
        return out(
            available=False,
            eligible=False,
            reason="只能补今天以前的断点。",
            reason_code="invalid_target",
        )
    if _actual_valid_checkin_day(state, target):
        return out(
            available=False,
            eligible=False,
            reason="当前顺序目标已经完成真实有效打卡，无需补打卡。",
            reason_code="already_checked",
        )
    if target_key in _makeup_checkin_map(state):
        return out(
            available=False,
            eligible=False,
            reason="当前顺序目标已经补救过，不能重复补打卡。",
            reason_code="already_rescued",
        )
    if prior_anchor is None or streak_before <= 0:
        return out(
            available=False,
            eligible=False,
            reason="找不到可连接的更早有效打卡，不能凭空买连续。",
            reason_code="no_prior_streak",
        )
    if month_makeups >= MAKEUP_CHECKIN_MONTHLY_LIMIT:
        return out(
            available=False,
            eligible=True,
            reason=f"本月补打卡已达 {MAKEUP_CHECKIN_MONTHLY_LIMIT} 次上限。",
            reason_code="monthly_limit",
        )
    assert cost is not None
    if total_xp < cost:
        return out(
            available=False,
            eligible=True,
            reason=f"XP 不足：补打卡需要 {cost} XP，当前只有 {total_xp} XP。",
            reason_code="insufficient_xp",
            can_afford=False,
        )
    return out(
        available=True,
        eligible=True,
        reason="可以消耗 XP 按顺序补救连续火苗。",
        reason_code="available",
        can_afford=True,
    )


def purchase_makeup_checkin(
    data_dir: Path,
    username: str,
    *,
    mastered_words: int,
    pk_wins: int = 0,
    pk_matches: int = 0,
) -> Dict[str, Any]:
    """购买一次补打卡。仅补 streak，且不修改 streak_correct_by_day。"""
    with _locked_user_state(data_dir, username):
        state = load_state(data_dir, username)
        today = china_today()
        offer = makeup_checkin_offer(state, today)
        if not offer.get("available"):
            raise ValueError(str(offer.get("reason") or "当前不可补打卡"))

        target_key = str(offer["target_date"])
        cost = int(offer["cost_xp"])
        lifetime_xp, before_xp = _account_values(state)
        after_xp = before_xp - cost
        if after_xp < 0:
            raise ValueError(f"XP 不足：补打卡需要 {cost} XP，当前只有 {before_xp} XP。")

        makeups = state.setdefault(MAKEUP_CHECKINS_KEY, {})
        if not isinstance(makeups, dict):
            makeups = {}
            state[MAKEUP_CHECKINS_KEY] = makeups
        makeups[target_key] = {
            "created_at": china_now_iso(timespec="seconds"),
            "cost_xp": cost,
            "streak_before": int(offer.get("streak_before") or 0),
            "policy": "makeup_checkin_v1",
        }
        _set_account_values(state, lifetime_xp, after_xp)
        _record_xp_flow_unlocked(state, "makeup_checkin", -cost, today.isoformat())
        _ensure_streak_max_initialized(state, today)
        _recompute_current_streak_from_history(state, today)
        new_achievements = _unlock_achievements(
            state,
            mastered_words=mastered_words,
            pk_wins=pk_wins,
            pk_matches=pk_matches,
        )
        _save_state_unlocked(data_dir, username, state)

        lv = level_from_xp(lifetime_xp)
        _, need_next = xp_to_next_level(lifetime_xp)
        return {
            "target_date": target_key,
            "cost_xp": cost,
            "total_xp": lifetime_xp,
            "lifetime_xp": lifetime_xp,
            "xp_balance": after_xp,
            "level": lv,
            "xp_to_next_level": need_next,
            "streak": display_streak(state, today),
            "streak_max_record": streak_max_record_display(state, today),
            "new_achievements": new_achievements,
            "makeup_checkin": makeup_checkin_offer(state, today),
        }


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
    event_id: str = '',
    event_scope: str = '',
) -> Dict[str, Any]:
    """
    答对后加分、更新 streak、解锁成就。在同一用户锁内调用。
    若已设本月打卡目标且当月有效打卡天数已达目标，且尚未发放过，则一次性发放「目标天数 × CHECKIN_GOAL_XP_PER_DAY」额外奖励（不影响日常练习 XP）。
    """
    with _locked_user_state(data_dir, username):
        state = load_state(data_dir, username)
        event_key = str(event_id or '').strip()[:96]
        event_scope_key = _practice_event_scope_key(event_scope)
        practice_events = state.setdefault('practice_events', [])
        if not isinstance(practice_events, list):
            practice_events = []
            state['practice_events'] = practice_events
        if event_key:
            for event in practice_events:
                if not isinstance(event, dict) or event.get('event_id') != event_key:
                    continue
                stored_scope = _stored_practice_event_scope_key(
                    event.get('scope'),
                    event.get('scope_encoding'),
                )
                if event_scope_key and stored_scope not in ('', event_scope_key):
                    continue
                if not event_scope_key and stored_scope:
                    continue
                payload = event.get('payload')
                if isinstance(payload, dict):
                    return dict(payload)
        today = china_today()
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
        answer_xp_gain = _apply_daily_cap(daily_so_far, raw)
        sbd = state.setdefault("streak_correct_by_day", {})
        today_correct_before = int(sbd.get(day_key, 0))
        sbd[day_key] = today_correct_before + 1

        state["total_correct"] = int(state.get("total_correct") or 0) + 1
        _update_streak(state, today)

        checkin_bonus_raw = 0
        checkin_bonus_xp = 0
        crossed_checkin_threshold = (
            today_correct_before < CHECKIN_MIN_CORRECT
            and int(sbd.get(day_key, 0)) >= CHECKIN_MIN_CORRECT
        )
        if crossed_checkin_threshold:
            checkin_bonus_raw = checkin_completion_bonus_raw(display_streak(state, today))
            checkin_bonus_xp = _apply_daily_cap(
                daily_so_far + answer_xp_gain,
                checkin_bonus_raw,
            )
        xp_gain = answer_xp_gain + checkin_bonus_xp

        monthly_bonus_xp = 0
        if (
            state.get("mcheckin_goal_month") == ym
            and state.get("mcheckin_goal") is not None
            and state.get("mcheckin_goal_bonus_awarded_month") != ym
        ):
            g = int(state["mcheckin_goal"])
            if monthly_goal_progress_days(state, ym) >= g:
                monthly_bonus_xp = g * CHECKIN_GOAL_XP_PER_DAY
                state["mcheckin_goal_bonus_awarded_month"] = ym

        total_credit = xp_gain + monthly_bonus_xp
        if total_credit > 0:
            lifetime_xp, xp_balance = _account_values(state)
            _set_account_values(
                state,
                lifetime_xp + total_credit,
                xp_balance + total_credit,
            )
        if xp_gain > 0:
            state["daily_xp"][day_key] = daily_so_far + xp_gain
        if monthly_bonus_xp > 0:
            _record_xp_flow_unlocked(state, "monthly_goal_bonus", monthly_bonus_xp, day_key)
        if xp_gain > 0:
            _record_xp_flow_unlocked(state, "practice", xp_gain, day_key)

        new_achievements = _unlock_achievements(
            state,
            mastered_words=mastered_words,
            pk_wins=pk_wins,
            pk_matches=pk_matches,
        )
        lifetime_xp, xp_balance = _account_values(state)
        lv = level_from_xp(lifetime_xp)
        _, need_next = xp_to_next_level(lifetime_xp)
        today_correct = int(sbd.get(day_key, 0))
        check_in_done = today_correct >= CHECKIN_MIN_CORRECT

        payload = {
            "xp_gained": xp_gain,
            "answer_xp_gained": answer_xp_gain,
            "checkin_bonus_xp": checkin_bonus_xp,
            "checkin_bonus_raw_xp": checkin_bonus_raw,
            "raw_xp": raw,
            "monthly_goal_bonus_xp": monthly_bonus_xp,
            "total_xp": lifetime_xp,
            "lifetime_xp": lifetime_xp,
            "xp_balance": xp_balance,
            "level": lv,
            "xp_to_next_level": need_next,
            "streak": display_streak(state, today),
            "new_achievements": new_achievements,
            "daily_xp_today": int(state["daily_xp"].get(day_key, 0)),
            "today_correct_count": today_correct,
            "check_in_done_today": check_in_done,
            "check_in_min_correct": CHECKIN_MIN_CORRECT,
            "makeup_checkin": makeup_checkin_offer(state, today),
        }
        if event_key:
            event_record = {'event_id': event_key, 'payload': payload}
            if event_scope_key:
                event_record['scope'] = event_scope_key
                event_record['scope_encoding'] = 'sha256-v1'
            practice_events.append(event_record)
            matching_seen = 0
            retained_reversed = []
            for event in reversed(practice_events):
                stored_scope = (
                    _stored_practice_event_scope_key(
                        event.get('scope'),
                        event.get('scope_encoding'),
                    )
                    if isinstance(event, dict)
                    else ''
                )
                if stored_scope == event_scope_key:
                    matching_seen += 1
                    if matching_seen > PRACTICE_EVENT_LIMIT:
                        continue
                retained_reversed.append(event)
            practice_events[:] = reversed(retained_reversed)
        _save_state_unlocked(data_dir, username, state)
        return payload


def sync_achievements_only(
    data_dir: Path,
    username: str,
    *,
    mastered_words: int,
    pk_wins: int = 0,
    pk_matches: int = 0,
) -> List[Dict[str, Any]]:
    """不加分，仅根据已掌握数等补发成就（老用户首次打开）。"""
    with _locked_user_state(data_dir, username):
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
            _save_state_unlocked(data_dir, username, state)
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
    total_xp, xp_balance = _account_values(state)
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

    today = china_today()
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
    goal_max_days = monthly_goal_max_new_days(state, today)
    suggested_days = max(1, goal_max_days) if goal_max_days > 0 else 0
    can_edit_goal = state.get("mcheckin_goal_edits_ym") != ym
    streak_diag = streak_diagnostics(state, today)
    goal_progress_days = monthly_goal_progress_days(state, ym) if goal is not None else 0

    return {
        "total_xp": total_xp,
        "lifetime_xp": total_xp,
        "xp_balance": xp_balance,
        "level": lv,
        "xp_to_next_level": need,
        "streak": display_streak(state, today),
        "streak_max_record": streak_max_record_display(state, today),
        "streak_diagnostics": streak_diag,
        "last_streak_date": state.get("last_streak_date"),
        "total_correct": int(state.get("total_correct") or 0),
        "leaderboard_opt_in": bool(state.get("leaderboard_opt_in", True)),
        "achievements_unlocked": unlocked,
        "achievements_all": all_defs,
        "daily_xp_today": int(state.get("daily_xp", {}).get(day_key, 0)),
        "daily_xp_soft_cap": DAILY_XP_SOFT_CAP,
        "daily_xp_hard_cap": DAILY_XP_HARD_CAP,
        "today_correct_count": today_correct,
        "check_in_done_today": today_correct >= CHECKIN_MIN_CORRECT,
        "check_in_min_correct": CHECKIN_MIN_CORRECT,
        "checkin_completion_xp": CHECKIN_COMPLETION_XP,
        "checkin_streak_bonus_xp_per_day": CHECKIN_STREAK_BONUS_XP_PER_DAY,
        "checkin_streak_bonus_cap_days": CHECKIN_STREAK_BONUS_CAP_DAYS,
        "month_key": ym,
        "month_valid_checkin_days": month_days,
        "month_days_in_month": dim,
        "monthly_checkin_goal": goal,
        "monthly_checkin_goal_month": goal_month,
        "monthly_checkin_goal_suggested_days": suggested_days,
        "monthly_checkin_goal_max_days": goal_max_days,
        "monthly_checkin_goal_progress_days": goal_progress_days,
        "monthly_checkin_goal_can_edit": can_edit_goal,
        "monthly_goal_completion_bonus_xp": bonus_total,
        "monthly_goal_bonus_awarded_this_month": state.get("mcheckin_goal_bonus_awarded_month") == ym,
        "checkin_goal_xp_per_day": CHECKIN_GOAL_XP_PER_DAY,
        "makeup_checkin": makeup_checkin_offer(state, today),
        "makeup_checkin_days_this_month": _makeup_checkin_month_count(state, ym),
    }


def patch_settings(
    data_dir: Path,
    username: str,
    leaderboard_opt_in: Optional[bool] = None,
    monthly_checkin_goal: Optional[int] = None,
    *,
    clear_monthly_goal: bool = False,
) -> Dict[str, Any]:
    today = china_today()
    ym = today.strftime("%Y-%m")
    goal_update = False
    goal_new: Optional[int] = None
    if clear_monthly_goal:
        goal_update = True
        goal_new = None
    elif monthly_checkin_goal is not None:
        g = int(monthly_checkin_goal)
        if g < 1:
            raise ValueError("本月目标须至少为 1 天")
        goal_update = True
        goal_new = g

    with _locked_user_state(data_dir, username):
        state = load_state(data_dir, username)
        if leaderboard_opt_in is not None:
            state["leaderboard_opt_in"] = bool(leaderboard_opt_in)

        def _effective_goal_for_month() -> Optional[int]:
            g = state.get("mcheckin_goal")
            gm = state.get("mcheckin_goal_month")
            if gm != ym or g is None:
                return None
            try:
                return int(g)
            except (TypeError, ValueError):
                return None

        if goal_update:
            goal_old = _effective_goal_for_month()
            if goal_old != goal_new:
                if state.get("mcheckin_goal_edits_ym") == ym:
                    raise ValueError("本月已修改过打卡目标，下月再试。")
                if goal_new is None:
                    state["mcheckin_goal"] = None
                    state["mcheckin_goal_month"] = None
                    state["mcheckin_goal_baseline_days"] = None
                else:
                    max_days = monthly_goal_max_new_days(state, today)
                    if goal_new > max_days:
                        raise ValueError(f"从今天起本月最多还能完成 {max_days} 个有效打卡日")
                    state["mcheckin_goal"] = goal_new
                    state["mcheckin_goal_month"] = ym
                    state["mcheckin_goal_baseline_days"] = valid_checkin_days_in_month(
                        state, ym
                    )
                state["mcheckin_goal_edits_ym"] = ym

        _save_state_unlocked(data_dir, username, state)
    bonus_granted = try_grant_monthly_checkin_goal_bonus(data_dir, username)
    state = load_state(data_dir, username)
    return {
        "leaderboard_opt_in": state["leaderboard_opt_in"],
        "monthly_checkin_goal": state.get("mcheckin_goal") if state.get("mcheckin_goal_month") == ym else None,
        "monthly_checkin_goal_month": state.get("mcheckin_goal_month"),
        "monthly_goal_bonus_just_granted_xp": bonus_granted,
    }


def load_states_batch(
    data_dir: Path, usernames: List[str]
) -> Dict[str, Dict[str, Any]]:
    """一次请求内对多个用户各读一次 gamification.json，供排行榜等复用。"""
    return {un: load_state(data_dir, un) for un in usernames}


def build_leaderboard_from_states(
    states: Dict[str, Dict[str, Any]],
    usernames: List[str],
    *,
    viewer: str,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    today = china_today()
    for un in usernames:
        st = states.get(un)
        if st is None:
            continue
        if not st.get("leaderboard_opt_in", True):
            continue
        xp = int(st.get("lifetime_xp") or st.get("total_xp") or 0)
        ach_n = len(st.get("achievements") or {})
        streak = display_streak(st, today)
        streak_max = streak_max_record_display(st, today)
        streak_diag = streak_diagnostics(st, today)
        if streak_v2_active(today):
            streak_max = max(streak, streak_max)
        rows.append(
            {
                "username": un,
                "total_xp": xp,
                "level": level_from_xp(xp),
                "streak": streak,
                "streak_max_record": streak_max,
                "streak_current_start_date": streak_diag.get("current_start_date"),
                "streak_current_end_date": streak_diag.get("current_end_date"),
                "streak_gap_before_current_date": streak_diag.get("gap_before_current_date"),
                "streak_gap_before_current_correct_count": streak_diag.get("gap_before_current_correct_count"),
                "streak_gap_before_current_daily_xp": streak_diag.get("gap_before_current_daily_xp"),
                "check_in_min_correct": CHECKIN_MIN_CORRECT,
                "achievements_count": ach_n,
                "is_viewer": un == viewer,
            }
        )
    rows.sort(key=lambda r: (-r["total_xp"], r["username"]))
    previous_xp: Optional[int] = None
    rank = 0
    for i, r in enumerate(rows, start=1):
        if previous_xp is None or int(r["total_xp"]) != previous_xp:
            rank = i
            previous_xp = int(r["total_xp"])
        r["rank"] = rank
    return rows


def build_leaderboard(
    data_dir: Path,
    usernames: List[str],
    *,
    viewer: str,
) -> List[Dict[str, Any]]:
    return build_leaderboard_from_states(
        load_states_batch(data_dir, usernames),
        usernames,
        viewer=viewer,
    )

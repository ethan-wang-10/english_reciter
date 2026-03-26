"""
月度群体奖池、1v1 打卡 PK（赌注 XP）。
数据目录：user_data_simple/_challenges/
"""

from __future__ import annotations

import json
import threading
import uuid
from calendar import monthrange
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import gamification as gamification_mod

MONTHLY_POOL_FEE_XP = 150
JOIN_WINDOW_LAST_DAY = 5  # 每月 1～5 日可报名
WAGER_TIERS = (0, 50, 100, 200)

_challenges_lock = threading.Lock()


def challenges_dir(data_dir: Path) -> Path:
    p = data_dir / "_challenges"
    p.mkdir(parents=True, exist_ok=True)
    return p


def monthly_pool_path(data_dir: Path) -> Path:
    return challenges_dir(data_dir) / "monthly_pool.json"


def duels_path(data_dir: Path) -> Path:
    return challenges_dir(data_dir) / "duels.json"


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


def month_key(d: date) -> str:
    return d.strftime("%Y-%m")


def pool_join_window_open(today: Optional[date] = None) -> bool:
    t = today or date.today()
    return 1 <= t.day <= JOIN_WINDOW_LAST_DAY


def valid_checkin_days_for_user(data_dir: Path, username: str, ym: str) -> int:
    st = gamification_mod.load_state(data_dir, username)
    return gamification_mod.valid_checkin_days_in_month(st, ym)


def _settle_monthly_pool_if_needed(data_dir: Path) -> None:
    """若存在未结算的上一自然月奖池，则瓜分。"""
    path = monthly_pool_path(data_dir)
    raw = _load_json(path, {"months": {}})
    months = raw.get("months") or {}
    if not isinstance(months, dict):
        months = {}
    today = date.today()
    cur_ym = month_key(today)
    changed = False

    for ym, block in list(months.items()):
        if not isinstance(block, dict):
            continue
        if ym >= cur_ym:
            continue
        if block.get("settled"):
            continue
        participants = [str(x) for x in (block.get("participants") or []) if x]
        pool = int(block.get("pool_xp") or 0)
        if not participants or pool <= 0:
            block["settled"] = True
            block["settled_at"] = datetime.now().isoformat(timespec="seconds")
            block["settlement_note"] = "无人或空池"
            months[ym] = block
            changed = True
            continue

        best = -1
        counts: Dict[str, int] = {}
        for u in participants:
            c = valid_checkin_days_for_user(data_dir, u, ym)
            counts[u] = c
            if c > best:
                best = c
        winners = [u for u in participants if counts.get(u, 0) == best and best >= 0]
        share = pool // len(winners) if winners else 0
        remainder = pool - share * len(winners)

        for w in winners:
            ok, _, _ = gamification_mod.apply_xp_delta(data_dir, w, share)
            if not ok:
                pass
        block["settled"] = True
        block["settled_at"] = datetime.now().isoformat(timespec="seconds")
        block["winners"] = winners
        block["winner_days"] = best
        block["per_winner_xp"] = share
        block["remainder_xp"] = remainder
        block["counts"] = counts
        months[ym] = block
        changed = True

    if changed:
        raw["months"] = months
        _save_json(path, raw)


def get_monthly_pool_state(data_dir: Path, username: str) -> Dict[str, Any]:
    _settle_monthly_pool_if_needed(data_dir)
    today = date.today()
    ym = month_key(today)
    path = monthly_pool_path(data_dir)
    raw = _load_json(path, {"months": {}})
    months = raw.get("months") or {}
    block = months.get(ym) or {}
    participants = list(block.get("participants") or [])
    pool = int(block.get("pool_xp") or 0)
    joined = username in participants
    my_days = valid_checkin_days_for_user(data_dir, username, ym) if joined else 0
    dim = monthrange(today.year, today.month)[1]

    return {
        "month": ym,
        "pool_xp": pool,
        "participant_count": len(participants),
        "fee_xp": MONTHLY_POOL_FEE_XP,
        "join_window_open": pool_join_window_open(today),
        "join_window_last_day": JOIN_WINDOW_LAST_DAY,
        "joined": joined,
        "my_month_valid_days": my_days,
        "month_days": dim,
    }


def join_monthly_pool(data_dir: Path, username: str) -> Tuple[bool, str, Dict[str, Any]]:
    if not pool_join_window_open():
        return False, f"仅在每月 1～{JOIN_WINDOW_LAST_DAY} 日可加入群体挑战", {}

    _settle_monthly_pool_if_needed(data_dir)
    today = date.today()
    ym = month_key(today)
    path = monthly_pool_path(data_dir)
    with _challenges_lock:
        raw = _load_json(path, {"months": {}})
        months = raw.setdefault("months", {})
        block = months.get(ym) or {"pool_xp": 0, "participants": []}
        participants = list(block.get("participants") or [])
        if username in participants:
            return False, "本月已加入", get_monthly_pool_state(data_dir, username)

        ok, msg, _ = gamification_mod.apply_xp_delta(data_dir, username, -MONTHLY_POOL_FEE_XP)
        if not ok:
            return False, msg or "积分不足", {}

        participants.append(username)
        block["participants"] = participants
        block["pool_xp"] = int(block.get("pool_xp") or 0) + MONTHLY_POOL_FEE_XP
        months[ym] = block
        _save_json(path, raw)

    return True, "", get_monthly_pool_state(data_dir, username)


def _load_duels(data_dir: Path) -> Dict[str, Any]:
    return _load_json(duels_path(data_dir), {"duels": []})


def _save_duels(data_dir: Path, data: Dict[str, Any]) -> None:
    _save_json(duels_path(data_dir), data)


def create_duel(
    data_dir: Path,
    from_user: str,
    target_user: str,
    *,
    wager_xp: int,
    duel_month: Optional[str] = None,
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    if wager_xp not in WAGER_TIERS:
        return False, f"wager_xp 须为 {list(WAGER_TIERS)} 之一", None
    if from_user == target_user:
        return False, "不能挑战自己", None
    today = date.today()
    ym = duel_month or month_key(today)
    duel_id = str(uuid.uuid4())
    row = {
        "id": duel_id,
        "month": ym,
        "from_user": from_user,
        "target_user": target_user,
        "wager_xp": int(wager_xp),
        "status": "pending",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "accepted_at": None,
        "settled": False,
        "winner": None,
    }
    with _challenges_lock:
        data = _load_duels(data_dir)
        duels = data.setdefault("duels", [])
        duels.append(row)
        _save_duels(data_dir, data)
    return True, "", row


def respond_duel(
    data_dir: Path,
    duel_id: str,
    target_user: str,
    accept: bool,
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    with _challenges_lock:
        data = _load_duels(data_dir)
        duels = data.get("duels") or []
        found = None
        for d in duels:
            if d.get("id") == duel_id:
                found = d
                break
        if not found:
            return False, "挑战不存在", None
        if found.get("target_user") != target_user:
            return False, "无权操作", None
        if found.get("status") != "pending":
            return False, "已处理", None
        if not accept:
            found["status"] = "declined"
            _save_duels(data_dir, data)
            return True, "", found

        w = int(found.get("wager_xp") or 0)
        a, b = found.get("from_user"), found.get("target_user")
        if w > 0:
            ok1, msg1, _ = gamification_mod.apply_xp_delta(data_dir, str(a), -w)
            if not ok1:
                return False, f"发起方{msg1}", None
            ok2, msg2, _ = gamification_mod.apply_xp_delta(data_dir, str(b), -w)
            if not ok2:
                gamification_mod.apply_xp_delta(data_dir, str(a), w)
                return False, f"应战方{msg2}", None
        found["status"] = "active"
        found["accepted_at"] = datetime.now().isoformat(timespec="seconds")
        found["escrow_xp"] = w
        _save_duels(data_dir, data)
        return True, "", found


def settle_due_duels(data_dir: Path) -> None:
    """结算上月及更早未处理的 active 挑战。"""
    today = date.today()
    cur_ym = month_key(today)
    with _challenges_lock:
        data = _load_duels(data_dir)
        duels = data.get("duels") or []
        changed = False
        for d in duels:
            if d.get("settled"):
                continue
            if d.get("status") not in ("active",):
                continue
            ym = str(d.get("month") or "")
            if ym >= cur_ym:
                continue
            a = str(d.get("from_user") or "")
            b = str(d.get("target_user") or "")
            w = int(d.get("wager_xp") or 0)
            da = valid_checkin_days_for_user(data_dir, a, ym)
            db = valid_checkin_days_for_user(data_dir, b, ym)
            winner = None
            if da > db:
                winner = a
            elif db > da:
                winner = b
            if winner and w > 0:
                gamification_mod.apply_xp_delta(data_dir, winner, 2 * w)
            elif winner is None and w > 0:
                gamification_mod.apply_xp_delta(data_dir, a, w)
                gamification_mod.apply_xp_delta(data_dir, b, w)
            d["settled"] = True
            d["settled_at"] = datetime.now().isoformat(timespec="seconds")
            d["days_a"] = da
            d["days_b"] = db
            d["winner"] = winner
            d["tie"] = winner is None
            changed = True
        if changed:
            _save_duels(data_dir, data)


def list_duels_for_user(data_dir: Path, username: str) -> List[Dict[str, Any]]:
    settle_due_duels(data_dir)
    data = _load_duels(data_dir)
    duels = data.get("duels") or []
    out = []
    for d in duels:
        if d.get("from_user") == username or d.get("target_user") == username:
            out.append(dict(d))
    out.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return out

"""
月度群体奖池、1v1 打卡 PK（赌注 XP）。
数据目录：user_data_simple/_challenges/
"""

from __future__ import annotations

import json
import threading
import uuid
from calendar import monthrange
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

from app_time import china_now, china_now_iso, china_today
import gamification as gamification_mod
from storage_utils import interprocess_file_lock, write_json_atomic

MONTHLY_POOL_FEE_XP = 150
STAKE_SAFETY_RESERVE_XP = 50
# 每月 1～5 日为准备期（报名、组队）；第 6 日起比赛正式开始，有效打卡仅计 6 日及以后
PREPARATION_LAST_DAY = 5
COMPETITION_START_DAY = 6
JOIN_WINDOW_LAST_DAY = PREPARATION_LAST_DAY  # 仅准备期内可加入奖池
WAGER_TIERS = (0, 50, 100, 200)
# 1v1 邀约：自发起日起第 N 个自然日 23:59:59 前未接受则自动过期
DUEL_INVITE_EXPIRY_DAYS = 5

_challenges_lock = threading.RLock()


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
    write_json_atomic(path, data)


@contextmanager
def _locked_challenges_storage(data_dir: Path) -> Generator[None, None, None]:
    with _challenges_lock:
        with interprocess_file_lock(challenges_dir(data_dir) / ".challenges.lock"):
            yield


def month_key(d: date) -> str:
    return d.strftime("%Y-%m")


def duel_invite_expires_at_from_created(created: datetime) -> datetime:
    """自 created 所在日起第 DUEL_INVITE_EXPIRY_DAYS 个自然日 23:59:59（本地时间）。"""
    d0 = created.date()
    last_day = d0 + timedelta(days=DUEL_INVITE_EXPIRY_DAYS - 1)
    return datetime.combine(last_day, time(23, 59, 59))


def pool_join_window_open(today: Optional[date] = None) -> bool:
    t = today or china_today()
    return 1 <= t.day <= JOIN_WINDOW_LAST_DAY


def pool_preparation_phase(today: Optional[date] = None) -> bool:
    """每月 1～5 日为准备期（尚未开赛）。"""
    t = today or china_today()
    return 1 <= t.day <= PREPARATION_LAST_DAY


def pool_competition_phase(today: Optional[date] = None) -> bool:
    """第 6 日起为比赛期（本月内）。"""
    t = today or china_today()
    return t.day >= COMPETITION_START_DAY


def max_competition_days_in_month(ym: str) -> int:
    """本月可用于比赛的有效打卡天数上限（从 6 日到月末的天数）。"""
    parts = ym.split("-")
    if len(parts) != 2:
        return 0
    y, m = int(parts[0]), int(parts[1])
    dim = monthrange(y, m)[1]
    return max(0, dim - PREPARATION_LAST_DAY)


def competition_checkin_days_for_user(data_dir: Path, username: str, ym: str) -> int:
    """比赛期内的有效打卡天数（仅 6 日及以后）。"""
    st = gamification_mod.load_state(data_dir, username)
    return gamification_mod.valid_checkin_days_in_month_from_day(
        st, ym, COMPETITION_START_DAY
    )


def valid_checkin_days_for_user(data_dir: Path, username: str, ym: str) -> int:
    st = gamification_mod.load_state(data_dir, username)
    return gamification_mod.valid_checkin_days_in_month(st, ym)


def duel_pk_counting_range(duel: Dict[str, Any]) -> Optional[Tuple[date, date]]:
    """
    1v1 PK 计分区间：双方同意（应战接受）的次日起，至决斗所属自然月月末（含端点）。
    若接受日过晚导致次月才起算，则该自然月内无计分日，返回 None。
    """
    ym = str(duel.get("month") or "").strip()
    if len(ym) != 7:
        return None
    accepted = duel.get("accepted_at")
    if not accepted:
        return None
    try:
        ts = str(accepted).replace("Z", "")
        acc_d = date.fromisoformat(ts[:10])
    except ValueError:
        return None
    y, m = map(int, ym.split("-"))
    dim = monthrange(y, m)[1]
    month_first = date(y, m, 1)
    month_last = date(y, m, dim)
    pk_start = acc_d + timedelta(days=1)
    eff_start = max(month_first, pk_start)
    if eff_start > month_last:
        return None
    return eff_start, month_last


def duel_pk_days_for_user(data_dir: Path, username: str, duel: Dict[str, Any]) -> int:
    """1v1 在计分区间内的有效打卡天数。"""
    rng = duel_pk_counting_range(duel)
    if not rng:
        return 0
    s, e = rng
    st = gamification_mod.load_state(data_dir, username)
    return gamification_mod.valid_checkin_days_in_range(st, s, e)


def enrich_duel_for_api(duel: Dict[str, Any]) -> Dict[str, Any]:
    """补充 PK 计分起止日期、邀约过期时间供前端展示。"""
    out = dict(duel)
    rng = duel_pk_counting_range(out)
    if rng:
        s, e = rng
        out["pk_stats_start_date"] = s.isoformat()
        out["pk_stats_end_date"] = e.isoformat()
    else:
        out["pk_stats_start_date"] = None
        out["pk_stats_end_date"] = None
    if out.get("status") == "pending" and not out.get("expires_at") and out.get("created_at"):
        try:
            ca = str(out["created_at"]).replace("Z", "")
            dt = datetime.fromisoformat(ca[:19])
            out["expires_at"] = duel_invite_expires_at_from_created(dt).isoformat(timespec="seconds")
        except (ValueError, TypeError):
            pass
    return out


def _parse_iso_datetime(s: str) -> Optional[datetime]:
    if not s:
        return None
    t = str(s).replace("Z", "")
    try:
        return datetime.fromisoformat(t[:19])
    except ValueError:
        return None


def _duel_between_users(duel: Dict[str, Any], user_a: str, user_b: str) -> bool:
    pair = {str(user_a), str(user_b)}
    return {str(duel.get("from_user") or ""), str(duel.get("target_user") or "")} == pair


def _has_open_duel_between(duels: List[Dict[str, Any]], user_a: str, user_b: str, ym: str) -> bool:
    for d in duels:
        if not _duel_between_users(d, user_a, user_b):
            continue
        status = d.get("status")
        if status == "pending":
            return True
        if status == "active" and not d.get("settled") and str(d.get("month") or "") == ym:
            return True
    return False


def expire_pending_duels_if_needed(data_dir: Path) -> None:
    """pending 在邀约截止时间（第 N 个自然日 23:59）之后仍未处理则标记为 expired。"""
    now = china_now()
    with _locked_challenges_storage(data_dir):
        data = _load_duels(data_dir)
        duels = data.get("duels") or []
        changed = False
        for d in duels:
            if d.get("status") != "pending":
                continue
            ca = _parse_iso_datetime(str(d["created_at"])) if d.get("created_at") else None
            if ca:
                correct_exp = duel_invite_expires_at_from_created(ca).isoformat(timespec="seconds")
                if d.get("expires_at") != correct_exp:
                    d["expires_at"] = correct_exp
                    changed = True
            exp_s = d.get("expires_at")
            exp_dt = _parse_iso_datetime(str(exp_s)) if exp_s else None
            if not exp_dt:
                continue
            if now > exp_dt:
                d["status"] = "expired"
                d["expired_at"] = now.isoformat(timespec="seconds")
                changed = True
        if changed:
            _save_duels(data_dir, data)


def _settle_monthly_pool_if_needed_unlocked(data_dir: Path) -> None:
    """若存在未结算的上一自然月奖池，则瓜分。"""
    path = monthly_pool_path(data_dir)
    raw = _load_json(path, {"months": {}})
    months = raw.get("months") or {}
    if not isinstance(months, dict):
        months = {}
    today = china_today()
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
            block["settled_at"] = china_now_iso(timespec="seconds")
            block["settlement_note"] = "无人或空池"
            months[ym] = block
            changed = True
            continue

        best = -1
        counts: Dict[str, int] = {}
        for u in participants:
            c = competition_checkin_days_for_user(data_dir, u, ym)
            counts[u] = c
            if c > best:
                best = c
        winners = [u for u in participants if counts.get(u, 0) == best and best >= 0]
        share = pool // len(winners) if winners else 0
        remainder = pool - share * len(winners)

        payout_failed = False
        for w in winners:
            ok, _, _ = gamification_mod.apply_xp_delta(
                data_dir,
                w,
                share,
                source="monthly_pool_reward",
                lifetime_delta=max(0, share - MONTHLY_POOL_FEE_XP),
                transaction_id=f"monthly-pool:{ym}:reward:{w}",
            )
            if not ok:
                payout_failed = True
        if payout_failed:
            continue
        block["settled"] = True
        block["settled_at"] = china_now_iso(timespec="seconds")
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


def _settle_monthly_pool_if_needed(data_dir: Path) -> None:
    with _locked_challenges_storage(data_dir):
        _settle_monthly_pool_if_needed_unlocked(data_dir)


def get_monthly_pool_state(data_dir: Path, username: str) -> Dict[str, Any]:
    _settle_monthly_pool_if_needed(data_dir)
    today = china_today()
    ym = month_key(today)
    path = monthly_pool_path(data_dir)
    raw = _load_json(path, {"months": {}})
    months = raw.get("months") or {}
    block = months.get(ym) or {}
    participants = list(block.get("participants") or [])
    pool = int(block.get("pool_xp") or 0)
    joined = username in participants
    dim = monthrange(today.year, today.month)[1]
    race_max = max_competition_days_in_month(ym)
    in_prep = pool_preparation_phase(today)
    in_comp = pool_competition_phase(today)
    phase = "preparation" if in_prep else "competition"

    my_comp_days = competition_checkin_days_for_user(data_dir, username, ym) if joined else 0
    # 兼容旧字段：全月有效打卡天数（含准备期）
    my_month_valid_days = valid_checkin_days_for_user(data_dir, username, ym) if joined else 0

    runners: List[Dict[str, Any]] = []
    for u in participants:
        cd = competition_checkin_days_for_user(data_dir, u, ym)
        prog = (cd / race_max) if race_max > 0 else 0.0
        prog = max(0.0, min(1.0, float(prog)))
        runners.append(
            {
                "username": u,
                "competition_days": cd,
                "progress": round(prog, 4),
            }
        )
    runners.sort(key=lambda x: (-x["competition_days"], x["username"]))

    return {
        "month": ym,
        "pool_xp": pool,
        "participant_count": len(participants),
        "fee_xp": MONTHLY_POOL_FEE_XP,
        "stake_safety_reserve_xp": STAKE_SAFETY_RESERVE_XP,
        "join_window_open": pool_join_window_open(today),
        "join_window_last_day": JOIN_WINDOW_LAST_DAY,
        "preparation_last_day": PREPARATION_LAST_DAY,
        "competition_start_day": COMPETITION_START_DAY,
        "phase": phase,
        "preparation_phase": in_prep,
        "competition_phase": in_comp,
        "competition_days_max": race_max,
        "runners": runners,
        "joined": joined,
        "my_month_valid_days": my_month_valid_days,
        "my_competition_days": my_comp_days,
        "month_days": dim,
    }


def join_monthly_pool(data_dir: Path, username: str) -> Tuple[bool, str, Dict[str, Any]]:
    if not pool_join_window_open():
        return False, f"仅在每月 1～{JOIN_WINDOW_LAST_DAY} 日准备期内可加入群体挑战", {}

    _settle_monthly_pool_if_needed(data_dir)
    today = china_today()
    ym = month_key(today)
    path = monthly_pool_path(data_dir)
    already_joined = False
    with _locked_challenges_storage(data_dir):
        raw = _load_json(path, {"months": {}})
        months = raw.setdefault("months", {})
        block = months.get(ym) or {"pool_xp": 0, "participants": []}
        participants = list(block.get("participants") or [])
        if username in participants:
            already_joined = True
        else:
            ok, msg, _ = gamification_mod.spend_xp_with_reserve(
                data_dir,
                username,
                MONTHLY_POOL_FEE_XP,
                STAKE_SAFETY_RESERVE_XP,
                source="monthly_pool_fee",
                transaction_id=f"monthly-pool:{ym}:fee:{username}",
            )
            if not ok:
                return False, msg or "积分不足", {}

            participants.append(username)
            block["participants"] = participants
            block["pool_xp"] = int(block.get("pool_xp") or 0) + MONTHLY_POOL_FEE_XP
            months[ym] = block
            _save_json(path, raw)

    if already_joined:
        return False, "本月已加入", get_monthly_pool_state(data_dir, username)

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
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    if wager_xp not in WAGER_TIERS:
        return False, f"wager_xp 须为 {list(WAGER_TIERS)} 之一", None
    if from_user == target_user:
        return False, "不能挑战自己", None
    expire_pending_duels_if_needed(data_dir)
    now = china_now()
    created_iso = now.isoformat(timespec="seconds")
    expires_iso = duel_invite_expires_at_from_created(now).isoformat(timespec="seconds")
    duel_id = str(uuid.uuid4())
    row = {
        "id": duel_id,
        "month": None,
        "from_user": from_user,
        "target_user": target_user,
        "wager_xp": int(wager_xp),
        "status": "pending",
        "created_at": created_iso,
        "expires_at": expires_iso,
        "accepted_at": None,
        "settled": False,
        "winner": None,
    }
    with _locked_challenges_storage(data_dir):
        data = _load_duels(data_dir)
        duels = data.setdefault("duels", [])
        if _has_open_duel_between(duels, from_user, target_user, month_key(now.date())):
            return False, "你们之间已有待处理或本月进行中的 PK", None
        duels.append(row)
        _save_duels(data_dir, data)
    return True, "", enrich_duel_for_api(row)


def respond_duel(
    data_dir: Path,
    duel_id: str,
    target_user: str,
    accept: bool,
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    expire_pending_duels_if_needed(data_dir)
    with _locked_challenges_storage(data_dir):
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
        if found.get("status") == "expired":
            return False, "邀约已过期", None
        if found.get("status") != "pending":
            return False, "已处理", None
        if not accept:
            found["status"] = "declined"
            _save_duels(data_dir, data)
            return True, "", enrich_duel_for_api(found)

        w = int(found.get("wager_xp") or 0)
        a, b = found.get("from_user"), found.get("target_user")
        accept_now = china_now()
        accept_month = month_key(accept_now.date())
        if w > 0:
            stake_a_id = f"duel:{duel_id}:stake:{a}"
            stake_b_id = f"duel:{duel_id}:stake:{b}"
            ok1, msg1, _ = gamification_mod.spend_xp_with_reserve(
                data_dir,
                str(a),
                w,
                STAKE_SAFETY_RESERVE_XP,
                source="duel_stake",
                transaction_id=stake_a_id,
            )
            if not ok1:
                return False, f"发起方{msg1}", None
            ok2, msg2, _ = gamification_mod.spend_xp_with_reserve(
                data_dir,
                str(b),
                w,
                STAKE_SAFETY_RESERVE_XP,
                source="duel_stake",
                transaction_id=stake_b_id,
            )
            if not ok2:
                rolled_back, rollback_msg, _ = gamification_mod.rollback_spend_transaction(
                    data_dir,
                    str(a),
                    stake_a_id,
                    refund_source="duel_refund",
                )
                if not rolled_back:
                    return False, f"应战方{msg2}；发起方扣款回退失败：{rollback_msg}", None
                return False, f"应战方{msg2}", None
        found["month"] = accept_month
        found["status"] = "active"
        found["accepted_at"] = accept_now.isoformat(timespec="seconds")
        found["escrow_xp"] = w
        _save_duels(data_dir, data)
        return True, "", enrich_duel_for_api(found)


def settle_due_duels(data_dir: Path) -> None:
    """结算上月及更早未处理的 active 挑战。"""
    today = china_today()
    cur_ym = month_key(today)
    with _locked_challenges_storage(data_dir):
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
            da = duel_pk_days_for_user(data_dir, a, d)
            db = duel_pk_days_for_user(data_dir, b, d)
            winner = None
            if da > db:
                winner = a
            elif db > da:
                winner = b
            payout_ok = True
            if winner and w > 0:
                payout_ok, _, _ = gamification_mod.apply_xp_delta(
                    data_dir,
                    winner,
                    2 * w,
                    source="duel_reward",
                    lifetime_delta=w,
                    transaction_id=f"duel:{d.get('id')}:settlement:{winner}",
                )
            elif winner is None and w > 0:
                refund_a_ok, _, _ = gamification_mod.apply_xp_delta(
                    data_dir,
                    a,
                    w,
                    source="duel_refund",
                    lifetime_delta=0,
                    transaction_id=f"duel:{d.get('id')}:refund:{a}",
                )
                refund_b_ok, _, _ = gamification_mod.apply_xp_delta(
                    data_dir,
                    b,
                    w,
                    source="duel_refund",
                    lifetime_delta=0,
                    transaction_id=f"duel:{d.get('id')}:refund:{b}",
                )
                payout_ok = refund_a_ok and refund_b_ok
            if not payout_ok:
                continue
            d["settled"] = True
            d["settled_at"] = china_now_iso(timespec="seconds")
            d["days_a"] = da
            d["days_b"] = db
            rng = duel_pk_counting_range(d)
            if rng:
                d["pk_stats_start_date"] = rng[0].isoformat()
                d["pk_stats_end_date"] = rng[1].isoformat()
            d["winner"] = winner
            d["tie"] = winner is None
            changed = True
        if changed:
            _save_duels(data_dir, data)


def list_duels_for_user(data_dir: Path, username: str) -> List[Dict[str, Any]]:
    expire_pending_duels_if_needed(data_dir)
    settle_due_duels(data_dir)
    data = _load_duels(data_dir)
    duels = data.get("duels") or []
    out = []
    for d in duels:
        if d.get("from_user") == username or d.get("target_user") == username:
            row = enrich_duel_for_api(dict(d))
            fu = row.get("from_user")
            tu = row.get("target_user")
            if fu and tu:
                row["pk_checkin_days"] = {
                    str(fu): duel_pk_days_for_user(data_dir, str(fu), row),
                    str(tu): duel_pk_days_for_user(data_dir, str(tu), row),
                }
            else:
                row["pk_checkin_days"] = {}
            out.append(row)
    out.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return out


def pk_user_stats_from_duels(data_dir: Path, username: str) -> Dict[str, int]:
    """已结算 PK：参与次数与胜场（用于成就）。"""
    settle_due_duels(data_dir)
    data = _load_duels(data_dir)
    wins = 0
    matches = 0
    for d in data.get("duels") or []:
        if not d.get("settled"):
            continue
        a = str(d.get("from_user") or "")
        b = str(d.get("target_user") or "")
        if username not in (a, b):
            continue
        matches += 1
        if str(d.get("winner") or "") == username:
            wins += 1
    return {"pk_wins": wins, "pk_matches": matches}


def monthly_pk_board(data_dir: Path) -> Dict[str, Any]:
    """
    全站月度 PK 看板：上一自然月已结算战绩 + 本月进行中。
    """
    expire_pending_duels_if_needed(data_dir)
    settle_due_duels(data_dir)
    today = china_today()
    cur_ym = month_key(today)
    first = today.replace(day=1)
    prev_last = first - timedelta(days=1)
    prev_ym = month_key(prev_last)

    data = _load_duels(data_dir)
    duels = data.get("duels") or []
    settled_last: List[Dict[str, Any]] = []
    ongoing: List[Dict[str, Any]] = []

    for d in duels:
        ym = str(d.get("month") or "").strip()
        if not ym:
            continue
        st = d.get("status")
        if d.get("settled") and ym == prev_ym:
            row = enrich_duel_for_api(dict(d))
            fu = row.get("from_user")
            tu = row.get("target_user")
            if fu and tu:
                row["pk_checkin_days"] = {
                    str(fu): duel_pk_days_for_user(data_dir, str(fu), row),
                    str(tu): duel_pk_days_for_user(data_dir, str(tu), row),
                }
            else:
                row["pk_checkin_days"] = {}
            settled_last.append(row)
        elif (not d.get("settled")) and st == "active" and ym == cur_ym:
            row = enrich_duel_for_api(dict(d))
            fu = row.get("from_user")
            tu = row.get("target_user")
            if fu and tu:
                row["pk_checkin_days"] = {
                    str(fu): duel_pk_days_for_user(data_dir, str(fu), row),
                    str(tu): duel_pk_days_for_user(data_dir, str(tu), row),
                }
            else:
                row["pk_checkin_days"] = {}
            ongoing.append(row)

    settled_last.sort(key=lambda x: str(x.get("settled_at") or ""), reverse=True)
    ongoing.sort(key=lambda x: str(x.get("accepted_at") or ""), reverse=True)

    return {
        "prev_month": prev_ym,
        "current_month": cur_ym,
        "settled_last_month": settled_last,
        "ongoing_this_month": ongoing,
    }


def purge_user_challenges_refs(data_dir: Path, username: str) -> None:
    """
    管理员删除学生账号时：从月度奖池参与者与 1v1 决斗记录中移除该用户。
    不尝试回退已扣除的 XP；删除账号视为放弃相关挑战数据。
    """
    if not username:
        return
    with _locked_challenges_storage(data_dir):
        path = monthly_pool_path(data_dir)
        raw = _load_json(path, {"months": {}})
        months = raw.setdefault("months", {})
        for _ym, block in list(months.items()):
            if not isinstance(block, dict):
                continue
            participants = [
                str(x) for x in (block.get("participants") or []) if x and str(x) != username
            ]
            block["participants"] = participants
        _save_json(path, raw)

        data = _load_duels(data_dir)
        duels = data.get("duels") or []
        data["duels"] = [
            d
            for d in duels
            if str(d.get("from_user") or "") != username
            and str(d.get("target_user") or "") != username
        ]
        _save_duels(data_dir, data)

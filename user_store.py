"""
用户账号持久化：SQLite 表 users，替代 users.json。

首次启动若 users.sqlite3 中无记录且存在 users.json，则自动导入并重命名备份 users.json。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from app_time import china_now_iso
from storage_utils import PersistentStateLoadError, read_json_state

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_data_dir: Optional[Path] = None
_conn: Optional[sqlite3.Connection] = None
DEFAULT_INVITE_QUOTA = 15
_DEFAULT_INVITE_QUOTA = DEFAULT_INVITE_QUOTA
_OLD_DEFAULT_INVITE_QUOTA = 5
_SCHEMA_VERSION = 2
_USER_COLUMNS = (
    "username, password_hash, email, created_at, enabled, role, child_username, plan, "
    "invite_quota_limit, invite_quota_used"
)


class EmailAlreadyUsedError(ValueError):
    """A normalized email address is already bound to another account."""


class DuplicateEmailMigrationError(ValueError):
    """Legacy email conflicts require correction before the store can open."""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY NOT NULL,
    password_hash TEXT NOT NULL,
    email TEXT,
    created_at TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    role TEXT,
    child_username TEXT,
    plan TEXT NOT NULL DEFAULT 'free',
    invite_quota_limit INTEGER NOT NULL DEFAULT 15,
    invite_quota_used INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_users_role ON users (role);
"""


def init_user_store(data_dir: Path) -> None:
    """应用启动时调用；数据库路径为 data_dir / users.sqlite3。"""
    global _data_dir, _conn
    with _lock:
        if _data_dir != data_dir:
            if _conn is not None:
                try:
                    _conn.close()
                except Exception:
                    pass
                _conn = None
            _data_dir = data_dir
        if _conn is None:
            _open_conn_and_migrate_unlocked()


def close_connection() -> None:
    """关闭进程内 SQLite 连接（Gunicorn worker 退出或 atexit 时调用）。"""
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None


def users_json_path(data_dir: Optional[Path] = None) -> Path:
    root = data_dir if data_dir is not None else _data_dir
    if root is None:
        raise RuntimeError("init_user_store 未调用且未传入 data_dir")
    return root / "users.json"


def users_sqlite_path(data_dir: Optional[Path] = None) -> Path:
    root = data_dir if data_dir is not None else _data_dir
    if root is None:
        raise RuntimeError("init_user_store 未调用且未传入 data_dir")
    return root / "users.sqlite3"


def user_table_count() -> int:
    with _lock:
        conn = _ensure_conn_unlocked()
        cur = conn.execute("SELECT COUNT(*) FROM users")
        return int(cur.fetchone()[0])


def _ensure_conn_unlocked() -> sqlite3.Connection:
    global _conn
    if _data_dir is None:
        raise RuntimeError("user_store: init_user_store() 未调用")
    if _conn is None:
        _open_conn_and_migrate_unlocked()
    assert _conn is not None
    return _conn


def _open_conn_and_migrate_unlocked() -> None:
    global _conn
    assert _data_dir is not None
    path = _data_dir / "users.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(path),
        check_same_thread=False,
        timeout=30.0,
    )
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.executescript(_SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        _ensure_user_columns_unlocked(conn)
        _run_schema_migrations_unlocked(conn)
        conn.commit()
        _auto_migrate_from_json_unlocked(conn)
    except Exception:
        conn.rollback()
        conn.close()
        raise
    _conn = conn


def _ensure_user_columns_unlocked(conn: sqlite3.Connection) -> None:
    """为已存在的 users.sqlite3 补新列。"""
    cur = conn.execute("PRAGMA table_info(users)")
    columns = {str(row[1]) for row in cur.fetchall()}
    if "invite_quota_limit" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN invite_quota_limit INTEGER NOT NULL DEFAULT 15")
    if "invite_quota_used" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN invite_quota_used INTEGER NOT NULL DEFAULT 0")
    if "email_key" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN email_key TEXT")


def _run_schema_migrations_unlocked(conn: sqlite3.Connection) -> None:
    """Run one-time SQLite data migrations that cannot be expressed in CREATE TABLE defaults."""
    cur = conn.execute("PRAGMA user_version")
    try:
        version = int(cur.fetchone()[0] or 0)
    except (TypeError, ValueError):
        version = 0
    if version < 1:
        conn.execute(
            "UPDATE users SET invite_quota_limit = ? WHERE invite_quota_limit = ?",
            (_DEFAULT_INVITE_QUOTA, _OLD_DEFAULT_INVITE_QUOTA),
        )
    if version < 2:
        owners: Dict[str, str] = {}
        updates = []
        for username, email in conn.execute("SELECT username, email FROM users"):
            key = _email_key(email)
            if key and key in owners:
                raise DuplicateEmailMigrationError(
                    f"Duplicate normalized email for accounts {owners[key]!r} and {username!r}; "
                    "resolve the conflict in users.sqlite3 before restarting"
                )
            if key:
                owners[key] = str(username)
            updates.append((key, username))
        conn.executemany("UPDATE users SET email_key = ? WHERE username = ?", updates)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_key ON users (email_key)")
    if version < _SCHEMA_VERSION:
        conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")


def _email_key(value: Any) -> Optional[str]:
    return str(value or "").strip().casefold() or None


def _raise_email_conflict(error: Exception) -> None:
    if isinstance(error, sqlite3.IntegrityError) and "users.email_key" in str(error):
        raise EmailAlreadyUsedError("邮箱已被其他账号使用") from error


def _user_row_tuple(
    username: str,
    u: Dict[str, Any],
) -> tuple:
    """INSERT / UPSERT 用的元组，与表列顺序一致。"""
    ph = str(u.get("password_hash") or "")
    email = u.get("email")
    if email is not None:
        email = str(email).strip() or None
    created_at = str(u.get("created_at") or "").strip()
    if not created_at:
        created_at = china_now_iso(timespec="seconds")
    enabled = 0 if u.get("enabled") is False else 1
    role = u.get("role")
    if role is not None:
        role = str(role).strip() or None
    child_username = u.get("child_username")
    if child_username is not None:
        child_username = str(child_username).strip() or None
    plan = str(u.get("plan") or "free").strip() or "free"
    if plan not in ("free", "paid"):
        plan = "free"
    limit_raw = u.get("invite_quota_limit", _DEFAULT_INVITE_QUOTA)
    if limit_raw is None or limit_raw == "":
        invite_quota_limit = _DEFAULT_INVITE_QUOTA
    else:
        try:
            invite_quota_limit = int(limit_raw)
        except (TypeError, ValueError):
            invite_quota_limit = _DEFAULT_INVITE_QUOTA
    try:
        invite_quota_used = int(u.get("invite_quota_used", 0) or 0)
    except (TypeError, ValueError):
        invite_quota_used = 0
    invite_quota_limit = max(0, invite_quota_limit)
    invite_quota_used = max(0, invite_quota_used)
    return (
        username,
        ph,
        email,
        created_at,
        enabled,
        role,
        child_username,
        plan,
        invite_quota_limit,
        invite_quota_used,
        _email_key(email),
    )


def _upsert_user_unlocked(conn: sqlite3.Connection, username: str, u: Dict[str, Any]) -> None:
    row = _user_row_tuple(username, u)
    conn.execute(
        "INSERT INTO users (username, password_hash, email, created_at, enabled, role, child_username, plan, invite_quota_limit, invite_quota_used, email_key) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(username) DO UPDATE SET "
        "password_hash=excluded.password_hash, "
        "email=excluded.email, "
        "created_at=excluded.created_at, "
        "enabled=excluded.enabled, "
        "role=excluded.role, "
        "child_username=excluded.child_username, "
        "plan=excluded.plan, "
        "invite_quota_limit=excluded.invite_quota_limit, "
        "invite_quota_used=excluded.invite_quota_used, "
        "email_key=excluded.email_key",
        row,
    )


def _sync_users_table_unlocked(conn: sqlite3.Connection, users: Dict[str, Any]) -> None:
    """使表内容与 ``users`` 字典一致：删除不在字典中的行，对其余键 UPSERT。"""
    pairs: List[Tuple[str, Dict[str, Any]]] = []
    for uname, u in users.items():
        if not isinstance(u, dict):
            continue
        pairs.append((str(uname), u))
    wanted = [p[0] for p in pairs]
    if not wanted:
        conn.execute("DELETE FROM users")
        return
    placeholders = ",".join("?" * len(wanted))
    conn.execute(f"DELETE FROM users WHERE username NOT IN ({placeholders})", tuple(wanted))
    conn.execute("UPDATE users SET email_key = NULL WHERE email_key IS NOT NULL")
    for uname, u in pairs:
        _upsert_user_unlocked(conn, uname, u)


def _row_to_user_dict(row: tuple) -> Dict[str, Any]:
    (
        _,
        password_hash,
        email,
        created_at,
        enabled,
        role,
        child_username,
        plan,
        invite_quota_limit,
        invite_quota_used,
    ) = row
    d: Dict[str, Any] = {
        "password_hash": password_hash,
        "created_at": created_at,
        "enabled": bool(enabled),
    }
    if email is not None and str(email).strip() != "":
        d["email"] = email
    if plan and str(plan) != "free":
        d["plan"] = str(plan)
    if role:
        d["role"] = str(role)
        d["child_username"] = (child_username or "") if child_username is not None else ""
    try:
        quota_limit = int(invite_quota_limit)
    except (TypeError, ValueError):
        quota_limit = _DEFAULT_INVITE_QUOTA
    try:
        quota_used = int(invite_quota_used)
    except (TypeError, ValueError):
        quota_used = 0
    if quota_limit != _DEFAULT_INVITE_QUOTA:
        d["invite_quota_limit"] = max(0, quota_limit)
    if quota_used > 0:
        d["invite_quota_used"] = max(0, quota_used)
    return d


def _load_users_unlocked(conn: sqlite3.Connection) -> Dict[str, Any]:
    cur = conn.execute(f"SELECT {_USER_COLUMNS} FROM users ORDER BY username")
    out: Dict[str, Any] = {}
    for row in cur.fetchall():
        uname = str(row[0])
        out[uname] = _row_to_user_dict(row)
    return out


def _auto_migrate_from_json_unlocked(conn: sqlite3.Connection) -> None:
    cur = conn.execute("SELECT COUNT(*) FROM users")
    if int(cur.fetchone()[0]) > 0:
        return
    jf = _data_dir / "users.json"
    data = read_json_state(jf, {})
    if not data:
        return
    if any(not isinstance(user, dict) for user in data.values()):
        raise PersistentStateLoadError(f"Invalid legacy user account: {jf}")

    inserted = 0
    try:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None:
            conn.rollback()
            return
        to_sync: Dict[str, Any] = {}
        for uname, u in data.items():
            if not isinstance(u, dict):
                continue
            u2 = dict(u)
            if "enabled" not in u2:
                u2["enabled"] = True
            to_sync[str(uname)] = u2
            inserted += 1
        _sync_users_table_unlocked(conn, to_sync)
        conn.commit()
    except Exception as error:
        conn.rollback()
        logger.exception("从 users.json 迁移到 SQLite 失败")
        _raise_email_conflict(error)
        raise PersistentStateLoadError(f"Cannot safely migrate legacy users: {jf}") from error

    if inserted == 0:
        logger.warning("users.json 有键但无有效用户字典，未重命名源文件")
        return

    try:
        backup = jf.with_name(f"users.json.migrated_{int(time.time())}.bak")
        jf.rename(backup)
        logger.info("已从 users.json 自动迁移 %s 个用户到 users.sqlite3，备份: %s", inserted, backup.name)
    except OSError as e:
        logger.warning("迁移成功但重命名 users.json 失败（请手动移走避免下次重复导入）: %s", e)


def load_users() -> Dict[str, Any]:
    """加载全部用户，结构与旧版 users.json 一致（username 为外层键）。"""
    with _lock:
        conn = _ensure_conn_unlocked()
        return _load_users_unlocked(conn)


def get_user(username: str) -> Optional[Dict[str, Any]]:
    """按用户名读取单条记录，避免鉴权热路径反复加载完整用户表。"""
    with _lock:
        conn = _ensure_conn_unlocked()
        row = conn.execute(
            f"SELECT {_USER_COLUMNS} FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        return _row_to_user_dict(row) if row is not None else None


def get_email_owner(email: str, *, exclude: str = "") -> Optional[str]:
    """Look up a normalized email using the unique index."""
    key = _email_key(email)
    if key is None:
        return None
    with _lock:
        conn = _ensure_conn_unlocked()
        row = conn.execute(
            "SELECT username FROM users WHERE email_key = ? AND username != ?",
            (key, exclude),
        ).fetchone()
        return str(row[0]) if row is not None else None


def update_password_hash(username: str, expected_hash: str, new_hash: str) -> bool:
    """仅在旧值仍匹配时更新目标用户密码哈希，避免覆盖并发密码修改。"""
    with _lock:
        conn = _ensure_conn_unlocked()
        try:
            cur = conn.execute(
                "UPDATE users SET password_hash = ? WHERE username = ? AND password_hash = ?",
                (new_hash, username, expected_hash),
            )
            conn.commit()
            return cur.rowcount == 1
        except Exception:
            conn.rollback()
            logger.exception("更新用户密码哈希失败: %s", username)
            raise


def save_users(users: Dict[str, Any]) -> None:
    """保存用户表：与传入字典语义一致（多出的库中用户删除，其余按用户名 UPSERT）。"""
    with _lock:
        conn = _ensure_conn_unlocked()
        try:
            conn.execute("BEGIN IMMEDIATE")
            _sync_users_table_unlocked(conn, users)
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error("保存用户表失败: %s", e)
            _raise_email_conflict(e)
            raise


def mutate_users(
    mutator: Callable[[Dict[str, Any]], Any],
    *,
    usernames: Optional[Iterable[str]] = None,
    rollback_on_error: bool = True,
) -> Any:
    """
    Atomically mutate users, reading only ``usernames`` when supplied.

    The callback sees existing rows within that scope and may insert or delete scoped
    usernames. Only changed rows are written. Omitting the scope preserves the legacy
    whole-dictionary callback API. ``rollback_on_error`` is retained for compatibility;
    failed owned transactions always roll back.
    """
    with _lock:
        conn = _ensure_conn_unlocked()
        try:
            conn.execute("BEGIN IMMEDIATE")
            scope = None if usernames is None else set(map(str, usernames))
            if scope is None:
                users = _load_users_unlocked(conn)
            else:
                users = {}
                for username in sorted(scope):
                    row = conn.execute(
                        f"SELECT {_USER_COLUMNS} FROM users WHERE username = ?", (username,)
                    ).fetchone()
                    if row is not None:
                        users[username] = _row_to_user_dict(row)
            original = deepcopy(users)
            result = mutator(users)
            if scope is not None and set(users).difference(scope):
                raise ValueError("User mutation attempted to write outside its username scope")
            for username in original:
                if not isinstance(users.get(username), dict):
                    conn.execute("DELETE FROM users WHERE username = ?", (username,))
            for username, user in users.items():
                if (
                    isinstance(user, dict)
                    and username in original
                    and _email_key(user.get("email")) != _email_key(original[username].get("email"))
                ):
                    conn.execute("UPDATE users SET email_key = NULL WHERE username = ?", (username,))
            for username, user in users.items():
                if isinstance(user, dict) and user != original.get(username):
                    _upsert_user_unlocked(conn, str(username), user)
            conn.commit()
            return result
        except Exception as error:
            conn.rollback()
            logger.exception("事务化修改用户表失败")
            _raise_email_conflict(error)
            raise


def import_users_from_json(
    json_path: Path,
    *,
    data_dir: Path,
    replace_existing: bool = False,
    backup_json: bool = True,
) -> int:
    """
    显式从 JSON 文件导入用户（供迁移脚本使用）。

    须先读取并解析 JSON，再 ``init_user_store``，否则自动迁移可能先重命名 ``users.json`` 导致读盘失败。

    :param replace_existing: True 时先清空 SQLite 用户表再导入；False 时若表中已有行则报错。
    :return: 导入的用户条数。
    """
    raw = json_path.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("JSON 根须为对象（用户名 -> 用户字段）")
    if any(not isinstance(user, dict) for user in data.values()):
        raise ValueError("JSON 用户字段须为对象")

    init_user_store(data_dir)
    with _lock:
        conn = _ensure_conn_unlocked()
        cur = conn.execute("SELECT COUNT(*) FROM users")
        existing = int(cur.fetchone()[0])
        if existing > 0 and not replace_existing:
            raise ValueError(
                f"users.sqlite3 中已有 {existing} 条用户；若需覆盖请先备份数据库并传入 replace_existing=True"
            )
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])
            if existing > 0 and not replace_existing:
                raise ValueError("users.sqlite3 already contains users; replacement was not requested")
            to_sync: Dict[str, Any] = {}
            inserted = 0
            for uname, u in data.items():
                if not isinstance(u, dict):
                    continue
                u2 = dict(u)
                if "enabled" not in u2:
                    u2["enabled"] = True
                to_sync[str(uname)] = u2
                inserted += 1
            _sync_users_table_unlocked(conn, to_sync)
            conn.commit()
        except Exception as error:
            conn.rollback()
            _raise_email_conflict(error)
            raise

    if backup_json and json_path.is_file():
        try:
            bak = json_path.with_name(f"{json_path.name}.imported_{int(time.time())}.bak")
            json_path.rename(bak)
            logger.info("已备份 JSON: %s", bak.name)
        except OSError as e:
            logger.warning("导入后备份 JSON 失败: %s", e)

    return inserted

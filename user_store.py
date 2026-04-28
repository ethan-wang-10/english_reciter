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
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_data_dir: Optional[Path] = None
_conn: Optional[sqlite3.Connection] = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY NOT NULL,
    password_hash TEXT NOT NULL,
    email TEXT,
    created_at TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    role TEXT,
    child_username TEXT,
    plan TEXT NOT NULL DEFAULT 'free'
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
    _conn = sqlite3.connect(
        str(path),
        check_same_thread=False,
        timeout=30.0,
    )
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA synchronous=NORMAL")
    _conn.execute("PRAGMA busy_timeout=30000")
    _conn.executescript(_SCHEMA)
    _conn.commit()
    _auto_migrate_from_json_unlocked(_conn)


def _row_to_user_dict(row: tuple) -> Dict[str, Any]:
    _, password_hash, email, created_at, enabled, role, child_username, plan = row
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
    return d


def _insert_user_unlocked(conn: sqlite3.Connection, username: str, u: Dict[str, Any]) -> None:
    ph = str(u.get("password_hash") or "")
    email = u.get("email")
    if email is not None:
        email = str(email).strip() or None
    created_at = str(u.get("created_at") or "").strip()
    if not created_at:
        created_at = datetime.now().isoformat()
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
    conn.execute(
        "INSERT INTO users (username, password_hash, email, created_at, enabled, role, child_username, plan) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (username, ph, email, created_at, enabled, role, child_username, plan),
    )


def _auto_migrate_from_json_unlocked(conn: sqlite3.Connection) -> None:
    cur = conn.execute("SELECT COUNT(*) FROM users")
    if int(cur.fetchone()[0]) > 0:
        return
    jf = _data_dir / "users.json"
    if not jf.is_file():
        return
    try:
        raw = jf.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception as e:
        logger.warning("users.json 存在但无法解析，跳过自动迁移: %s", e)
        return
    if not isinstance(data, dict) or not data:
        return

    inserted = 0
    try:
        conn.execute("BEGIN IMMEDIATE")
        for uname, u in data.items():
            if not isinstance(u, dict):
                continue
            u2 = dict(u)
            if "enabled" not in u2:
                u2["enabled"] = True
            _insert_user_unlocked(conn, str(uname), u2)
            inserted += 1
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("从 users.json 迁移到 SQLite 失败")
        return

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
        cur = conn.execute(
            "SELECT username, password_hash, email, created_at, enabled, role, child_username, plan "
            "FROM users ORDER BY username"
        )
        out: Dict[str, Any] = {}
        for row in cur.fetchall():
            uname = str(row[0])
            out[uname] = _row_to_user_dict(row)
        return out


def save_users(users: Dict[str, Any]) -> None:
    """以全量替换方式保存（与原先 json.dump 整文件写入语义一致）。"""
    with _lock:
        conn = _ensure_conn_unlocked()
        try:
            conn.execute("DELETE FROM users")
            for uname, u in users.items():
                if not isinstance(u, dict):
                    continue
                _insert_user_unlocked(conn, str(uname), u)
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error("保存用户表失败: %s", e)


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

    init_user_store(data_dir)
    with _lock:
        conn = _ensure_conn_unlocked()
        cur = conn.execute("SELECT COUNT(*) FROM users")
        existing = int(cur.fetchone()[0])
        if existing > 0 and not replace_existing:
            raise ValueError(
                f"users.sqlite3 中已有 {existing} 条用户；若需覆盖请先备份数据库并传入 replace_existing=True"
            )
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM users")
        inserted = 0
        for uname, u in data.items():
            if not isinstance(u, dict):
                continue
            u2 = dict(u)
            if "enabled" not in u2:
                u2["enabled"] = True
            _insert_user_unlocked(conn, str(uname), u2)
            inserted += 1
        conn.commit()

    if backup_json and json_path.is_file():
        try:
            bak = json_path.with_name(f"{json_path.name}.imported_{int(time.time())}.bak")
            json_path.rename(bak)
            logger.info("已备份 JSON: %s", bak.name)
        except OSError as e:
            logger.warning("导入后备份 JSON 失败: %s", e)

    return inserted

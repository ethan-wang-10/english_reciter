"""
用户与管理员会话 token：SQLite 持久化，支持多 Gunicorn worker 与进程重启后仍有效。

聊天 SSE 仍依赖进程内队列，多 worker 下广播问题不在此模块解决。
"""

from __future__ import annotations

import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

SESSION_KIND_USER = "user"
SESSION_KIND_ADMIN = "admin"

_lock = threading.Lock()
_db_path: Optional[Path] = None
_conn: Optional[sqlite3.Connection] = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS auth_sessions (
    token TEXT PRIMARY KEY,
    principal TEXT NOT NULL,
    session_kind TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_principal_kind
    ON auth_sessions (principal, session_kind);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_expires ON auth_sessions (expires_at);
"""


def init_auth_session_store(data_dir: Path) -> None:
    """在应用启动时调用一次；数据库路径为 data_dir / sessions.sqlite3。

    若路径相对上次调用发生变化，会关闭旧连接（便于测试使用独立临时目录）。"""
    global _db_path, _conn
    with _lock:
        target = data_dir / "sessions.sqlite3"
        if _db_path == target and _conn is not None:
            return
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None
        _db_path = target


def _ensure_conn() -> sqlite3.Connection:
    global _conn
    if _db_path is None:
        raise RuntimeError("auth_session_store: init_auth_session_store() 未调用")
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    if _conn is None:
        _conn = sqlite3.connect(
            str(_db_path),
            check_same_thread=False,
            timeout=30.0,
        )
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.execute("PRAGMA busy_timeout=30000")
        _conn.executescript(_SCHEMA)
        _conn.commit()
    return _conn


def _cleanup_expired_unlocked(conn: sqlite3.Connection) -> None:
    now = time.time()
    conn.execute("DELETE FROM auth_sessions WHERE expires_at < ?", (now,))
    conn.commit()


def create_session(kind: str, principal: str, ttl: timedelta) -> str:
    """签发新 token 并写入库。"""
    token = secrets.token_urlsafe(32)
    exp = (datetime.now() + ttl).timestamp()
    with _lock:
        conn = _ensure_conn()
        _cleanup_expired_unlocked(conn)
        conn.execute(
            "INSERT INTO auth_sessions (token, principal, session_kind, expires_at) VALUES (?, ?, ?, ?)",
            (token, principal, kind, exp),
        )
        conn.commit()
    return token


def verify_session(token: str, kind: str) -> Optional[str]:
    """有效则返回 principal，否则返回 None 并删除过期行。"""
    if not token:
        return None
    now = time.time()
    with _lock:
        conn = _ensure_conn()
        cur = conn.execute(
            "SELECT principal, expires_at FROM auth_sessions WHERE token = ? AND session_kind = ?",
            (token, kind),
        )
        row = cur.fetchone()
        if not row:
            return None
        principal, exp = row[0], float(row[1])
        if exp < now:
            conn.execute("DELETE FROM auth_sessions WHERE token = ?", (token,))
            conn.commit()
            return None
        return str(principal)


def revoke_principal(kind: str, principal: str) -> None:
    """注销某身份下全部会话（如用户登出所有设备、删号）。"""
    with _lock:
        conn = _ensure_conn()
        conn.execute(
            "DELETE FROM auth_sessions WHERE session_kind = ? AND principal = ?",
            (kind, principal),
        )
        conn.commit()


def revoke_token(kind: str, token: str) -> None:
    with _lock:
        conn = _ensure_conn()
        conn.execute(
            "DELETE FROM auth_sessions WHERE session_kind = ? AND token = ?",
            (kind, token),
        )
        conn.commit()

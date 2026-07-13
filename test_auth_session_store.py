"""auth_session_store：SQLite 会话表行为测试。"""

import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from auth_session_store import (
    SESSION_KIND_ADMIN,
    SESSION_KIND_PASSWORD_RESET,
    SESSION_KIND_PASSWORD_RESET_COOLDOWN,
    SESSION_KIND_USER,
    create_session,
    create_session_if_absent,
    consume_session,
    init_auth_session_store,
    revoke_principal,
    revoke_token,
    verify_session,
)


@pytest.fixture
def session_dir(tmp_path: Path) -> Path:
    init_auth_session_store(tmp_path)
    return tmp_path


def test_user_session_verify_and_revoke(session_dir: Path) -> None:
    tok = create_session(SESSION_KIND_USER, "alice", timedelta(seconds=3600))
    assert verify_session(tok, SESSION_KIND_USER) == "alice"
    assert verify_session(tok, SESSION_KIND_ADMIN) is None
    revoke_principal(SESSION_KIND_USER, "alice")
    assert verify_session(tok, SESSION_KIND_USER) is None


def test_admin_session_kind_isolated(session_dir: Path) -> None:
    tok = create_session(SESSION_KIND_ADMIN, "admin", timedelta(seconds=60))
    assert verify_session(tok, SESSION_KIND_ADMIN) == "admin"
    revoke_token(SESSION_KIND_ADMIN, tok)
    assert verify_session(tok, SESSION_KIND_ADMIN) is None


def test_expired_session_not_verified(session_dir: Path) -> None:
    tok = create_session(SESSION_KIND_USER, "bob", timedelta(seconds=-1))
    assert verify_session(tok, SESSION_KIND_USER) is None


def test_password_reset_session_is_one_time(session_dir: Path) -> None:
    tok = create_session(SESSION_KIND_PASSWORD_RESET, "alice", timedelta(minutes=30))
    with sqlite3.connect(str(session_dir / "sessions.sqlite3")) as conn:
        stored = conn.execute(
            "SELECT token FROM auth_sessions WHERE session_kind = ?",
            (SESSION_KIND_PASSWORD_RESET,),
        ).fetchone()[0]
    assert stored != tok
    assert verify_session(tok, SESSION_KIND_PASSWORD_RESET) == "alice"
    assert consume_session(tok, SESSION_KIND_PASSWORD_RESET) == "alice"
    assert consume_session(tok, SESSION_KIND_PASSWORD_RESET) is None


def test_password_reset_cooldown_claim_is_atomic(session_dir: Path) -> None:
    first = create_session_if_absent(
        SESSION_KIND_PASSWORD_RESET_COOLDOWN,
        "alice",
        timedelta(seconds=60),
    )
    assert first
    assert create_session_if_absent(
        SESSION_KIND_PASSWORD_RESET_COOLDOWN,
        "alice",
        timedelta(seconds=60),
    ) is None
    revoke_token(SESSION_KIND_PASSWORD_RESET_COOLDOWN, first)
    assert create_session_if_absent(
        SESSION_KIND_PASSWORD_RESET_COOLDOWN,
        "alice",
        timedelta(seconds=60),
    )

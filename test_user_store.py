"""user_store：SQLite 用户表与 JSON 迁移测试。"""

import json
import sqlite3
from pathlib import Path

import pytest

from user_store import (
    DEFAULT_INVITE_QUOTA,
    close_connection,
    get_user,
    import_users_from_json,
    init_user_store,
    load_users,
    save_users,
    update_password_hash,
    user_table_count,
)


@pytest.fixture
def ud(tmp_path: Path) -> Path:
    return tmp_path


def test_save_load_roundtrip(ud: Path) -> None:
    init_user_store(ud)
    users = {
        "alice": {
            "password_hash": "x",
            "email": "a@ex.com",
            "created_at": "2026-01-01T00:00:00",
            "enabled": True,
            "plan": "free",
            "invite_quota_used": 2,
        },
        "bob_parent": {
            "password_hash": "y",
            "created_at": "2026-01-02T00:00:00",
            "enabled": True,
            "role": "parent",
            "child_username": "bob",
            "plan": "paid",
            "invite_quota_limit": 0,
        },
    }
    save_users(users)
    assert user_table_count() == 2
    got = load_users()
    assert got["alice"]["password_hash"] == "x"
    assert got["alice"].get("plan") is None  # free 省略与旧 JSON 行为一致
    assert got["alice"].get("invite_quota_limit") is None
    assert got["alice"]["invite_quota_used"] == 2
    assert got["bob_parent"]["role"] == "parent"
    assert got["bob_parent"]["child_username"] == "bob"
    assert got["bob_parent"]["plan"] == "paid"
    assert got["bob_parent"]["invite_quota_limit"] == 0


def test_get_user_and_conditional_password_hash_update(ud: Path) -> None:
    init_user_store(ud)
    save_users(
        {
            "alice": {
                "password_hash": "old",
                "created_at": "2026-01-01T00:00:00",
                "enabled": True,
            }
        }
    )

    assert get_user("missing") is None
    assert get_user("alice")["password_hash"] == "old"
    assert update_password_hash("alice", "stale", "new") is False
    assert get_user("alice")["password_hash"] == "old"
    assert update_password_hash("alice", "old", "new") is True
    assert get_user("alice")["password_hash"] == "new"


def test_auto_migrate_from_json(ud: Path) -> None:
    jf = ud / "users.json"
    jf.write_text(
        json.dumps(
            {
                "carol": {
                    "password_hash": "h",
                    "created_at": "2026-03-01T00:00:00",
                    "enabled": True,
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    init_user_store(ud)
    assert user_table_count() == 1
    assert load_users()["carol"]["password_hash"] == "h"
    assert not jf.is_file()


def test_old_default_invite_quota_migrates_to_current_default(ud: Path) -> None:
    init_user_store(ud)
    save_users(
        {
            "legacy": {
                "password_hash": "h",
                "created_at": "2026-03-01T00:00:00",
                "enabled": True,
                "invite_quota_limit": 5,
            },
            "custom": {
                "password_hash": "h",
                "created_at": "2026-03-01T00:00:00",
                "enabled": True,
                "invite_quota_limit": 3,
            },
        }
    )
    close_connection()
    with sqlite3.connect(str(ud / "users.sqlite3")) as conn:
        conn.execute("PRAGMA user_version = 0")
    init_user_store(ud)
    users = load_users()
    assert users["legacy"].get("invite_quota_limit") is None
    assert users["custom"]["invite_quota_limit"] == 3
    with sqlite3.connect(str(ud / "users.sqlite3")) as conn:
        row = conn.execute(
            "SELECT invite_quota_limit FROM users WHERE username = 'legacy'"
        ).fetchone()
    assert row[0] == DEFAULT_INVITE_QUOTA


def test_save_users_upsert_drops_removed(ud: Path) -> None:
    """save_users 仅 UPSERT 传入键，并删除库中已不在 dict 中的用户。"""
    init_user_store(ud)
    save_users(
        {
            "a": {"password_hash": "1", "created_at": "2026-01-01", "enabled": True},
            "b": {"password_hash": "2", "created_at": "2026-01-02", "enabled": True},
        }
    )
    assert user_table_count() == 2
    users = load_users()
    users["a"]["password_hash"] = "1b"
    del users["b"]
    save_users(users)
    assert user_table_count() == 1
    u = load_users()
    assert set(u.keys()) == {"a"}
    assert u["a"]["password_hash"] == "1b"


def test_import_replace(ud: Path) -> None:
    init_user_store(ud)
    jf = ud / "users.json"
    jf.write_text(
        json.dumps({"d": {"password_hash": "p", "created_at": "2026-01-01", "enabled": True}}),
        encoding="utf-8",
    )
    import_users_from_json(jf, data_dir=ud, replace_existing=True, backup_json=False)
    assert "d" in load_users()
    save_users({})
    jf.write_text(
        json.dumps({"e": {"password_hash": "q", "created_at": "2026-01-02", "enabled": True}}),
        encoding="utf-8",
    )
    import_users_from_json(jf, data_dir=ud, replace_existing=True, backup_json=False)
    u = load_users()
    assert "e" in u and "d" not in u

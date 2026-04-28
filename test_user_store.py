"""user_store：SQLite 用户表与 JSON 迁移测试。"""

import json
from pathlib import Path

import pytest

from user_store import import_users_from_json, init_user_store, load_users, save_users, user_table_count


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
        },
        "bob_parent": {
            "password_hash": "y",
            "created_at": "2026-01-02T00:00:00",
            "enabled": True,
            "role": "parent",
            "child_username": "bob",
            "plan": "paid",
        },
    }
    save_users(users)
    assert user_table_count() == 2
    got = load_users()
    assert got["alice"]["password_hash"] == "x"
    assert got["alice"].get("plan") is None  # free 省略与旧 JSON 行为一致
    assert got["bob_parent"]["role"] == "parent"
    assert got["bob_parent"]["child_username"] == "bob"
    assert got["bob_parent"]["plan"] == "paid"


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

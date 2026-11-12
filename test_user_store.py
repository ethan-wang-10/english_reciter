"""user_store：SQLite 用户表与 JSON 迁移测试。"""

import json
import importlib.util
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from user_store import (
    DEFAULT_INVITE_QUOTA,
    DuplicateEmailMigrationError,
    EmailAlreadyUsedError,
    close_connection,
    get_user,
    get_email_owner,
    import_users_from_json,
    init_user_store,
    load_users,
    mutate_users,
    save_users,
    update_password_hash,
    user_table_count,
)
from storage_utils import PersistentStateLoadError


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


def _account(**fields):
    return {"password_hash": "p", "created_at": "2026-01-01", **fields}


def test_scoped_mutation_reads_and_writes_only_selected_users(ud: Path, monkeypatch) -> None:
    import user_store

    init_user_store(ud)
    save_users({"alice": _account(), "bob": _account()})
    statements = []
    user_store._conn.set_trace_callback(statements.append)
    monkeypatch.setattr(user_store, "_load_users_unlocked", lambda _: pytest.fail("full table read"))
    try:
        assert mutate_users(
            lambda users: users["alice"].update(plan="paid"), usernames=["alice"]
        ) is None
    finally:
        user_store._conn.set_trace_callback(None)
    selects = [sql for sql in statements if sql.startswith("SELECT")]
    assert len(selects) == 1
    assert "WHERE username = 'alice'" in selects[0]
    writes = [sql for sql in statements if sql.startswith(("INSERT", "UPDATE", "DELETE"))]
    assert len(writes) == 1
    assert "'bob'" not in writes[0]
    assert get_user("alice")["plan"] == "paid"
    assert get_user("bob").get("plan") is None


def test_legacy_mutation_writes_only_changed_rows_and_noop_writes_nothing(ud: Path) -> None:
    import user_store

    init_user_store(ud)
    save_users({"alice": _account(), "bob": _account()})
    statements = []
    user_store._conn.set_trace_callback(statements.append)
    try:
        mutate_users(lambda users: users["alice"].update(plan="paid"))
        writes = [sql for sql in statements if sql.startswith(("INSERT", "UPDATE", "DELETE"))]
        assert len(writes) == 1
        statements.clear()
        mutate_users(lambda users: None)
        assert not any(sql.startswith(("INSERT", "UPDATE", "DELETE")) for sql in statements)
    finally:
        user_store._conn.set_trace_callback(None)


def test_scoped_mutation_insert_delete_and_out_of_scope_rollback(ud: Path) -> None:
    init_user_store(ud)
    save_users({"alice": _account(), "bob": _account()})

    def replace(users):
        del users["alice"]
        users["carol"] = _account()
        return "changed"

    assert mutate_users(replace, usernames=["alice", "carol"]) == "changed"
    assert set(load_users()) == {"bob", "carol"}
    with pytest.raises(ValueError, match="outside"):
        mutate_users(lambda users: users.update(bob=_account(plan="paid")), usernames=["carol"])
    assert get_user("bob").get("plan") is None


def test_normalized_email_unique_constraint_and_rollback(ud: Path) -> None:
    init_user_store(ud)
    save_users({"alice": _account(email=" Alice@Example.com "), "bob": _account()})
    assert get_email_owner("ALICE@example.COM") == "alice"
    assert get_email_owner("alice@example.com", exclude="alice") is None
    assert get_email_owner("") is None
    with pytest.raises(EmailAlreadyUsedError):
        mutate_users(
            lambda users: users["bob"].update(email="ALICE@example.COM", plan="paid"),
            usernames=["bob"],
        )
    assert "email" not in get_user("bob")
    assert get_user("bob").get("plan") is None
    with pytest.raises(EmailAlreadyUsedError):
        save_users({"one": _account(email="Straße@example.com"), "two": _account(email="STRASSE@example.com")})
    assert set(load_users()) == {"alice", "bob"}


def test_atomic_email_swap_and_full_replacement(ud: Path) -> None:
    init_user_store(ud)
    save_users({"alice": _account(email="a@ex.com"), "bob": _account(email="b@ex.com")})

    def swap(users):
        users["alice"]["email"], users["bob"]["email"] = users["bob"]["email"], users["alice"]["email"]

    mutate_users(swap, usernames=["alice", "bob"])
    assert get_email_owner("a@ex.com") == "bob"
    save_users({"alice": _account(email="a@ex.com"), "bob": _account(email="b@ex.com")})
    assert get_email_owner("a@ex.com") == "alice"


def test_duplicate_legacy_emails_fail_migration_without_losing_accounts(ud: Path) -> None:
    init_user_store(ud)
    save_users({"alice": _account(email="a@ex.com"), "bob": _account()})
    close_connection()
    with sqlite3.connect(str(ud / "users.sqlite3")) as conn:
        conn.execute("DROP INDEX idx_users_email_key")
        conn.execute("UPDATE users SET email = ' A@EX.COM ' WHERE username = 'bob'")
        conn.execute("PRAGMA user_version = 1")
    with pytest.raises(DuplicateEmailMigrationError, match="alice.*bob"):
        init_user_store(ud)
    with sqlite3.connect(str(ud / "users.sqlite3")) as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        conn.execute("UPDATE users SET email = NULL WHERE username = 'bob'")
    init_user_store(ud)
    assert get_email_owner("a@ex.com") == "alice"


def test_duplicate_json_emails_preserve_source_and_import_rolls_back(ud: Path) -> None:
    source = ud / "users.json"
    source.write_text(json.dumps({"one": _account(email="a@ex.com"), "two": _account(email="A@EX.COM")}), encoding="utf-8")
    with pytest.raises(EmailAlreadyUsedError):
        init_user_store(ud)
    assert source.is_file()
    source.unlink()
    init_user_store(ud)
    save_users({"existing": _account()})
    source.write_text(json.dumps({"one": _account(email="a@ex.com"), "two": _account(email="A@EX.COM")}), encoding="utf-8")
    with pytest.raises(EmailAlreadyUsedError):
        import_users_from_json(source, data_dir=ud, replace_existing=True)
    assert source.is_file()
    assert set(load_users()) == {"existing"}
    mutate_users(lambda users: users["existing"].update(plan="paid"), usernames=["existing"])
    assert get_user("existing")["plan"] == "paid"


@pytest.mark.parametrize("source_text", ["{broken", "[]", '{"alice": {"password_hash": "p"}, "bob": null}'])
def test_invalid_legacy_json_stops_startup_without_partial_import(ud: Path, source_text: str) -> None:
    source = ud / "users.json"
    source.write_text(source_text, encoding="utf-8")
    with pytest.raises(PersistentStateLoadError):
        init_user_store(ud)
    assert source.read_text(encoding="utf-8") == source_text
    with sqlite3.connect(str(ud / "users.sqlite3")) as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    source.write_text(json.dumps({"recovered": _account()}), encoding="utf-8")
    init_user_store(ud)
    assert set(load_users()) == {"recovered"}


def test_explicit_invalid_import_does_not_replace_existing_accounts(ud: Path) -> None:
    init_user_store(ud)
    save_users({"existing": _account()})
    source = ud / "import.json"
    source.write_text('{"new": {"password_hash": "p"}, "invalid": null}', encoding="utf-8")
    with pytest.raises(ValueError):
        import_users_from_json(source, data_dir=ud, replace_existing=True)
    assert set(load_users()) == {"existing"}
    assert source.is_file()


def _independent_store():
    spec = importlib.util.spec_from_file_location("independent_user_store", Path(__file__).with_name("user_store.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_independent_connections_do_not_lose_concurrent_updates(ud: Path) -> None:
    init_user_store(ud)
    save_users({"alice": _account()})
    stores = [_independent_store() for _ in range(4)]
    for store in stores:
        store.init_user_store(ud)

    def increment(store):
        for _ in range(20):
            store.mutate_users(
                lambda users: users["alice"].update(invite_quota_used=users["alice"].get("invite_quota_used", 0) + 1),
                usernames=["alice"],
            )

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(increment, stores))
        assert get_user("alice")["invite_quota_used"] == 80
    finally:
        for store in stores:
            store.close_connection()


def test_independent_connections_compete_for_email_atomically(ud: Path) -> None:
    init_user_store(ud)
    save_users({"alice": _account(), "bob": _account()})
    stores = [_independent_store() for _ in range(2)]
    for store in stores:
        store.init_user_store(ud)
    barrier = threading.Barrier(2)

    def assign(pair):
        store, username = pair
        barrier.wait(timeout=10)
        try:
            store.mutate_users(lambda users: users[username].update(email="SAME@EX.COM"), usernames=[username])
            return "saved"
        except store.EmailAlreadyUsedError:
            return "conflict"

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(assign, zip(stores, ["alice", "bob"])))
        assert sorted(outcomes) == ["conflict", "saved"]
        assert sum("email" in user for user in load_users().values()) == 1
    finally:
        for store in stores:
            store.close_connection()

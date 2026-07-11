"""Flask 入口的轻量契约与静态资源测试。"""

import os
import tempfile

import pytest


_WEB_DATA_DIR = tempfile.TemporaryDirectory(prefix="english-reciter-web-test-")
os.environ["ENGLISH_RECITER_DATA_DIR"] = _WEB_DATA_DIR.name

import simple_web_app as web  # noqa: E402


@pytest.fixture
def client():
    web.app.config.update(TESTING=True)
    with web.app.test_client() as test_client:
        yield test_client


def test_static_assets_use_public_cache_headers(client) -> None:
    response = client.get("/static/css/style.css")
    assert response.status_code == 200
    cache_control = response.headers.get("Cache-Control", "")
    assert "public" in cache_control
    assert "max-age=3600" in cache_control
    assert "no-cache" not in cache_control


def test_unused_invites_are_owner_scoped_recoverable_and_removed_after_use(
    client, monkeypatch, tmp_path
) -> None:
    alice = {
        "password_hash": "unused",
        "created_at": "2026-01-01T00:00:00",
        "enabled": True,
        "invite_quota_limit": 15,
        "invite_quota_used": 0,
    }
    users = {"alice": alice}
    monkeypatch.setattr(web, "INVITES_FILE", tmp_path / "invites.json")
    monkeypatch.setattr(web, "INVITE_CODE_KEY_FILE", tmp_path / ".invite-code.key")
    monkeypatch.setattr(web, "INVITES_LOCK_FILE", tmp_path / ".invites.lock")
    monkeypatch.setattr(web, "_invite_fernet_cache", None)
    monkeypatch.delenv("INVITE_CODE_ENCRYPTION_SECRET", raising=False)
    monkeypatch.setattr(web, "verify_token", lambda token: "alice" if token == "test" else None)
    monkeypatch.setattr(web, "get_user", lambda username: users.get(username))
    monkeypatch.setattr(web, "load_users", lambda: users)
    monkeypatch.setattr(web, "mutate_users", lambda mutator: mutator(users))
    headers = {"Authorization": "Bearer test"}

    created = client.post("/api/user/invites", headers=headers, json={})
    assert created.status_code == 201
    code = created.get_json()["invite_code"]
    data = web.load_invites()
    stored = data["invites"][0]
    assert "code_ciphertext" in stored
    assert stored["code_ciphertext"] != code
    assert "invite_code" not in stored

    # Simulate a process restart: the generated key file must recover the same code.
    monkeypatch.setattr(web, "_invite_fernet_cache", None)

    data["invites"].extend(
        [
            {
                "id": "legacy-hash-only",
                "code_hash": web._hash_invite_code("LEGACY2345"),
                "created_at": "2026-01-01T01:00:00",
                "created_by": "alice",
                "created_by_kind": "user",
                "used_at": None,
                "used_by": None,
            },
            {
                "id": "other-user",
                "code_hash": web._hash_invite_code("OTHER23456"),
                "code_ciphertext": web._encrypt_invite_code_for_storage("OTHER23456"),
                "created_at": "2026-01-01T02:00:00",
                "created_by": "mallory",
                "created_by_kind": "user",
                "used_at": None,
                "used_by": None,
            },
        ]
    )
    web.save_invites(data)

    listed = client.get("/api/user/invites/unused", headers=headers)
    assert listed.status_code == 200
    assert listed.headers["Cache-Control"] == "private, no-store"
    rows = {row["id"]: row for row in listed.get_json()["invites"]}
    assert set(rows) == {stored["id"], "legacy-hash-only"}
    assert rows[stored["id"]]["invite_code"] == code
    assert rows[stored["id"]]["selectable"] is True
    assert rows["legacy-hash-only"]["selectable"] is False
    assert rows["legacy-hash-only"]["unavailable_reason"] == "legacy_hash_only"
    assert "invite_code" not in rows["legacy-hash-only"]

    public_rows = {
        row["id"]: row
        for row in client.get("/api/user/invites", headers=headers).get_json()["invites"]
    }
    assert public_rows[stored["id"]]["selectable"] is True
    assert public_rows["legacy-hash-only"]["selectable"] is False

    alice["enabled"] = False
    denied, denied_error = web.register_user_with_invite("blocked", "secret1", None, code)
    assert denied is False
    assert denied_error == "邀请码无效或已使用"
    assert next(
        row for row in web.load_invites()["invites"] if row["id"] == stored["id"]
    )["used_at"] is None
    alice["enabled"] = True

    ok, error = web.register_user_with_invite("bob", "secret1", None, code)
    assert ok is True
    assert error == ""
    consumed = next(row for row in web.load_invites()["invites"] if row["id"] == stored["id"])
    assert consumed["used_by"] == "bob"
    assert "code_ciphertext" not in consumed

    after_use = client.get("/api/user/invites/unused", headers=headers).get_json()["invites"]
    assert {row["id"] for row in after_use} == {"legacy-hash-only"}


def test_unused_invites_reject_parent_session(client, monkeypatch) -> None:
    users = {
        "alice": {
            "password_hash": "unused",
            "created_at": "2026-01-01T00:00:00",
            "enabled": True,
        },
        "alice_parent": {
            "password_hash": "unused",
            "created_at": "2026-01-01T00:00:00",
            "enabled": True,
            "role": "parent",
            "child_username": "alice",
        },
    }
    monkeypatch.setattr(web, "verify_token", lambda token: "alice_parent")
    monkeypatch.setattr(web, "get_user", lambda username: users.get(username))

    response = client.get(
        "/api/user/invites/unused",
        headers={"Authorization": "Bearer parent-test"},
    )

    assert response.status_code == 403


def test_save_invites_keeps_previous_file_when_serialization_fails(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(web, "INVITES_FILE", tmp_path / "invites.json")
    original = {"invites": [{"id": "kept"}]}
    web.save_invites(original)

    def fail_dump(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(web.json, "dump", fail_dump)
    with pytest.raises(OSError, match="disk full"):
        web.save_invites({"invites": [{"id": "replacement"}]})

    assert web.load_invites() == original
    assert not list(tmp_path.glob(".invites.json.*.tmp"))


def test_recover_invite_supports_legacy_secret_key_ciphertext(
    monkeypatch, tmp_path
) -> None:
    from cryptography.fernet import Fernet

    code = "LEGACYKEY2"
    legacy_secret = "previous-secret"
    legacy_key = web.base64.urlsafe_b64encode(
        web.hashlib.sha256(
            (legacy_secret + "|english_reciter.invites.v1").encode("utf-8")
        ).digest()
    )
    ciphertext = web._INVITE_CODE_ENC_PREFIX + Fernet(legacy_key).encrypt(
        code.encode("utf-8")
    ).decode("ascii")
    current_key_file = tmp_path / ".invite-code.key"
    current_key_file.write_bytes(Fernet.generate_key())
    monkeypatch.setattr(web, "INVITE_CODE_KEY_FILE", current_key_file)
    monkeypatch.setattr(web, "_invite_fernet_cache", None)
    monkeypatch.delenv("INVITE_CODE_ENCRYPTION_SECRET", raising=False)
    monkeypatch.setenv("SECRET_KEY", legacy_secret)

    recovered = web._recover_invite_code(
        {
            "id": "legacy-secret",
            "code_hash": web._hash_invite_code(code),
            "code_ciphertext": ciphertext,
            "used_at": None,
        }
    )

    assert recovered == code


def test_purge_user_removes_owned_invites_before_username_can_be_reused(
    monkeypatch, tmp_path
) -> None:
    users = {
        "alice": {
            "password_hash": "unused",
            "created_at": "2026-01-01T00:00:00",
            "enabled": True,
        },
        "alice_parent": {
            "password_hash": "unused",
            "created_at": "2026-01-01T00:00:00",
            "enabled": True,
            "role": "parent",
            "child_username": "alice",
        },
    }
    monkeypatch.setattr(web, "DATA_DIR", tmp_path)
    monkeypatch.setattr(web, "INVITES_FILE", tmp_path / "invites.json")
    monkeypatch.setattr(web, "INVITES_LOCK_FILE", tmp_path / ".invites.lock")
    monkeypatch.setattr(web, "mutate_users", lambda mutator: mutator(users))
    monkeypatch.setattr(web, "get_user", lambda username: users.get(username))
    monkeypatch.setattr(web, "_revoke_user_tokens", lambda username: None)
    monkeypatch.setattr(web.challenges_mod, "purge_user_challenges_refs", lambda *args: None)
    (tmp_path / "alice").mkdir()
    (tmp_path / "alice" / "old-data.json").write_text("{}", encoding="utf-8")
    web.save_invites(
        {
            "invites": [
                {
                    "id": "owned-unused",
                    "created_by": "alice",
                    "created_by_kind": "user",
                    "used_at": None,
                },
                {
                    "id": "owned-used",
                    "created_by": "alice",
                    "created_by_kind": "user",
                    "used_at": "2026-01-02T00:00:00",
                },
                {
                    "id": "other",
                    "created_by": "mallory",
                    "created_by_kind": "user",
                    "used_at": None,
                },
            ]
        }
    )

    web._purge_student_account_completely("alice")

    assert "alice" not in users
    assert "alice_parent" not in users
    assert [row["id"] for row in web.load_invites()["invites"]] == ["other"]
    assert not (tmp_path / "alice").exists()


def test_purge_user_restores_directory_when_user_transaction_fails(
    monkeypatch, tmp_path
) -> None:
    user = {
        "password_hash": "unused",
        "created_at": "2026-01-01T00:00:00",
        "enabled": True,
    }
    monkeypatch.setattr(web, "DATA_DIR", tmp_path)
    monkeypatch.setattr(web, "INVITES_FILE", tmp_path / "invites.json")
    monkeypatch.setattr(web, "INVITES_LOCK_FILE", tmp_path / ".invites.lock")
    monkeypatch.setattr(web, "get_user", lambda username: user if username == "alice" else None)

    def fail_mutate(_mutator):
        raise RuntimeError("sqlite failed")

    monkeypatch.setattr(web, "mutate_users", fail_mutate)
    user_dir = tmp_path / "alice"
    user_dir.mkdir()
    (user_dir / "learning_data.json").write_text("{}", encoding="utf-8")
    original_invites = {"invites": [{"id": "kept"}]}
    web.save_invites(original_invites)

    with pytest.raises(RuntimeError, match="sqlite failed"):
        web._purge_student_account_completely("alice")

    assert (user_dir / "learning_data.json").is_file()
    assert web.load_invites() == original_invites


@pytest.mark.parametrize("payload", ["null", "[]", "{broken"])
def test_login_rejects_non_object_or_malformed_json(client, payload: str) -> None:
    response = client.post(
        "/api/auth/login",
        data=payload,
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.get_json()["error"] == "无效的JSON数据"


def test_register_rejects_non_object_json(client) -> None:
    response = client.post(
        "/api/auth/register",
        data="null",
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.get_json()["error"] == "无效的JSON数据"


def test_login_accepts_json_content_type_with_charset(client, monkeypatch) -> None:
    monkeypatch.setattr(web, "verify_user", lambda username, password: True)
    monkeypatch.setattr(
        web,
        "get_user",
        lambda username: {
            "password_hash": "unused",
            "created_at": "2026-01-01T00:00:00",
            "enabled": True,
        },
    )
    monkeypatch.setattr(web, "create_token", lambda username: "test-token")
    monkeypatch.setattr(
        web,
        "_auth_session_payload",
        lambda username: {
            "login_username": username,
            "is_parent": False,
            "child_username": None,
            "system_broadcast": None,
        },
    )

    response = client.post(
        "/api/auth/login",
        data='{"username":"alice","password":"secret"}',
        content_type="application/json; charset=UTF-8",
    )

    assert response.status_code == 200
    assert response.get_json()["access_token"] == "test-token"


def test_wordbank_search_builds_english_candidates_once_per_term(client, monkeypatch) -> None:
    monkeypatch.setattr(web, "verify_token", lambda token: "alice")
    monkeypatch.setattr(
        web,
        "get_user",
        lambda username: {
            "password_hash": "unused",
            "created_at": "2026-01-01T00:00:00",
            "enabled": True,
        },
    )
    monkeypatch.setattr(web, "_rate_allow", lambda key, limit: True)
    rows = [
        {"english": "apple", "chinese": "苹果"},
        {"english": "banana", "chinese": "香蕉"},
        {"english": "orange", "chinese": "橙子"},
    ]
    monkeypatch.setattr(web, "merge_wordbank_rows_for_search", lambda level: (rows, {"apple", "banana", "orange"}))
    monkeypatch.setattr(web, "get_wordbank_lemma_mappings", lambda: {})
    monkeypatch.setattr(
        web,
        "_first_lemma_in_csv_with_kind",
        lambda term, *args: (("apple", "plural") if term == "apples" else (term, "surface")),
    )
    candidate_calls = []

    def candidates(term, *args):
        candidate_calls.append(term)
        yield ("apple" if term == "apples" else term), "test"

    monkeypatch.setattr(web, "_iter_csv_lemma_candidates", candidates)

    response = client.get(
        "/api/wordbank/csv/search?q=apples,banana&nlp=0&heuristics=1",
        headers={"Authorization": "Bearer test"},
    )

    assert response.status_code == 200
    assert [row["english"] for row in response.get_json()["words"]] == ["apple", "banana"]
    assert candidate_calls == ["apples", "banana"]

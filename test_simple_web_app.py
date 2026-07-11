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

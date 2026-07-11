"""Flask 入口的轻量契约与静态资源测试。"""

from html.parser import HTMLParser
import gzip
import os
from pathlib import Path
import re
import tempfile

import pytest


_WEB_DATA_DIR = tempfile.TemporaryDirectory(prefix="english-reciter-web-test-")
os.environ["ENGLISH_RECITER_DATA_DIR"] = _WEB_DATA_DIR.name

import simple_web_app as web  # noqa: E402


class _IndexContractParser(HTMLParser):
    """Collect references and accessible names without adding a DOM dependency."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: list[str] = []
        self.references: set[str] = set()
        self.icon_references: set[str] = set()
        self.buttons: list[dict[str, object]] = []
        self._button_stack: list[dict[str, object]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(str(values["id"]))
        for name in ("aria-controls", "aria-labelledby", "aria-describedby"):
            self.references.update(str(values.get(name) or "").split())
        if tag == "use" and str(values.get("href") or "").startswith("#"):
            self.icon_references.add(str(values["href"])[1:])
        if tag == "button":
            button = {
                "id": values.get("id") or values.get("class") or "button",
                "has_text": False,
                "has_name": bool(values.get("aria-label") or values.get("title")),
            }
            self._button_stack.append(button)

    def handle_data(self, data: str) -> None:
        if self._button_stack and data.strip():
            self._button_stack[-1]["has_text"] = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "button" and self._button_stack:
            self.buttons.append(self._button_stack.pop())


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


def test_index_icon_and_accessibility_references_are_valid() -> None:
    parser = _IndexContractParser()
    index_text = Path("static/index.html").read_text(encoding="utf-8")
    parser.feed(index_text)

    script_icon_references: set[str] = set()
    for script in Path("static/js").glob("*.js"):
        script_icon_references.update(
            re.findall(r'<use\s+href=["\']#([a-z0-9-]+)', script.read_text(encoding="utf-8"))
        )

    duplicate_ids = sorted({value for value in parser.ids if parser.ids.count(value) > 1})
    missing_references = sorted(parser.references.difference(parser.ids))
    missing_icons = sorted((parser.icon_references | script_icon_references).difference(parser.ids))
    unnamed_buttons = sorted(
        str(button["id"])
        for button in parser.buttons
        if not button["has_text"] and not button["has_name"]
    )

    assert duplicate_ids == []
    assert missing_references == []
    assert missing_icons == []
    assert unnamed_buttons == []


def test_frontend_asset_versions_match() -> None:
    source = Path("static/index.html").read_text(encoding="utf-8")
    source += Path("static/js/app.js").read_text(encoding="utf-8")
    versions = set(re.findall(r"\?v=([a-zA-Z0-9-]+)", source))
    assert versions == {"20260711-duo8"}


def test_brand_color_pairs_keep_readable_contrast() -> None:
    css = Path("static/css/style.css").read_text(encoding="utf-8")

    def css_color(variable: str) -> str:
        match = re.search(rf"--{re.escape(variable)}:\s*(#[0-9a-fA-F]{{6}})\s*;", css)
        assert match is not None, f"missing CSS color token: {variable}"
        return match.group(1)

    def luminance(hex_color: str) -> float:
        channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    def contrast(first: str, second: str) -> float:
        light, dark = sorted((luminance(first), luminance(second)), reverse=True)
        return (light + 0.05) / (dark + 0.05)

    assert contrast(css_color("color-brand"), css_color("color-brand-ink")) >= 4.5
    assert contrast(css_color("color-action-secondary"), css_color("color-surface")) >= 4.5
    assert contrast(css_color("color-danger-action"), css_color("color-surface")) >= 4.5


def test_frontend_css_stays_within_gzip_budget() -> None:
    css = Path("static/css/style.css").read_bytes()
    assert len(gzip.compress(css, compresslevel=9)) <= 36 * 1024


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

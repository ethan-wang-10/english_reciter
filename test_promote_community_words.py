"""社区候选词条提升到 words_v2.json 的隔离测试。"""

from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest


_WEB_DATA_DIR = None
if "simple_web_app" not in sys.modules:
    _WEB_DATA_DIR = tempfile.TemporaryDirectory(prefix="english-reciter-promotion-test-")
    os.environ["ENGLISH_RECITER_DATA_DIR"] = _WEB_DATA_DIR.name

import simple_web_app as web
import wordbank_v2
from scripts import promote_community_words as promoter


def _community_entry(english: str, *, status: str | None = None) -> dict:
    entry = {
        "english": english,
        "chinese": "测试释义",
        "example": "This is a test.",
        "added_by": "parent",
        "added_at": "2026-07-14T10:00:00+08:00",
    }
    if status is not None:
        entry["status"] = status
    return entry


def _valid_ai_entry(english: str) -> dict:
    return {
        "english": english,
        "level": "高中",
        "phonetic": "/test/",
        "senses": [
            {
                "pos": "noun",
                "definition_zh": "测试释义",
                "example_en": f"We learned the word {english} today.",
                "example_cn": "我们今天学习了这个词。",
                "example_form": "",
            }
        ],
    }


@pytest.fixture
def isolated_wordbanks(monkeypatch, tmp_path):
    community_file = tmp_path / "data" / "_shared" / "community_wordbank.json"
    v2_file = tmp_path / "static" / "wordbanks" / "words_v2.json"
    v2_lock = tmp_path / "static" / "wordbanks" / ".words.lock"
    community_file.parent.mkdir(parents=True)
    v2_file.parent.mkdir(parents=True)
    v2_file.write_text("[]", encoding="utf-8")

    monkeypatch.setattr(web, "COMMUNITY_WB_FILE", community_file)
    monkeypatch.setattr(wordbank_v2, "WORDS_V2_FILE", v2_file)
    monkeypatch.setattr(wordbank_v2, "WORDS_INTERPROCESS_LOCKFILE", v2_lock)
    wordbank_v2.invalidate_words_v2_cache()
    yield community_file, v2_file
    wordbank_v2.invalidate_words_v2_cache()


def _write_community(path, entries: list[dict]) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "english_reciter.wordbank.community/v1",
                "version": 1,
                "words": entries,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _read_community(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_legacy_community_entries_default_to_pending(isolated_wordbanks) -> None:
    community_file, _ = isolated_wordbanks
    _write_community(community_file, [_community_entry(" Legacy  Word ")])

    snapshot = web.read_community_wordbank_snapshot()

    entry = snapshot["words"][0]
    assert snapshot["schema"] == "english_reciter.wordbank.community/v2"
    assert snapshot["version"] == 2
    assert snapshot["label"] == "社区词库（待审核）"
    assert entry["status"] == "pending"
    assert entry["promotion_attempts"] == 0
    assert entry["promoted_at"] is None


def test_malformed_community_file_is_never_overwritten(isolated_wordbanks) -> None:
    community_file, _ = isolated_wordbanks
    malformed = '{"words": [broken]}'
    community_file.write_text(malformed, encoding="utf-8")

    with pytest.raises(RuntimeError, match="已保留原文件"):
        web.read_community_wordbank_snapshot()

    assert community_file.read_text(encoding="utf-8") == malformed


def test_missing_community_file_is_reported_without_creating_it(
    isolated_wordbanks, monkeypatch
) -> None:
    community_file, _ = isolated_wordbanks
    community_file.unlink(missing_ok=True)
    monkeypatch.setattr(
        web,
        "read_community_wordbank_snapshot",
        lambda: pytest.fail("文件不存在时不应创建空社区文件"),
    )
    output = []

    result = promoter.promote_community_words(
        web,
        wordbank_v2,
        dry_run=True,
        print_fn=lambda message, **kwargs: output.append(str(message)),
    )

    assert result["source_missing"] is True
    assert not community_file.exists()
    assert any(str(community_file) in line for line in output)
    assert any("不存在" in line for line in output)


def test_valid_entry_is_appended_once_and_marked_promoted(
    isolated_wordbanks, monkeypatch
) -> None:
    community_file, v2_file = isolated_wordbanks
    _write_community(community_file, [_community_entry("benefit")])
    calls = []
    monkeypatch.setattr(web, "get_deepseek_api_key", lambda: "test-key")
    monkeypatch.setattr(
        web,
        "deepseek_generate_word_entries_v2",
        lambda words, level="": calls.append(list(words)) or [_valid_ai_entry("benefit")],
    )

    first = promoter.promote_community_words(web, wordbank_v2, print_fn=lambda *a, **k: None)
    second = promoter.promote_community_words(web, wordbank_v2, print_fn=lambda *a, **k: None)

    assert first["promoted_generated"] == 1
    assert second["selected"] == 0
    assert calls == [["benefit"]]
    assert [row["english"] for row in json.loads(v2_file.read_text(encoding="utf-8"))] == [
        "benefit"
    ]
    saved = _read_community(community_file)["words"][0]
    assert saved["status"] == "promoted"
    assert saved["promotion_attempts"] == 1
    assert saved["promoted_word_key"] == "benefit"
    assert saved["last_error"] is None


def test_existing_v2_entry_marks_promoted_without_ai(
    isolated_wordbanks, monkeypatch
) -> None:
    community_file, v2_file = isolated_wordbanks
    _write_community(community_file, [_community_entry("benefit", status="failed")])
    v2_file.write_text(json.dumps([_valid_ai_entry("benefit")]), encoding="utf-8")
    wordbank_v2.invalidate_words_v2_cache()
    monkeypatch.setattr(
        web,
        "deepseek_generate_word_entries_v2",
        lambda *args, **kwargs: pytest.fail("已有 V2 词条时不应调用 AI"),
    )

    result = promoter.promote_community_words(web, wordbank_v2, print_fn=lambda *a, **k: None)

    assert result["promoted_existing"] == 1
    saved = _read_community(community_file)["words"][0]
    assert saved["status"] == "promoted"
    assert saved["promotion_attempts"] == 0


def test_partial_failure_persists_and_retries_only_failed_entry(
    isolated_wordbanks, monkeypatch
) -> None:
    community_file, _ = isolated_wordbanks
    _write_community(
        community_file,
        [_community_entry("benefit"), _community_entry("challenge")],
    )
    monkeypatch.setattr(web, "get_deepseek_api_key", lambda: "test-key")
    replies = [
        [_valid_ai_entry("benefit")],
        [_valid_ai_entry("challenge")],
    ]
    calls = []

    def generate(words, level=""):
        calls.append(list(words))
        return replies.pop(0)

    monkeypatch.setattr(web, "deepseek_generate_word_entries_v2", generate)

    first = promoter.promote_community_words(
        web, wordbank_v2, batch_size=2, print_fn=lambda *a, **k: None
    )
    second = promoter.promote_community_words(
        web, wordbank_v2, batch_size=2, print_fn=lambda *a, **k: None
    )

    assert first["promoted_generated"] == 1
    assert first["failed"] == 1
    assert first["exit_code"] == 2
    assert second["promoted_generated"] == 1
    assert calls == [["benefit", "challenge"], ["challenge"]]
    by_key = {row["english"]: row for row in _read_community(community_file)["words"]}
    assert by_key["benefit"]["promotion_attempts"] == 1
    assert by_key["challenge"]["promotion_attempts"] == 2
    assert by_key["challenge"]["status"] == "promoted"


def test_new_community_entry_added_during_ai_call_is_preserved(
    isolated_wordbanks, monkeypatch
) -> None:
    community_file, _ = isolated_wordbanks
    _write_community(community_file, [_community_entry("benefit")])
    monkeypatch.setattr(web, "get_deepseek_api_key", lambda: "test-key")

    def generate(words, level=""):
        with web._community_wb_guard():
            latest = web._read_community_file_unlocked()
            latest["words"].append(web._normalize_community_entry(_community_entry("newcomer")))
            web._write_community_file_atomic(latest)
        return [_valid_ai_entry("benefit")]

    monkeypatch.setattr(web, "deepseek_generate_word_entries_v2", generate)

    promoter.promote_community_words(web, wordbank_v2, print_fn=lambda *a, **k: None)

    by_key = {row["english"]: row for row in _read_community(community_file)["words"]}
    assert set(by_key) == {"benefit", "newcomer"}
    assert by_key["benefit"]["status"] == "promoted"
    assert by_key["newcomer"]["status"] == "pending"


def test_invalid_or_extra_ai_entries_never_enter_v2(
    isolated_wordbanks, monkeypatch
) -> None:
    community_file, v2_file = isolated_wordbanks
    _write_community(community_file, [_community_entry("benefit")])
    monkeypatch.setattr(web, "get_deepseek_api_key", lambda: "test-key")
    invalid = _valid_ai_entry("benefit")
    invalid["senses"][0]["example_en"] = "中文例句"
    monkeypatch.setattr(
        web,
        "deepseek_generate_word_entries_v2",
        lambda words, level="": [invalid, _valid_ai_entry("hallucinated")],
    )

    result = promoter.promote_community_words(web, wordbank_v2, print_fn=lambda *a, **k: None)

    assert result["failed"] == 1
    assert json.loads(v2_file.read_text(encoding="utf-8")) == []
    saved = _read_community(community_file)["words"][0]
    assert saved["status"] == "failed"
    assert saved["promotion_attempts"] == 1


def test_v2_write_failure_keeps_entry_retryable(isolated_wordbanks, monkeypatch) -> None:
    community_file, _ = isolated_wordbanks
    _write_community(community_file, [_community_entry("benefit")])
    monkeypatch.setattr(web, "get_deepseek_api_key", lambda: "test-key")
    monkeypatch.setattr(
        web,
        "deepseek_generate_word_entries_v2",
        lambda words, level="": [_valid_ai_entry("benefit")],
    )
    monkeypatch.setattr(
        wordbank_v2,
        "append_words_v2_entries",
        lambda rows: (_ for _ in ()).throw(OSError("disk full")),
    )

    result = promoter.promote_community_words(web, wordbank_v2, print_fn=lambda *a, **k: None)

    assert result["exit_code"] == 2
    saved = _read_community(community_file)["words"][0]
    assert saved["status"] == "failed"
    assert "写入 words_v2.json 失败" in saved["last_error"]

import csv
import json
import os

import pytest

from question_authoring import AuthoringError, QuestionAuthoringService
from test_question_authoring import _claim, _raw, _submit, authoring
from test_simple_web_app import web


def _csv_row(english="benefit", level="高中", chinese="n. 益处"):
    return {
        "english": english,
        "chinese": chinese,
        "level": level,
        "example1": f"Daily exercise has a clear {english} for physical health.",
    }


def _v2_entry(english="benefit", level="GRE", chinese="益处"):
    return {
        "english": english,
        "level": level,
        "senses": [{
            "pos": "noun",
            "definition_zh": chinese,
            "example_en": f"Daily exercise has a clear {english} for physical health.",
        }],
    }


@pytest.fixture
def wordbank(monkeypatch, tmp_path):
    csv_path = tmp_path / "words.csv"
    v2_path = tmp_path / "words_v2.json"
    monkeypatch.setattr(web, "WORDS_CSV_FILE", csv_path)
    monkeypatch.setattr(web, "WORDS_INTERPROCESS_LOCKFILE", tmp_path / ".wordbank.lock")
    monkeypatch.setattr(web.wordbank_v2, "WORDS_V2_FILE", v2_path)
    monkeypatch.setattr(web.wordbank_v2, "WORDS_INTERPROCESS_LOCKFILE", tmp_path / ".wordbank.lock")
    for name, value in (
        ("_words_csv_cache", None), ("_words_csv_cache_mtime", 0.0),
        ("_words_csv_by_key_cache", None), ("_words_csv_by_key_cache_mtime", -1.0),
        ("_merge_wordbank_rows_cache", {}), ("_merge_wordbank_rows_cache_rev", (-1.0, -1.0)),
    ):
        monkeypatch.setattr(web, name, value)
    for name, value in (
        ("_words_v2_cache", None), ("_words_v2_cache_mtime", 0.0), ("_words_v2_by_key", None),
    ):
        monkeypatch.setattr(web.wordbank_v2, name, value)

    def write(csv_rows, v2_rows):
        with csv_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(_csv_row()))
            writer.writeheader()
            writer.writerows(csv_rows)
        previous_mtime = v2_path.stat().st_mtime if v2_path.exists() else 0.0
        v2_path.write_text(json.dumps(v2_rows, ensure_ascii=False), encoding="utf-8")
        if previous_mtime:
            os.utime(v2_path, (previous_mtime + 2, previous_mtime + 2))

    return write


def test_sources_apply_level_filter_after_v2_precedence(wordbank):
    wordbank([_csv_row()], [_v2_entry()])

    assert web.gaokao_question_sources("高中") == []
    assert web.gaokao_question_sources("GRE") == [web._current_gaokao_source("benefit")]
    assert web.gaokao_question_sources() == [web._current_gaokao_source("benefit")]


def test_sources_use_last_csv_duplicate_before_filtering(wordbank):
    wordbank([
        _csv_row(level="高中", chinese="n. 好处"),
        _csv_row(english=" BENEFIT ", level="GRE", chinese="n. 益处"),
    ], [])

    assert web.gaokao_question_sources("高中") == []
    assert web.gaokao_question_sources("GRE") == [web._current_gaokao_source("benefit")]
    assert web.gaokao_question_sources() == [web._current_gaokao_source("benefit")]


def test_sources_keep_v2_only_and_csv_only_words_sorted_once(wordbank):
    wordbank([_csv_row("novel"), _csv_row("benefit")], [
        _v2_entry(level="高中", chinese="好处"),
        _v2_entry(level="高中", chinese="益处"),
        _v2_entry("advantage", level="高中"),
    ])

    expected = [web._current_gaokao_source(key) for key in ("advantage", "benefit", "novel")]
    assert web.gaokao_question_sources("高中") == expected


def test_invalid_canonical_source_does_not_fall_back_to_old_csv(wordbank):
    wordbank([_csv_row()], [{"english": "benefit", "level": "GRE", "senses": []}])

    assert web.gaokao_question_sources() == []
    assert web.gaokao_question_sources("高中") == []
    assert web._current_gaokao_source("benefit") is None


@pytest.mark.parametrize("storage", ["csv_duplicate", "v2_override"])
def test_claim_from_canonical_sources_accepts_unchanged_upload(authoring, wordbank, storage):
    if storage == "csv_duplicate":
        wordbank([_csv_row(chinese="n. 好处"), _csv_row()], [])
    else:
        wordbank([_csv_row(level="GRE", chinese="n. 好处")], [_v2_entry(level="高中")])
    service = QuestionAuthoringService(web.gaokao_question_sources, web._current_gaokao_source)

    job = _claim(service, level="高中")
    assert len(job["items"]) == 1
    result = _submit(service, job, _raw())

    assert result["items"][0]["status"] == "awaiting_recognition_blind"


def test_external_v2_edit_invalidates_lookup_and_rejects_claim_upload(authoring, wordbank):
    wordbank([], [_v2_entry(level="高中")])
    service = QuestionAuthoringService(web.gaokao_question_sources, web._current_gaokao_source)
    job = _claim(service, level="高中")
    original = web._current_gaokao_source("benefit")
    wordbank([], [_v2_entry(level="高中", chinese="救济金")])

    assert web._current_gaokao_source("benefit") != original
    with pytest.raises(AuthoringError, match="wordbank source changed") as raised:
        _submit(service, job, _raw())
    assert raised.value.status == 409

import copy

import pytest

import gaokao_backfill
import gaokao_questions as questions
from question_authoring import AuthoringError
from test_question_authoring import NOW, _claim, _generated, authoring
from test_simple_web_app import _new_v2_entry, web


@pytest.fixture
def lexical_import(authoring, monkeypatch):
    service, sources = authoring
    calls, appended, locked_phases = [], {}, []
    monkeypatch.setattr(web, "verify_token", lambda token: "alice")
    monkeypatch.setattr(web, "get_user", lambda username: {"password_hash": "unused", "enabled": True})
    monkeypatch.setattr(web, "is_paid_user", lambda username: True)
    monkeypatch.setattr(web, "_rate_allow", lambda *args: True)
    monkeypatch.setattr(web, "_import_jobs_async_enabled", lambda: False)
    monkeypatch.setattr(web, "_read_troubles_unlocked", lambda: {"mappings": {}, "difficult": {}})
    monkeypatch.setattr(web, "_vocab_import_spacy_accepts_surface", lambda *args: True)
    monkeypatch.setattr(web, "_wordbank_lemma_spacy_enabled", lambda: False)
    monkeypatch.setattr(web, "_normalize_import_english_surface", lambda value: value.lower())
    monkeypatch.setattr(web, "get_deepseek_api_key", lambda: "mock-key")
    monkeypatch.setattr(web.wordbank_v2, "get_v2_english_key_set", lambda: set(appended))
    monkeypatch.setattr(web.wordbank_v2, "invalidate_words_v2_cache", lambda: None)
    monkeypatch.setattr(web, "invalidate_merge_wordbank_rows_cache", lambda: None)
    monkeypatch.setattr(web, "record_surfaces_to_difficult", lambda *args: pytest.fail("successful import must not create failures"))

    def assert_locked(phase):
        with gaokao_backfill.generation_job_lock(blocking=False) as acquired:
            assert not acquired, f"generation lock was released during {phase}"
        locked_phases.append(phase)

    def generate(words, level="", include_gaokao_candidate=False):
        assert_locked("model")
        calls.append((list(words), include_gaokao_candidate))
        with pytest.raises(AuthoringError) as raised:
            service.claim({"worker_id": "concurrent", "request_id": "concurrent"}, now=NOW)
        assert raised.value.status == 409
        rows = [_new_v2_entry(word) for word in words]
        if not include_gaokao_candidate:
            for row in rows:
                row.pop("gaokao_question")
        return rows

    def append(rows):
        assert_locked("vocabulary write")
        appended.update({row["english"]: copy.deepcopy(row) for row in rows})
        return len(rows), []

    persist = questions.persist_candidate_pool_result

    def persist_locked(records, errors, **kwargs):
        assert_locked("question write")
        return persist(records, errors, **kwargs)

    monkeypatch.setattr(web, "deepseek_generate_word_entries_v2", generate)
    monkeypatch.setattr(web.wordbank_v2, "append_words_v2_entries", append)
    monkeypatch.setattr(questions, "persist_candidate_pool_result", persist_locked)
    web.app.config.update(TESTING=True)
    return service, web.app.test_client(), calls, appended, locked_phases


@pytest.mark.parametrize("generated", [False, True])
def test_mixed_import_generates_questions_only_for_unprotected_words(lexical_import, generated):
    service, client, calls, appended, locked_phases = lexical_import
    if generated:
        _generated(service)
    else:
        _claim(service)
    external_before = copy.deepcopy(questions.load_bank()["candidates"]["benefit"])
    response = client.post(
        "/api/wordbank/csv/import-words", headers={"Authorization": "Bearer test"},
        json={"words": "benefit,novel", "level": "高中", "also_add_to_queue": False},
    )
    assert response.status_code == 200
    assert calls == [(["novel"], True), (["benefit"], False)]
    assert set(appended) == {"benefit", "novel"}
    assert response.get_json()["new_in_csv"] == 2
    assert response.get_json()["gaokao_questions"]["generated_words"] == ["novel"]
    assert response.get_json()["gaokao_questions"]["generation_failed_words"] == []
    assert questions.load_bank()["candidates"]["benefit"] == external_before
    assert locked_phases.count("model") == 2
    assert locked_phases.count("vocabulary write") == 2
    assert locked_phases.count("question write") == 1
    with gaokao_backfill.generation_job_lock(blocking=False) as acquired:
        assert acquired


def test_busy_question_task_does_not_call_model_or_write_words(lexical_import):
    _, client, calls, appended, _ = lexical_import
    with gaokao_backfill.generation_job_lock(blocking=False) as acquired:
        assert acquired
        response = client.post(
            "/api/wordbank/csv/import-words", headers={"Authorization": "Bearer test"},
            json={"words": "novel", "also_add_to_queue": False},
        )
    assert response.status_code == 409
    assert calls == []
    assert appended == {}


@pytest.mark.parametrize("missing_example", [False, True])
def test_combined_finalize_does_not_record_missing_question_errors_for_external_words(authoring, missing_example):
    service, _ = authoring
    _claim(service)
    raw = _new_v2_entry("benefit")
    raw.pop("gaokao_question")
    entry = web.wordbank_v2.finalize_v2_entry_from_deepseek(raw)
    assert entry is not None
    if missing_example:
        entry["senses"] = []
    before = copy.deepcopy(questions.load_bank())
    assert web.finalize_combined_gaokao_candidates([raw], [entry]) == ({}, {})
    assert questions.load_bank() == before

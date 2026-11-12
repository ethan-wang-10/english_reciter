"""Cross-module rollout contracts for routes and durable provider calls."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from threading import Event
import urllib.error

import pytest

from import_job_store import ImportJobStore, ImportLeaseLost, ImportOutcomeUnknown
import simple_web_app as web


@pytest.fixture(autouse=True)
def disable_background_workers(monkeypatch):
    monkeypatch.setitem(web.app.config, "TESTING", True)
    monkeypatch.setenv("GAOKAO_AUTO_BACKFILL_ENABLED", "false")


@pytest.mark.parametrize("group,name,path,method", [
    ("auth", "register", "/api/auth/register", "POST"),
    ("wordbank", "search_wordbank_csv", "/api/wordbank/csv/search", "GET"),
    ("chat", "api_chat_messages_get", "/api/chat/messages", "GET"),
])
def test_extracted_handlers_keep_aliases_and_live_injected_services(monkeypatch, group, name, path, method):
    handler = getattr(web, name)
    assert handler.__module__ == "web_" + group + "_routes"
    assert web.app.view_functions[group + "." + name] is handler
    calls = []
    monkeypatch.setattr(web, "_rate_allow", lambda key, maximum: calls.append(key) or False)
    with web.app.test_request_context(path, method=method, json={}):
        response, status = handler() if name == "register" else handler.__wrapped__("alice")
    assert status == 429
    assert response.get_json()["error"]
    assert len(calls) == 1


def test_learning_handler_uses_the_current_injected_reciter_services(monkeypatch):
    sentinel = object()

    @contextmanager
    def session(username):
        assert username == "alice"
        yield sentinel

    monkeypatch.setattr(web, "user_reciter_session", session)
    monkeypatch.setattr(web, "_summary_payload_from_reciter", lambda reciter: {"live": reciter is sentinel})
    with web.app.test_request_context("/api/words/summary"):
        response, status = web.get_words_summary.__wrapped__("alice")
    assert status == 200
    assert response.get_json() == {"live": True}
    assert web.app.view_functions["learning.get_words_summary"] is web.get_words_summary


def test_extracted_protected_routes_still_require_authentication():
    with web.app.test_client() as client:
        for path in ("/api/words/summary", "/api/wordbank/csv", "/api/chat/messages", "/api/user/settings"):
            response = client.get(path)
            assert response.status_code == 401, path


def test_two_workers_cannot_run_the_same_provider_step_concurrently(tmp_path):
    store = ImportJobStore(tmp_path / "jobs.sqlite3")
    store.enqueue("vocab_import", "alice", {})
    job = store.claim_next()
    entered, release = Event(), Event()
    calls = []

    def provider():
        calls.append("charged")
        entered.set()
        assert release.wait(5)
        return {"content": "saved"}

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(store.external_step, job["job_id"], job["attempts"], "same", provider)
        try:
            assert entered.wait(5)
            with pytest.raises(ImportOutcomeUnknown):
                ImportJobStore(store.path).external_step(job["job_id"], job["attempts"], "same", provider)
        finally:
            release.set()
        assert future.result(timeout=5) == {"content": "saved"}
    assert calls == ["charged"]
    assert store.external_step(job["job_id"], job["attempts"], "same", provider) == {"content": "saved"}
    assert calls == ["charged"]


def test_provider_result_cannot_be_committed_after_the_job_loses_its_lease(tmp_path):
    store = ImportJobStore(tmp_path / "jobs.sqlite3")
    store.enqueue("vocab_import", "alice", {})
    job = store.claim_next()

    def provider():
        with store._connect() as connection:
            connection.execute("UPDATE import_jobs SET attempts=attempts+1 WHERE id=?", (job["job_id"],))
        return {"content": "stale paid response"}

    with pytest.raises(ImportLeaseLost):
        store.external_step(job["job_id"], job["attempts"], "request", provider)
    with store._connect() as connection:
        row = connection.execute("SELECT status, result_json FROM import_external_steps").fetchone()
        assert tuple(row) == ("started", None)


def test_deepseek_checkpoint_survives_reclaim_and_keeps_response_metadata(tmp_path, monkeypatch):
    store = ImportJobStore(tmp_path / "jobs.sqlite3")
    store.enqueue("vocab_import", "alice", {})
    first = store.claim_next()
    calls = []

    def provider(*args, **kwargs):
        calls.append("charged")
        return web._DeepSeekResponse("saved text", "length")

    monkeypatch.setattr(web, "_deepseek_chat_request", provider)
    with web.app.test_request_context("/"):
        web.g.import_job_context = (store, first)
        response = web._deepseek_chat([{"role": "user", "content": "same prompt"}])
        assert response.finish_reason == "length"
    stale = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(timespec="seconds")
    with store._connect() as connection:
        connection.execute("UPDATE import_jobs SET updated_at=? WHERE id=?", (stale, first["job_id"]))
    second = store.claim_next()
    with web.app.test_request_context("/"):
        web.g.import_job_context = (ImportJobStore(store.path), second)
        response = web._deepseek_chat([{"role": "user", "content": "same prompt"}])
        assert response == "saved text"
        assert response.finish_reason == "length"
    assert calls == ["charged"]


def test_durable_provider_failure_does_not_retry_http_or_replay_an_uncertain_step(tmp_path, monkeypatch):
    store = ImportJobStore(tmp_path / "jobs.sqlite3")
    store.enqueue("vocab_import", "alice", {})
    job = store.claim_next()
    calls = []

    def fail(*args, **kwargs):
        calls.append("request")
        raise urllib.error.URLError("response lost")

    monkeypatch.setattr(web, "get_deepseek_api_key", lambda: "test-only-key")
    monkeypatch.setattr(web, "_ssl_context_for_https", lambda: None)
    monkeypatch.setattr(web, "DEEPSEEK_HTTP_RETRIES", 3)
    monkeypatch.setattr(web.deepseek_policy, "check_before_request", lambda: None)
    monkeypatch.setattr(web.urllib.request, "urlopen", fail)
    with web.app.test_request_context("/"):
        web.g.import_job_context = (store, job)
        with pytest.raises(web.deepseek_policy.DeepSeekRequestError):
            web._deepseek_chat([{"role": "user", "content": "billable prompt"}], strict_errors=True)
        with pytest.raises(ImportOutcomeUnknown):
            web._deepseek_chat([{"role": "user", "content": "billable prompt"}], strict_errors=True)
    assert calls == ["request"]


def test_crash_after_word_write_restores_the_candidate_without_another_paid_request(tmp_path, monkeypatch):
    class SimulatedCrash(BaseException):
        pass

    store = ImportJobStore(tmp_path / "jobs.sqlite3")
    payload = {"words": "novel", "also_add_to_queue": False}
    store.enqueue("vocab_import", "alice", payload)
    first = store.claim_next()
    entry = {
        "english": "novel", "level": "高中", "phonetic": "/novel/",
        "senses": [{"pos": "noun", "definition_zh": "小说", "example_en": "She reads a novel.",
                    "example_cn": "她读一本小说。", "example_form": ""}],
    }
    record = {"word_key": "novel", "source_hash": "checkpoint-source"}
    saved_words, candidates, provider_calls, generation_calls = {}, {}, [], []

    @contextmanager
    def lock(**kwargs):
        yield True

    def append(rows):
        new = [row for row in rows if row["english"] not in saved_words]
        saved_words.update({row["english"]: row for row in new})
        return len(new), [row["english"] for row in rows if row not in new]

    def provider(*args, **kwargs):
        provider_calls.append("charged")
        return web._DeepSeekResponse(json.dumps([entry]))

    def generate(words, *, level="", include_gaokao_candidate=False):
        generation_calls.append((words, include_gaokao_candidate))
        return json.loads(web._deepseek_chat([{"role": "user", "content": "novel import"}]))

    def interrupted_persistence(records, errors):
        assert "novel" in saved_words
        raise SimulatedCrash()

    monkeypatch.setattr(web, "_read_troubles_unlocked", lambda: {"mappings": {}, "difficult": {}})
    monkeypatch.setattr(web, "_wordbank_lemma_spacy_enabled", lambda: False)
    monkeypatch.setattr(web, "_vocab_import_spacy_accepts_surface", lambda *args: True)
    monkeypatch.setattr(web, "_normalize_import_english_surface", lambda value: value)
    monkeypatch.setattr(web, "get_deepseek_api_key", lambda: "test-only-key")
    monkeypatch.setattr(web, "_deepseek_chat_request", provider)
    monkeypatch.setattr(web, "deepseek_generate_word_entries_v2", generate)
    monkeypatch.setattr(web, "accumulate_valid_deepseek_v2_entries", lambda entries, **kwargs: (entries, {"novel"}))
    monkeypatch.setattr(web, "finalize_combined_gaokao_candidates", lambda *args: ({"novel": record}, {}))
    monkeypatch.setattr(web, "invalidate_merge_wordbank_rows_cache", lambda: None)
    monkeypatch.setattr(web.wordbank_v2, "invalidate_words_v2_cache", lambda: None)
    monkeypatch.setattr(web.wordbank_v2, "get_v2_english_key_set", lambda: set(saved_words))
    monkeypatch.setattr(web.wordbank_v2, "append_words_v2_entries", append)
    monkeypatch.setattr(web.gaokao_backfill, "generation_job_lock", lock)
    monkeypatch.setattr(web.gaokao_questions, "externally_authored_words", lambda words: set())
    monkeypatch.setattr(web.gaokao_questions, "has_complete_questions", lambda *args, **kwargs: False)
    monkeypatch.setattr(web.gaokao_questions, "has_pending_candidate", lambda key, **kwargs: key in candidates)
    monkeypatch.setattr(web.gaokao_questions, "persist_candidate_pool_result", interrupted_persistence)

    with pytest.raises(SimulatedCrash):
        web._execute_vocab_import_job("alice", payload, context=(store, first))
    assert saved_words == {"novel": entry}
    assert store.checkpoints(first["job_id"], first["attempts"])[0]["records"] == {"novel": record}
    stale = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(timespec="seconds")
    with store._connect() as connection:
        connection.execute("UPDATE import_jobs SET updated_at=? WHERE id=?", (stale, first["job_id"]))
    second = store.claim_next()
    monkeypatch.setattr(web.gaokao_questions, "persist_candidate_pool_result",
                        lambda records, errors: candidates.update(records))
    result = web._execute_vocab_import_job("alice", payload, context=(ImportJobStore(store.path), second))
    assert candidates == {"novel": record}
    assert result["new_in_csv"] == 1
    assert result["gaokao_questions"]["generated_words"] == ["novel"]
    assert generation_calls == [(["novel"], True)]
    assert provider_calls == ["charged"]


def test_restored_candidates_do_not_overwrite_externally_authored_questions(tmp_path, monkeypatch):
    store = ImportJobStore(tmp_path / "jobs.sqlite3")
    store.enqueue("vocab_import", "alice", {})
    job = store.claim_next()
    store.checkpoint(job["job_id"], job["attempts"], "batch", {
        "rows": [{"english": "novel"}],
        "records": {"novel": {"word_key": "novel", "source_hash": "old"}},
        "errors": {"novel": "old generation failure"},
    })

    @contextmanager
    def lock(**kwargs):
        yield True

    monkeypatch.setattr(web.gaokao_backfill, "generation_job_lock", lock)
    monkeypatch.setattr(web.wordbank_v2, "append_words_v2_entries", lambda rows: (0, ["novel"]))
    monkeypatch.setattr(web.wordbank_v2, "invalidate_words_v2_cache", lambda: None)
    monkeypatch.setattr(web, "invalidate_merge_wordbank_rows_cache", lambda: None)
    monkeypatch.setattr(web.gaokao_questions, "externally_authored_words", lambda words: {"novel"})
    monkeypatch.setattr(web.gaokao_questions, "persist_candidate_pool_result",
                        lambda *args: pytest.fail("external questions must remain protected"))
    with web.app.test_request_context("/"):
        web.g.import_job_context = (store, job)
        rows, records, errors = web._restore_import_batches()
    assert rows == [{"english": "novel"}]
    assert records == errors == {}

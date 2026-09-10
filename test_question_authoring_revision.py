import copy
from datetime import timedelta

import pytest

import gaokao_questions as questions
from question_authoring import AuthoringError
from test_question_authoring import authoring, NOW, _claim, _generated, _raw, _submit, _verdict


def _seed(service, namespace, value):
    with service._bank(["benefit"]) as bank:
        bank[namespace]["benefit"] = copy.deepcopy(value)


def test_revision_archives_raw_and_restarts_independent_audits(authoring):
    service, sources = authoring
    _generated(service)
    recognition = _claim(service, "recognition_blind", "recognizer")
    _submit(service, recognition, _verdict(recognition))
    original = questions._read_bank_keys_unlocked(["benefit"])["candidates"]["benefit"]
    revision = _claim(service, "revision", "editor")
    item = revision["items"][0]
    assert item["mode"] == "repair"
    assert item["previous_records"]["candidates"] == original
    assert _claim(service, "context_blind", "context")["items"] == []
    assert questions.externally_authored_words(["benefit"]) == {"benefit"}
    receipt = _submit(service, revision, _raw())
    assert receipt["items"][0]["status"] == "awaiting_recognition_blind"
    assert _submit(service, revision, _raw()) == receipt
    modified = _raw()
    modified["context_explanation_zh"] += "不同内容。"
    with pytest.raises(AuthoringError, match="different content"):
        _submit(service, revision, modified)
    assert service.get_job(revision["job_id"], "editor", now=NOW)["items"][0]["previous_records"]["candidates"] == original
    for worker in ("generator", "editor"):
        assert _claim(service, "recognition_blind", worker)["items"] == []
    assert _claim(service, "context_blind", "new-context")["items"] == []
    for kind, worker in (("recognition_blind", "new-recognizer"), ("context_blind", "new-context2"), ("feedback", "final")):
        job = _claim(service, kind, worker)
        result = _submit(service, job, _verdict(job))
    assert result["items"][0]["status"] == "published"
    assert _claim(service, "revision", "another-editor")["items"] == []


@pytest.mark.parametrize("mutation", ["source", "record", "published"])
def test_revision_refuses_concurrent_changes(authoring, mutation):
    service, sources = authoring
    old = {"last_error": "timeout", "attempts": 3}
    _seed(service, "failures", old)
    job = _claim(service, "revision", "editor")
    assert job["items"][0]["mode"] == "generate"
    if mutation == "source":
        sources["benefit"]["chinese"] = "变化"
    elif mutation == "record":
        _seed(service, "failures", {"last_error": "changed"})
    else:
        _seed(service, "questions", {"published": True})
    before = questions._read_bank_keys_unlocked(["benefit"])
    with pytest.raises(AuthoringError, match="changed"):
        _submit(service, job, _raw())
    assert questions._read_bank_keys_unlocked(["benefit"]) == before


def test_revision_failure_and_rejection_preservation_and_leases(authoring):
    service, _ = authoring
    failure = {"last_error": "timeout", "attempts": 2}
    rejection = {"raw_output": _raw(), "last_error": "ambiguous", "audits": {"old": False}}
    _seed(service, "failures", failure)
    _seed(service, "rejections", rejection)
    assert _claim(service)["items"] == []
    job = _claim(service, "revision", "editor")
    assert job["items"][0]["mode"] == "repair"
    assert _claim(service, "revision", "competitor")["items"] == []
    assert _claim(service, "revision", "editor") == job
    service.release(job["job_id"], {"worker_id": "editor"}, now=NOW)
    with pytest.raises(AuthoringError, match="no longer active"):
        _submit(service, job, _raw())
    fresh = _claim(service, "revision", "editor2")
    _submit(service, fresh, _raw())
    archived = service.get_job(job["job_id"], "editor", now=NOW)["items"][0]["previous_records"]
    assert archived == {"failures": failure, "rejections": rejection}
    bank = questions._read_bank_keys_unlocked(["benefit"])
    assert not bank["failures"] and not bank["rejections"]
    assert bank["candidates"]["benefit"]["pool"]["external_authoring"]["audits"] == {}


def test_revision_lease_expiry_and_stale_review_upload(authoring):
    service, _ = authoring
    _generated(service)
    review = _claim(service, "recognition_blind", "recognizer", ttl_seconds=60)
    assert _claim(service, "revision", "editor")["items"] == []
    later = NOW + timedelta(seconds=61)
    revision = service.claim({"kind": "revision", "worker_id": "editor", "request_id": "after-expiry"}, now=later)
    with pytest.raises(AuthoringError):
        _submit(service, review, _verdict(review), now=later)
    _submit(service, revision, _raw(), now=later)


def test_revision_recovers_after_receipt_write_failure(authoring, monkeypatch):
    from question_authoring_store import QuestionAuthoringStore

    service, _ = authoring
    _seed(service, "failures", {"last_error": "timeout"})
    job = _claim(service, "revision", "editor")
    original = QuestionAuthoringStore.save_submission

    def fail(*args, **kwargs):
        raise OSError("receipt disk failure")

    monkeypatch.setattr(QuestionAuthoringStore, "save_submission", fail)
    with pytest.raises(OSError, match="receipt disk failure"):
        _submit(service, job, _raw())
    assert _claim(service, "revision", "editor") == job
    monkeypatch.setattr(QuestionAuthoringStore, "save_submission", original)
    assert _submit(service, job, _raw())["items"][0]["status"] == "awaiting_recognition_blind"


def test_words_filter_targets_and_has_normalized_idempotency(authoring):
    service, sources = authoring
    sources["other"] = {**sources["benefit"], "english": "other"}
    result = service.pending({"worker_id": "editor", "words": '[" BENEFIT "]'}, now=NOW)
    assert [item["source"]["english"] for item in result["items"]] == ["benefit"]
    job = _claim(service, words=[" BENEFIT "])
    assert _claim(service, words=["benefit"]) == job
    with pytest.raises(AuthoringError, match="different parameters"):
        _claim(service, words=["other"])
    assert _claim(service, request_id="unknown-word", words=["missing"])["items"] == []


@pytest.mark.parametrize("words", [[], None, {}, [1], [""], ["  "], ["a\n"], ["x" * 129],
                                   ["benefit", " BENEFIT "], [str(i) for i in range(11)], '["benefit"]'])
def test_claim_words_filter_strict_validation(authoring, words):
    service, _ = authoring
    with pytest.raises(AuthoringError, match="words"):
        _claim(service, words=words)


def test_filtered_context_response_remains_blind(authoring):
    service, _ = authoring
    _generated(service)
    recognition = _claim(service, "recognition_blind", "recognizer", words=["benefit"])
    _submit(service, recognition, _verdict(recognition))
    pending = service.pending({"worker_id": "context", "kind": "context_blind", "words": '["benefit"]'}, now=NOW)
    assert set(pending["items"][0]) == {"prompt", "options"}
    context = _claim(service, "context_blind", "context", words=["benefit"])
    assert set(context["items"][0]) == {"item_id", "prompt", "options"}
    assert "words" not in context and "request_parameters" not in context

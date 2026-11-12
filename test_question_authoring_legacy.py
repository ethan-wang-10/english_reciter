import copy
import sqlite3
from datetime import timedelta

import pytest

import gaokao_questions as questions
from question_authoring import AuthoringError
from test_question_authoring import NOW, _claim, _generated, _raw, _submit, _verdict, authoring


def _legacy(service, sources):
    record, error = questions.finalize_generated_questions(sources["benefit"], _raw())
    assert not error
    record["generation_prompt_version"] = 3
    record["quality_gate"] = "generation-prompt-self-check-v3"
    record.pop("audit_version", None)
    record.pop("recognition_format_version", None)
    with service._bank(["benefit"]) as bank:
        bank["questions"]["benefit"] = record
    return copy.deepcopy(record)


def _payload():
    with sqlite3.connect(questions.QUESTION_BANK_FILE.with_suffix(".sqlite3")) as db:
        return db.execute("SELECT payload FROM records WHERE namespace='questions' AND word_key='benefit'").fetchone()[0]


def _legacy_claim(service, **kwargs):
    return _claim(service, "legacy_revision", "legacy-author", words=["benefit"], **kwargs)


def _blind(service, stage):
    job = _claim(service, stage, stage + "-reviewer")
    _submit(service, job, _verdict(job))
    return job


def test_legacy_revision_is_explicit_and_preserves_published_bytes(authoring):
    service, sources = authoring
    legacy = _legacy(service, sources)
    original = _payload()
    assert _claim(service)["items"] == []
    assert _claim(service, "revision")["items"] == []
    with pytest.raises(AuthoringError, match="explicit words"):
        _claim(service, "legacy_revision")
    job = _legacy_claim(service)
    assert job["items"][0]["mode"] == "repair"
    assert job["items"][0]["previous_records"]["questions"] == legacy
    assert job["items"][0]["retained_manuscript"]["context_sentence"] == _raw()["context_sentence"]
    assert _payload() == original
    assert questions.plan_generated_sources(list(sources.values()))["benefit"]["action"] == "external"
    assert questions.generate_audited_and_persist(
        list(sources.values()), lambda *args: pytest.fail("a legacy reservation must block DeepSeek before upload"),
        force=True, retry_failed=True,
    )["pending"] == 0
    assert _legacy_claim(service) == job
    service.release(job["job_id"], {"worker_id": job["worker_id"]}, now=NOW)
    assert _payload() == original
    assert service.get_job(job["job_id"], job["worker_id"], now=NOW)["items"][0]["previous_records"]["questions"] == legacy


def test_legacy_upgrade_requires_all_independent_audits_and_archives_old_record(authoring):
    service, sources = authoring
    legacy = _legacy(service, sources)
    original = _payload()
    generation = _legacy_claim(service)
    _submit(service, generation, _raw())
    assert _payload() == original
    assert _legacy_claim(service, request_id="another-legacy")["items"] == []
    assert _claim(service, "recognition_blind", "legacy-author")["items"] == []
    assert questions.generate_audited_and_persist(
        list(sources.values()), lambda *args: pytest.fail("legacy upgrade must not call DeepSeek"),
        force=True, retry_failed=True,
    )["pending"] == 0
    _blind(service, "recognition_blind")
    assert _payload() == original
    _blind(service, "context_blind")
    assert _payload() == original
    feedback = _claim(service, "feedback", "final-reviewer")
    receipt = _submit(service, feedback, _verdict(feedback))
    assert receipt["items"][0]["status"] == "published"
    assert questions.get_question("benefit", "context", source=sources["benefit"])
    assert service.get_job(generation["job_id"], generation["worker_id"], now=NOW)["items"][0]["previous_records"]["questions"] == legacy
    assert _submit(service, feedback, _verdict(feedback), now=NOW + timedelta(days=2)) == receipt
    assert _legacy_claim(service, request_id="protected")["items"] == []


@pytest.mark.parametrize("stage", ["recognition_blind", "context_blind", "feedback"])
def test_legacy_audit_rejects_concurrent_published_edits_without_overwriting(authoring, stage):
    service, sources = authoring
    _legacy(service, sources)
    _submit(service, _legacy_claim(service), _raw())
    if stage != "recognition_blind":
        _blind(service, "recognition_blind")
    if stage == "feedback":
        _blind(service, "context_blind")
    job = _claim(service, stage, "reviewer")
    with service._bank(["benefit"]) as bank:
        bank["questions"]["benefit"]["context"]["explanation_zh"] = "另一位编辑更新的解析。"
    before = copy.deepcopy(questions.load_bank())
    with pytest.raises(AuthoringError, match="legacy question changed") as raised:
        _submit(service, job, _verdict(job))
    assert raised.value.status == 409
    assert questions.load_bank() == before


def test_legacy_source_drift_rejects_upload_and_preserves_published(authoring):
    service, sources = authoring
    _legacy(service, sources)
    job = _legacy_claim(service)
    original = _payload()
    sources["benefit"] = {**sources["benefit"], "chinese": "n. 救济金"}
    with pytest.raises(AuthoringError, match="source changed"):
        _submit(service, job, _raw())
    assert _payload() == original


@pytest.mark.parametrize("stage", ["recognition_blind", "context_blind", "feedback"])
def test_legacy_rejection_retains_published_and_can_be_repaired_again(authoring, stage):
    service, sources = authoring
    legacy = _legacy(service, sources)
    original = _payload()
    _submit(service, _legacy_claim(service), _raw())
    if stage != "recognition_blind":
        _blind(service, "recognition_blind")
    if stage == "feedback":
        _blind(service, "context_blind")
    job = _claim(service, stage, "rejecting-reviewer")
    verdict = _verdict(job)
    if stage == "feedback":
        verdict["feedback_quality"]["translation_correct"] = False
    else:
        field = "recognition_valid_definition" if stage == "recognition_blind" else "context_meaning_fits"
        verdict[field] = [False] * len(job["items"][0]["options"])
    assert _submit(service, job, verdict)["items"][0]["status"] == "rejected"
    assert _payload() == original
    retry = _legacy_claim(service, request_id="legacy-repair")
    assert retry["items"][0]["previous_records"]["questions"] == legacy
    assert retry["items"][0]["previous_records"]["rejections"]["pool"]["raw"] == _raw()


def test_withdrawn_legacy_record_is_not_resurrected(authoring):
    service, sources = authoring
    _legacy(service, sources)
    with service._bank(["benefit"]) as bank:
        bank["questions"]["benefit"]["withdrawn_at"] = NOW.isoformat()
    original = _payload()
    assert _legacy_claim(service)["items"] == []
    assert _payload() == original


def test_normal_candidate_cannot_replace_a_concurrently_published_record(authoring):
    service, sources = authoring
    _generated(service)
    job = _claim(service, "recognition_blind", "reviewer")
    _legacy(service, sources)
    before = copy.deepcopy(questions.load_bank())
    with pytest.raises(AuthoringError, match="published question exists"):
        _submit(service, job, _verdict(job))
    assert questions.load_bank() == before


def test_generated_legacy_draft_survives_release_and_continues_independent_audits(authoring):
    service, sources = authoring
    _legacy(service, sources)
    original = _payload()
    job = _legacy_claim(service)
    _submit(service, job, _raw())
    service.release(job["job_id"], {"worker_id": job["worker_id"]}, now=NOW)
    assert questions.load_bank()["candidates"]["benefit"]["pool"]["raw"] == _raw()
    assert _payload() == original
    assert _claim(service, "recognition_blind", "reviewer")["items"]


def test_stale_legacy_candidate_can_be_repaired_after_original_is_edited(authoring):
    service, sources = authoring
    _legacy(service, sources)
    _submit(service, _legacy_claim(service), _raw())
    with service._bank(["benefit"]) as bank:
        bank["questions"]["benefit"]["context"]["explanation_zh"] = "编辑修正了原来的解释。"
    original = _payload()
    assert _claim(service, "recognition_blind", "reviewer")["items"] == []
    new = _legacy_claim(service, request_id="repair-stale-snapshot")
    assert new["items"]
    assert new["items"][0]["previous_records"]["candidates"]["pool"]["raw"] == _raw()
    assert _payload() == original


def test_current_approved_record_is_protected_until_its_source_changes(authoring):
    service, sources = authoring
    _generated(service)
    _blind(service, "recognition_blind")
    _blind(service, "context_blind")
    job = _claim(service, "feedback", "final-reviewer")
    _submit(service, job, _verdict(job))
    original = _payload()
    assert _legacy_claim(service)["items"] == []
    sources["benefit"] = {**sources["benefit"], "chinese": "n. 救济金"}
    changed = _legacy_claim(service, request_id="repair-changed-source")
    assert changed["items"]
    assert _payload() == original


def test_expired_unsubmitted_legacy_claim_can_be_reclaimed_without_loss(authoring):
    service, sources = authoring
    legacy = _legacy(service, sources)
    original = _payload()
    expired = _legacy_claim(service, ttl_seconds=60)
    replacement = service.claim({
        "kind": "legacy_revision", "words": ["benefit"], "worker_id": "replacement",
        "request_id": "expired-replacement", "limit": 1,
    }, now=NOW + timedelta(seconds=61))
    assert replacement["items"]
    assert replacement["items"][0]["previous_records"]["questions"] == legacy
    assert _payload() == original
    with pytest.raises(AuthoringError):
        _submit(service, expired, _raw(), now=NOW + timedelta(seconds=61))
    assert _payload() == original

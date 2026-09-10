import copy
from datetime import datetime, timedelta, timezone

import pytest

import gaokao_backfill
import gaokao_questions as questions
from question_authoring import AuthoringError, QuestionAuthoringService
from question_authoring_store import QuestionAuthoringStore


NOW = datetime(2026, 9, 9, 1, 0, tzinfo=timezone.utc)


@pytest.fixture
def authoring(monkeypatch, tmp_path):
    monkeypatch.setattr(questions, "QUESTION_BANK_FILE", tmp_path / "questions.json")
    monkeypatch.setattr(questions, "QUESTION_BANK_LOCK_FILE", tmp_path / ".bank.lock")
    monkeypatch.setattr(questions, "_cache", None)
    monkeypatch.setattr(gaokao_backfill, "GENERATION_LOCK_FILE", tmp_path / ".jobs.lock")
    source = questions.source_from_wordbank_row({
        "english": "benefit", "chinese": "n. 益处", "level": "高中", "phonetic": "/benefit/",
        "example1": "Daily exercise has a clear benefit for physical health.",
        "example1_cn": "每天锻炼对身体健康有明显好处。",
    })
    sources = {"benefit": source}
    service = QuestionAuthoringService(lambda level: list(sources.values()), sources.get)
    return service, sources


def _raw():
    return {
        "english": "benefit",
        "recognition_distractors": ["负担", "风险", "障碍", "损失", "争论", "限制"],
        "recognition_explanation_zh": "benefit 作名词时表示益处或好处。",
        "context_sentence": "One clear benefit of the new filter is that families can now drink clean water without spending money on bottled water every day.",
        "context_translation_zh": "新过滤器的一个明显好处是，家家户户现在每天都能喝到干净的水，不必花钱购买瓶装水。",
        "context_distractors": ["burden", "drawback", "risk", "expense", "restriction", "obstacle"],
        "context_explanation_zh": "后半句说明水质改善且节省开支，限定所述是好处。",
    }


def _claim(service, kind="generation", worker="generator", request_id=None, **kwargs):
    return service.claim({
        "kind": kind, "worker_id": worker, "request_id": request_id or f"claim-{kind}-{worker}",
        "limit": 1, **kwargs,
    }, now=NOW)


def _submit(service, job, result, submission_id=None, now=NOW):
    return service.submit(job["job_id"], {
        "worker_id": job["worker_id"], "submission_id": submission_id or f"upload-{job['kind']}",
        "items": [{"item_id": job["items"][0]["item_id"], "result": result}],
    }, now=now)


def _verdict(job):
    task = job["items"][0]
    if job["kind"] == "recognition_blind":
        return {
            "recognition_valid_definition": [row["text"] == "益处" for row in task["options"]],
            "recognition_parallel_form": [True] * len(task["options"]),
        }
    if job["kind"] == "context_blind":
        return {
            "context_grammatical": [True] * len(task["options"]),
            "context_meaning_fits": [row["text"] == "benefit" for row in task["options"]],
            "context_quality": {"natural": True, "decisive_clues": True, "answer_revealed": False, "reason_zh": "句子给出了明确的正面结果。"},
        }
    return {"feedback_quality": {
        "recognition_explanation_correct": True, "recognition_options_parallel": True,
        "translation_correct": True, "context_explanation_correct": True,
        "answer_matches_headword": True, "reason_zh": "译文和解析均与题目及正确答案一致。",
    }}


def _generated(service):
    job = _claim(service)
    _submit(service, job, _raw())
    return job


def test_complete_external_workflow_publishes_without_deepseek(authoring):
    service, sources = authoring
    initial = service.pending({"worker_id": "generator"}, now=NOW)
    assert initial["items"][0]["source"]["english"] == "benefit"
    generated = _generated(service)
    assert questions.get_question("benefit", "context") is None
    assert _claim(service, request_id="second-generation")["items"] == []
    assert questions.plan_generated_sources(list(sources.values()))["benefit"]["action"] == "external"
    assert questions.pending_candidate_pools() == {}
    assert questions.automatic_retry_queue(NOW + timedelta(days=2)) == {}
    result = questions.generate_audited_and_persist(
        list(sources.values()), lambda *args: pytest.fail("external items must not invoke DeepSeek"),
        force=True, retry_failed=True,
    )
    assert result["pending"] == 0
    recognition = _claim(service, "recognition_blind", "recognition-reviewer")
    _submit(service, recognition, _verdict(recognition))
    assert _claim(service, "context_blind", "recognition-reviewer")["items"] == []
    context = _claim(service, "context_blind", "context-reviewer")
    assert set(context["items"][0]) == {"item_id", "prompt", "options"}
    assert not any(key in context["items"][0] for key in ("headword", "source", "english", "answer_option_id"))
    _submit(service, context, _verdict(context))
    feedback = _claim(service, "feedback", "feedback-reviewer")
    assert len(feedback["items"][0]["context"]["options"]) == 4
    result = _submit(service, feedback, _verdict(feedback))
    assert result["items"][0]["status"] == "published"
    published = questions.get_question("benefit", "context", source=sources["benefit"])
    assert published
    assert published["translation_zh"] == _raw()["context_translation_zh"]
    assert questions.load_bank()["questions"]["benefit"]["audit_provider"] == "external"
    assert service.get_job(generated["job_id"], "generator", now=NOW)["status"] == "completed"


def test_generation_receipt_is_idempotent_and_refuses_changed_content(authoring):
    service, _ = authoring
    job = _claim(service)
    result = _submit(service, job, _raw())
    revision = questions._question_store().revision()
    assert _submit(service, job, _raw(), now=NOW + timedelta(days=2)) == result
    assert questions._question_store().revision() == revision
    with pytest.raises(AuthoringError) as raised:
        _submit(service, job, {**_raw(), "context_translation_zh": "改写的译文。"})
    assert raised.value.status == 409


def test_claim_replay_keeps_snapshot_and_detects_different_parameters(authoring):
    service, _ = authoring
    first = _claim(service)
    assert _claim(service) == first
    assert _claim(service, request_id="another", worker="another")["items"] == []
    with pytest.raises(AuthoringError) as raised:
        _claim(service, limit=2)
    assert raised.value.status == 409


@pytest.mark.parametrize("malformed", [False, True])
def test_upload_recovers_after_bank_commit_before_receipt(authoring, monkeypatch, malformed):
    service, _ = authoring
    job = _claim(service)
    raw = _raw()
    if malformed:
        raw["context_sentence"] = "A benefit is clear."
    save = QuestionAuthoringStore.save_submission
    monkeypatch.setattr(QuestionAuthoringStore, "save_submission", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("interrupted")))
    with pytest.raises(OSError):
        _submit(service, job, raw)
    stored_raw = copy.deepcopy(questions.load_bank()["candidates"]["benefit"]["pool"]["raw"])
    assert stored_raw == raw
    assert _claim(service)["job_id"] == job["job_id"]
    monkeypatch.setattr(QuestionAuthoringStore, "save_submission", save)
    result = _submit(service, job, raw, now=NOW + timedelta(days=1))
    assert result["items"][0]["status"] == ("manual" if malformed else "awaiting_recognition_blind")
    assert service.get_job(job["job_id"], "generator", now=NOW + timedelta(days=1))["status"] == "completed"
    assert questions.load_bank()["candidates"]["benefit"]["pool"]["raw"] == stored_raw


def test_source_change_rejects_upload_without_replacing_content(authoring):
    service, sources = authoring
    job = _claim(service)
    before = copy.deepcopy(questions.load_bank()["candidates"])
    sources["benefit"] = {**sources["benefit"], "chinese": "n. 救济金"}
    with pytest.raises(AuthoringError) as raised:
        _submit(service, job, _raw())
    assert raised.value.status == 409
    assert questions.load_bank()["candidates"] == before


def test_release_removes_only_empty_reservation(authoring):
    service, _ = authoring
    job = _claim(service)
    service.release(job["job_id"], {"worker_id": "generator"}, now=NOW)
    assert questions.load_bank()["candidates"] == {}
    next_job = _claim(service, request_id="replacement")
    _submit(service, next_job, _raw())
    before = copy.deepcopy(questions.load_bank()["candidates"])
    service.release(next_job["job_id"], {"worker_id": "generator"}, now=NOW)
    assert questions.load_bank()["candidates"] == before


def test_expired_generation_lease_can_be_reclaimed_but_old_upload_is_refused(authoring):
    service, _ = authoring
    job = _claim(service, ttl_seconds=60)
    later = NOW + timedelta(seconds=61)
    replacement = service.claim({"worker_id": "replacement", "request_id": "replacement"}, now=later)
    assert len(replacement["items"]) == 1
    with pytest.raises(AuthoringError) as raised:
        _submit(service, job, _raw(), now=later)
    assert raised.value.status == 409
    assert questions.load_bank()["candidates"]["benefit"]["pool"]["raw"] == {}


def test_independent_audits_require_different_workers_and_prior_stages(authoring):
    service, _ = authoring
    _generated(service)
    assert _claim(service, "recognition_blind", "generator")["items"] == []
    assert _claim(service, "context_blind", "context-reviewer")["items"] == []
    assert _claim(service, "feedback", "feedback-reviewer")["items"] == []
    recognition = _claim(service, "recognition_blind", "recognition-reviewer")
    with pytest.raises(AuthoringError) as raised:
        service.get_job(recognition["job_id"], "other-reviewer", now=NOW)
    assert raised.value.status == 403


def test_semantic_rejection_is_retained_and_never_regenerated(authoring):
    service, sources = authoring
    _generated(service)
    recognition = _claim(service, "recognition_blind", "recognition-reviewer")
    verdict = _verdict(recognition)
    verdict["recognition_valid_definition"] = [True] * len(recognition["items"][0]["options"])
    result = _submit(service, recognition, verdict)
    assert result["items"][0]["status"] == "rejected"
    assert questions.load_bank()["rejections"]["benefit"]["pool"]["raw"] == _raw()
    assert _claim(service, request_id="cannot-regenerate")["items"] == []
    assert questions.sources_needing_prompt_refresh(list(sources.values()), force=True, retry_failed=True) == []


def test_audit_version_change_requires_a_fresh_claim(authoring, monkeypatch):
    service, _ = authoring
    _generated(service)
    recognition = _claim(service, "recognition_blind", "reviewer")
    monkeypatch.setattr(questions, "AUDIT_VERSION", questions.AUDIT_VERSION + 1)
    with pytest.raises(AuthoringError) as raised:
        _submit(service, recognition, _verdict(recognition))
    assert raised.value.status == 409


def test_invalid_verdict_leaves_claim_and_candidate_unchanged(authoring):
    service, _ = authoring
    _generated(service)
    recognition = _claim(service, "recognition_blind", "reviewer")
    revision = questions._question_store().revision()
    with pytest.raises(AuthoringError) as raised:
        _submit(service, recognition, {"recognition_valid_definition": [True]})
    assert raised.value.status == 422
    assert questions._question_store().revision() == revision
    assert service.get_job(recognition["job_id"], "reviewer", now=NOW)["status"] == "active"


def test_unbounded_numeric_query_is_a_validation_error(authoring):
    service, _ = authoring
    with pytest.raises(AuthoringError) as raised:
        service.pending({"worker_id": "generator", "limit": "9" * 5000}, now=NOW)
    assert raised.value.status == 400

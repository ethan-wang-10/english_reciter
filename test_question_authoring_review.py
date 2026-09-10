import copy
import hashlib
from datetime import timedelta

import pytest

import gaokao_questions as questions
from question_authoring import AuthoringError
from test_question_authoring import NOW, _claim, _generated, _raw, _submit, _verdict, authoring


def _approved_blind_stages(service):
    _generated(service)
    recognition = _claim(service, "recognition_blind", "recognition-reviewer")
    _submit(service, recognition, _verdict(recognition))
    context = _claim(service, "context_blind", "context-reviewer")
    _submit(service, context, _verdict(context))


def _change_recognition_instructions(monkeypatch):
    original = questions._blind_audit_spec

    def revised(kind):
        fields, instructions = original(kind)
        return fields, instructions + ("\nRecheck every definition independently." if kind == "recognition" else "")

    monkeypatch.setattr(questions, "_blind_audit_spec", revised)


def test_recognition_refresh_cannot_reuse_a_context_verdict_by_the_same_reviewer(authoring, monkeypatch):
    service, _ = authoring
    _approved_blind_stages(service)
    _change_recognition_instructions(monkeypatch)
    recognition = _claim(service, "recognition_blind", "context-reviewer", request_id="updated-recognition")
    if recognition["items"]:
        _submit(service, recognition, _verdict(recognition), submission_id="updated-recognition")
        feedback = _claim(service, "feedback", "feedback-reviewer", request_id="feedback-after-refresh")
        assert feedback["items"] == [], "both blind verdicts now belong to context-reviewer"


def test_feedback_claim_becomes_conflict_when_prior_instructions_change(authoring, monkeypatch):
    service, _ = authoring
    _approved_blind_stages(service)
    feedback = _claim(service, "feedback", "feedback-reviewer")
    before = copy.deepcopy(questions.load_bank())
    _change_recognition_instructions(monkeypatch)
    with pytest.raises(AuthoringError) as raised:
        _submit(service, feedback, _verdict(feedback))
    assert raised.value.status == 409
    assert questions.load_bank() == before


def test_context_claim_cannot_submit_after_rubric_clarification(authoring, monkeypatch):
    service, _ = authoring
    _generated(service)
    recognition = _claim(service, "recognition_blind", "recognition-reviewer")
    _submit(service, recognition, _verdict(recognition))
    context = _claim(service, "context_blind", "context-reviewer")
    before = copy.deepcopy(questions.load_bank())
    original = questions._blind_audit_spec

    def clarified(kind):
        fields, instructions = original(kind)
        return fields, instructions + ("\nRecheck contextual evidence under the clarified rubric." if kind == "context" else "")

    monkeypatch.setattr(questions, "_blind_audit_spec", clarified)
    with pytest.raises(AuthoringError, match="audit instructions or inputs changed") as raised:
        _submit(service, context, _verdict(context))
    assert raised.value.status == 409
    assert questions.load_bank() == before


def test_multiple_item_generation_validation_is_atomic(authoring):
    service, sources = authoring
    sources["bonus"] = questions.source_from_wordbank_row({
        "english": "bonus", "chinese": "n. 奖金", "level": "高中",
        "example1": "The employee received a bonus for her excellent work.",
        "example1_cn": "这名员工因工作出色而获得奖金。",
    })
    job = _claim(service, limit=2)
    before = copy.deepcopy(questions.load_bank())
    results = []
    for item in job["items"]:
        raw = _raw()
        key = item["source"]["english"]
        raw["english"] = key
        raw["context_sentence"] = raw["context_sentence"].replace("benefit", key)
        results.append({"item_id": item["item_id"], "result": raw})
    results[-1]["result"].pop("context_translation_zh")
    with pytest.raises(AuthoringError) as raised:
        service.submit(job["job_id"], {
            "worker_id": job["worker_id"], "submission_id": "atomic", "items": results,
        }, now=NOW)
    assert raised.value.status == 422
    assert questions.load_bank() == before
    assert service.get_job(job["job_id"], job["worker_id"], now=NOW)["status"] == "active"


def test_new_submission_id_cannot_overwrite_completed_generation(authoring):
    service, _ = authoring
    job = _generated(service)
    before = copy.deepcopy(questions.load_bank())
    with pytest.raises(AuthoringError) as raised:
        _submit(service, job, {**_raw(), "context_translation_zh": "不同的译文。"}, submission_id="different")
    assert raised.value.status == 409
    assert questions.load_bank() == before


def test_expired_job_release_does_not_remove_reclaimed_source(authoring):
    service, _ = authoring
    original = _claim(service, ttl_seconds=60)
    later = NOW + timedelta(seconds=61)
    replacement = service.claim({"worker_id": "replacement", "request_id": "replacement"}, now=later)
    before = copy.deepcopy(questions.load_bank())
    service.release(original["job_id"], {"worker_id": original["worker_id"]}, now=later)
    assert questions.load_bank() == before
    assert service.get_job(replacement["job_id"], "replacement", now=later)["status"] == "active"


def test_context_option_order_does_not_encode_hidden_headword(authoring):
    service, _ = authoring
    _generated(service)
    recognition = _claim(service, "recognition_blind", "recognition-reviewer")
    _submit(service, recognition, _verdict(recognition))
    context = _claim(service, "context_blind", "context-reviewer")
    visible = [row["text"] for row in context["items"][0]["options"]]
    recovered = [
        candidate for candidate in visible
        if sorted(visible, key=lambda text: hashlib.sha256(
            f"{candidate}:pool:context\0{text}".encode("utf-8")
        ).hexdigest()) == visible
    ]
    assert recovered != ["benefit"], "public ordering reveals the answer by testing each visible option as the hash seed"


def test_external_context_order_stays_the_same_when_only_hidden_answer_changes(authoring):
    service, _ = authoring
    _generated(service)
    pool = copy.deepcopy(questions.load_bank()["candidates"]["benefit"]["pool"])
    original = questions._candidate_pool_audit_questions(pool)[0]["context"]
    changed = copy.deepcopy(pool)
    changed["source"].update(english="burden", context_answer="burden")
    changed["raw"]["context_distractors"] = [
        "benefit" if option == "burden" else option for option in changed["raw"]["context_distractors"]
    ]
    alternative = questions._candidate_pool_audit_questions(changed)[0]["context"]
    assert original["options"] == alternative["options"]
    assert original["answer_option_id"] != alternative["answer_option_id"]


@pytest.mark.parametrize("generated", [False, True])
def test_combined_import_cannot_overwrite_external_reservation_or_generated_content(authoring, generated):
    service, sources = authoring
    if generated:
        _generated(service)
    else:
        _claim(service)
    before = copy.deepcopy(questions.load_bank())
    imported_pool, error = questions.build_generation_candidate_pool(sources["benefit"], _raw())
    assert not error
    questions.persist_candidate_pool_result({"benefit": imported_pool}, {})
    assert questions.load_bank() == before


@pytest.mark.parametrize("namespace", ["questions", "candidates", "rejections", "failures"])
@pytest.mark.parametrize("nested", [False, True])
def test_external_ownership_in_any_namespace_blocks_import_records_and_errors(authoring, namespace, nested):
    service, sources = authoring
    marker = {"audit_provider": "external", "sentinel": namespace}
    value = {"pool": marker} if nested else marker
    with service._bank(["benefit"]) as bank:
        bank[namespace]["benefit"] = value
    assert questions.externally_authored_words(["  BENEFIT  ", "novel"]) == {"benefit"}
    before = copy.deepcopy(questions.load_bank())
    imported_pool, error = questions.build_generation_candidate_pool(sources["benefit"], _raw())
    assert not error
    questions.persist_candidate_pool_result(
        {"benefit": imported_pool}, {"benefit": "import error", "novel": "ordinary import error"},
    )
    after = questions.load_bank()
    for name in ("questions", "candidates", "rejections", "failures"):
        assert after[name].get("benefit") == before[name].get("benefit")
    assert after["failures"]["novel"]["last_error"] == "ordinary import error"

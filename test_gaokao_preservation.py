import copy
import json

import pytest

import gaokao_questions as questions
from test_gaokao_questions import _blind_reply, _generated, _source


@pytest.fixture
def private_question_bank(monkeypatch, tmp_path):
    monkeypatch.setattr(questions, "QUESTION_BANK_FILE", tmp_path / "questions.json")
    monkeypatch.setattr(questions, "QUESTION_BANK_LOCK_FILE", tmp_path / ".questions.lock")
    monkeypatch.setattr(questions, "_cache", None)
    monkeypatch.setattr(questions, "_cache_mtime_ns", -1)


def _record(source):
    record, error = questions.finalize_generated_questions(source, _generated("benefit"))
    assert not error
    return questions._mark_independently_audited(record)


def _seed(namespace, source, *, incomplete=False, outdated=False):
    bank = questions.empty_bank()
    record = _record(source)
    pool, error = questions.build_generation_candidate_pool(source, _generated("benefit"))
    assert not error
    if outdated:
        record["generation_prompt_version"] = -1
        record["audit_version"] = -1
        record["quality_gate"] = "independent-semantic-audit-legacy"
        pool["generation_prompt_version"] = -1
    if incomplete:
        record.pop("context", None)
        pool["raw"].pop("context_sentence", None)
    if namespace == "questions":
        bank[namespace]["benefit"] = record
    else:
        pool["record"] = record
        bank[namespace]["benefit"] = {
            "record": record,
            "pool": pool,
            "last_error": "previous candidate needs review",
            "audit_version": -1 if outdated else questions.AUDIT_VERSION,
        }
    questions._write_bank_unlocked(bank)
    return copy.deepcopy(bank[namespace]["benefit"])


@pytest.mark.parametrize("namespace", ["questions", "candidates", "rejections"])
@pytest.mark.parametrize("condition", ["current", "outdated", "incomplete", "source_changed"])
@pytest.mark.parametrize("force", [False, True])
def test_existing_generated_material_never_requests_full_generation(
    private_question_bank, namespace, condition, force,
):
    source = _source("benefit", "n. 益处")
    _seed(
        namespace,
        source,
        incomplete=condition == "incomplete",
        outdated=condition == "outdated",
    )
    current_source = dict(source)
    if condition == "source_changed":
        current_source.update(chinese="n. 救济金", source_hash="changed-source")
    assert questions.plan_generated_sources([current_source])["benefit"]["action"] != "generate"
    generation_prompts = []

    def generation_chat(messages, max_tokens):
        generation_prompts.append(messages[-1]["content"])
        return json.dumps([_generated("benefit")])

    questions.generate_audited_and_persist(
        [current_source],
        generation_chat,
        audit_chat=lambda messages, _: _blind_reply(messages),
        refresh_prompt=True,
        force=force,
        audit_identity="preservation-test",
    )

    assert not any("你是高考英语词汇题库编辑" in prompt for prompt in generation_prompts)


def test_phonetic_update_keeps_current_published_questions(private_question_bank):
    row = {
        "english": "benefit",
        "chinese": "n. 益处",
        "level": "高中",
        "phonetic": "/old/",
        "example1": "The benefit of daily practice was clear to everyone in class.",
        "example1_cn": "每天练习的益处对班上的每个人都很明显。",
    }
    original_source = questions.source_from_wordbank_row(row)
    updated_source = questions.source_from_wordbank_row({**row, "phonetic": "/new/"})
    assert original_source and updated_source
    assert original_source["source_hash"] != updated_source["source_hash"]
    assert questions.source_content_hash(original_source) == questions.source_content_hash(updated_source)
    original_record = _seed("questions", original_source)

    def unexpected_request(*args):
        pytest.fail("phonetic changes must not request generation or audit")

    assert questions.sources_needing_prompt_refresh([updated_source]) == []
    result = questions.generate_audited_and_persist(
        [updated_source], unexpected_request, audit_chat=unexpected_request,
        refresh_prompt=True,
    )

    assert result["pending"] == 0
    assert questions.get_question("benefit", "context", source=updated_source) == original_record["context"]
    assert questions.load_bank()["questions"]["benefit"] == original_record


def test_semantic_source_change_holds_old_content_without_regeneration(private_question_bank):
    original_source = _source("benefit", "n. 益处")
    original_record = _seed("questions", original_source)
    updated_source = {**original_source, "chinese": "n. 救济金", "source_hash": "new-source"}
    calls = []

    def unexpected_request(messages, max_tokens):
        calls.append(messages)
        return "[]"

    assert questions.get_question("benefit", "context", source=original_source)
    assert questions.get_question("benefit", "context", source=updated_source) is None
    result = questions.generate_audited_and_persist(
        [updated_source], unexpected_request, audit_chat=unexpected_request,
        refresh_prompt=True, force=True,
    )

    assert calls == []
    assert result["generated"] == 0
    bank = questions.load_bank()
    assert bank["questions"]["benefit"] == original_record
    assert bank["failures"]["benefit"]["manual_review_required"] is True


@pytest.mark.parametrize("evidence", ["raw_draft", "failure_history"])
@pytest.mark.parametrize("force", [False, True])
def test_partial_prior_generation_is_held_without_new_requests(
    private_question_bank, evidence, force,
):
    source = _source("benefit", "n. 益处")
    raw = {"english": "benefit", "recognition_distractors": ["负担"]}
    bank = questions.empty_bank()
    if evidence == "raw_draft":
        bank["candidates"]["benefit"] = {
            "record": {},
            "pool": {"source": source, "raw": raw, "record": {}},
        }
    else:
        bank["failures"]["benefit"] = {
            "attempts": 1,
            "last_error": "previous generation output could not be parsed",
        }
    questions._write_bank_unlocked(bank)
    calls = []

    def unexpected_request(messages, max_tokens):
        calls.append(messages)
        return "[]"

    result = questions.generate_audited_and_persist(
        [source], unexpected_request, audit_chat=unexpected_request,
        refresh_prompt=True, force=force, retry_failed=True,
    )

    assert calls == []
    assert result["generated"] == 0
    bank = questions.load_bank()
    assert bank["failures"]["benefit"]["manual_review_required"] is True
    if evidence == "raw_draft":
        assert bank["candidates"]["benefit"]["pool"]["raw"] == raw


def test_new_word_generates_once_and_subsequent_runs_reuse_it(private_question_bank):
    source = _source("benefit", "n. 益处")
    generation_prompts = []

    def generation_chat(messages, max_tokens):
        generation_prompts.append(messages[-1]["content"])
        return json.dumps([_generated("benefit")])

    assert questions.plan_generated_sources([source])["benefit"]["action"] == "generate"
    first = questions.generate_audited_and_persist(
        [source], generation_chat,
        audit_chat=lambda messages, _: _blind_reply(messages),
        audit_identity="preservation-test",
    )
    assert first["generated"] == 1
    assert len(generation_prompts) == 1
    assert "你是高考英语词汇题库编辑" in generation_prompts[0]
    generation_prompts.clear()

    for force in (False, True):
        questions.generate_audited_and_persist(
            [source], generation_chat,
            audit_chat=lambda messages, _: _blind_reply(messages),
            refresh_prompt=True, force=force,
            audit_identity="preservation-test",
        )

    assert generation_prompts == []
    assert questions.get_question("benefit", "context", source=source)


def test_missing_translation_plans_only_feedback_repair(private_question_bank):
    source = _source("benefit", "n. 益处")
    original_record = _seed("questions", source, outdated=True)
    bank = questions.load_bank()
    bank["questions"]["benefit"]["context"]["translation_zh"] = ""
    questions._write_bank_unlocked(bank)

    plan = questions.plan_generated_sources([source])["benefit"]

    assert plan["action"] == "repair"
    assert plan["repair_fields"] == ["context_translation_zh"]
    assert plan["pool"]["raw"]["context_sentence"] == original_record["context"]["prompt"].replace(
        "____", "benefit",
    )


def test_explicitly_missing_current_source_does_not_serve_stored_question(private_question_bank):
    source = _source("benefit", "n. 益处")
    _seed("questions", source)

    assert questions.get_question("benefit", "context")
    assert questions.get_question("benefit", "context", source=None) is None


def test_source_change_during_audit_prevents_publication(private_question_bank):
    source = _source("benefit", "n. 益处")
    retained = _seed("candidates", source)
    changed = {**source, "chinese": "n. 救济金", "source_hash": "new-source"}
    result = questions.generate_audited_and_persist(
        [source], lambda *args: pytest.fail("retained content cannot be generated again"),
        audit_chat=lambda messages, _: _blind_reply(messages),
        source_lookup=lambda key: changed,
    )
    assert result["generated"] == 0
    assert result["audit_retry_words"] == ["benefit"]
    bank = questions.load_bank()
    assert bank["questions"] == {}
    assert bank["candidates"]["benefit"]["pool"]["raw"] == retained["pool"]["raw"]


def test_rejected_published_question_is_withdrawn_without_erasing_content(private_question_bank):
    source = _source("benefit", "n. 益处")
    original = _seed("questions", source)
    calls = []

    def audit(messages, max_tokens):
        calls.append(messages[-1]["content"])
        rows = json.loads(_blind_reply(messages))
        rows[0]["recognition_valid_definition"] = [False] * 4
        return json.dumps(rows)

    result = questions.generate_audited_and_persist(
        [source], lambda *args: pytest.fail("force cannot regenerate existing content"),
        audit_chat=audit, force=True,
    )
    assert result["failed"] == 1
    assert len(calls) == 1
    assert questions.get_question("benefit", "recognition", source=source) is None
    stored = questions.load_bank()["questions"]["benefit"]
    assert stored["withdrawn_at"]
    assert stored["recognition"] == original["recognition"]
    assert stored["context"] == original["context"]


def test_invalid_model_draft_is_retained_instead_of_regenerated(private_question_bank):
    source = _source("benefit", "n. 益处")
    raw = {**_generated("benefit"), "context_sentence": "A benefit is clear."}
    calls = []

    def generate(messages, max_tokens):
        calls.append(messages[-1]["content"])
        return json.dumps([raw])

    first = questions.generate_audited_and_persist([source], generate)
    assert first["failed"] == 1
    assert len(calls) == 1
    questions.generate_audited_and_persist([source], generate, force=True, retry_failed=True)
    assert len(calls) == 1
    stored = questions.load_bank()["candidates"]["benefit"]
    assert stored["pool"]["raw"] == raw
    assert stored["manual_review_required"] is True

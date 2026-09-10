from copy import deepcopy
import json

import pytest

import gaokao_questions as questions
import question_external_audit as external


@pytest.fixture
def pool():
    source = {
        "english": "abandon", "chinese": "v. 放弃", "level": "高中", "pos": "v",
        "phonetic": "/abandon/", "source_hash": "test-source-hash",
        "context_sentence": "They decided to ____ the project.",
        "context_answer": "abandon", "context_cn": "他们决定放弃这个项目。",
    }
    raw = {
        "english": "abandon",
        "recognition_distractors": ["坚持", "批评", "记忆", "观察", "收集", "衡量"],
        "recognition_explanation_zh": "abandon 表示放弃或不再继续。",
        "context_sentence": (
            "Because the old bridge had become dangerously unstable, the engineers decided "
            "to abandon the entire project after another structural inspection failed."
        ),
        "context_translation_zh": "由于旧桥已变得极不稳定，另一次结构检查未通过后，工程师们决定放弃整个项目。",
        "context_distractors": ["preserve", "examine", "repair", "paint", "measure", "sell"],
        "context_explanation_zh": "桥梁不稳定且检查未通过，因此工程师决定放弃项目。",
    }
    result, error = questions.build_generation_candidate_pool(source, raw)
    assert not error
    return result


def blind_verdict(pool, kind, *, extra_valid=(), ungrammatical=()):
    task = external.prepare(pool, f"{kind}_blind")["task"]
    answer = "放弃" if kind == "recognition" else "abandon"
    valid = [option["text"] in {answer, *extra_valid} for option in task["options"]]
    parallel = [option["text"] not in ungrammatical for option in task["options"]]
    if kind == "recognition":
        return {"recognition_valid_definition": valid, "recognition_parallel_form": parallel}
    return {
        "context_grammatical": parallel, "context_meaning_fits": valid,
        "context_quality": {
            "natural": True, "decisive_clues": True, "answer_revealed": False,
            "reason_zh": "句子自然，检查失败和危险情况提供具体线索。",
        },
    }


def previous_verdicts(pool):
    return {f"{kind}_blind": blind_verdict(pool, kind) for kind in questions.QUESTION_TYPES}


def feedback_verdict():
    return {"feedback_quality": {
        "recognition_explanation_correct": True, "recognition_options_parallel": True,
        "translation_correct": True, "context_explanation_correct": True,
        "answer_matches_headword": True, "reason_zh": "译文及解析准确，选项形式一致。",
    }}


def test_context_task_is_redacted_and_option_order_matches_existing_audit(pool):
    original = deepcopy(pool)
    task = external.prepare(pool, "context_blind")["task"]
    assert set(task) == {"prompt", "options"}
    assert "abandon" not in task["prompt"]
    assert task["prompt"].count("____") == 1
    assert all(set(option) == {"id", "text"} for option in task["options"])
    assert task["options"] == questions._option_rows(
        pool["raw"]["context_distractors"], "abandon", "abandon:pool:context",
    )[0]
    recognition = external.prepare(pool, "recognition_blind")["task"]
    assert set(recognition) == {"prompt", "options"}
    assert recognition["prompt"] == "abandon"
    assert recognition["options"] == questions._option_rows(
        pool["raw"]["recognition_distractors"], "放弃", "abandon:pool:recognition",
    )[0]
    assert pool == original


@pytest.mark.parametrize("kind", questions.QUESTION_TYPES)
def test_invalid_correct_answer_is_semantic_rejection(pool, kind):
    verdict = blind_verdict(pool, kind)
    field = "recognition_valid_definition" if kind == "recognition" else "context_meaning_fits"
    verdict[field] = [False] * len(verdict[field])
    result = external.validate_and_evaluate(pool, f"{kind}_blind", verdict)
    assert result["status"] == "rejected"
    assert result["error"] == f"semantic audit rejected {kind} correct answer"
    assert "record" not in result


@pytest.mark.parametrize("field,value", [
    ("natural", False), ("decisive_clues", False), ("answer_revealed", True),
])
def test_context_quality_failure_cannot_pass(pool, field, value):
    verdict = blind_verdict(pool, "context")
    verdict["context_quality"][field] = value
    result = external.validate_and_evaluate(pool, "context_blind", verdict)
    assert result["status"] == "rejected"
    assert "context quality" in result["error"]


def test_insufficient_safe_options_is_rejection(pool):
    verdict = blind_verdict(pool, "context", extra_valid=pool["raw"]["context_distractors"][:4])
    result = external.validate_and_evaluate(pool, "context_blind", verdict)
    assert result["status"] == "rejected"
    assert "insufficient safe options (2/3)" in result["error"]


def test_feedback_uses_safe_backup_options_and_preserves_candidate_identity(pool):
    prior = {
        "recognition_blind": blind_verdict(pool, "recognition", extra_valid=("坚持",)),
        "context_blind": blind_verdict(pool, "context", extra_valid=("preserve",), ungrammatical=("examine",)),
    }
    task = external.prepare(pool, "feedback", prior)["task"]
    for kind in questions.QUESTION_TYPES:
        assert len(task[kind]["options"]) == 4
    assert "坚持" not in {option["text"] for option in task["recognition"]["options"]}
    assert not {"preserve", "examine"} & {option["text"] for option in task["context"]["options"]}
    result = external.validate_and_evaluate(pool, "feedback", feedback_verdict(), prior)
    assert result["status"] == "approved"
    record = result["record"]
    assert record["recognition"] == task["recognition"]
    assert record["context"] == task["context"]
    assert record["source_hash"] == pool["source"]["source_hash"]
    assert record["source_snapshot"] == pool["source"]
    assert record["source_content_hash"] == questions.source_content_hash(pool["source"])
    assert record["candidate_id"] == questions._candidate_pool_fingerprint(pool)
    assert record["quality_gate"] == questions.INDEPENDENT_AUDIT_QUALITY_GATE
    assert record["audit_version"] == questions.AUDIT_VERSION


@pytest.mark.parametrize("field", questions._feedback_audit_spec()[0])
def test_each_feedback_check_is_required_for_publication(pool, field):
    verdict = feedback_verdict()
    verdict["feedback_quality"][field] = False
    result = external.validate_and_evaluate(pool, "feedback", verdict, previous_verdicts(pool))
    assert result["status"] == "rejected"
    assert field in result["error"]
    assert "record" not in result


@pytest.mark.parametrize("missing", ["recognition_blind", "context_blind"])
def test_feedback_requires_both_prior_stages(pool, missing):
    prior = previous_verdicts(pool)
    del prior[missing]
    with pytest.raises(ValueError, match=f"requires an approved {missing}"):
        external.prepare(pool, "feedback", prior)
    with pytest.raises(ValueError, match=f"requires an approved {missing}"):
        external.validate_and_evaluate(pool, "feedback", feedback_verdict(), prior)


def test_feedback_revalidates_prior_semantic_judgments(pool):
    prior = previous_verdicts(pool)
    prior["context_blind"]["context_quality"]["decisive_clues"] = False
    with pytest.raises(ValueError, match="requires an approved context_blind"):
        external.prepare(pool, "feedback", prior)


@pytest.mark.parametrize("value", [None, "true", [True], [1] * 7, [True] * 6])
def test_blind_arrays_require_exact_number_of_json_booleans(pool, value):
    verdict = blind_verdict(pool, "recognition")
    verdict["recognition_valid_definition"] = value
    with pytest.raises(ValueError, match="boolean array"):
        external.validate_and_evaluate(pool, "recognition_blind", verdict)


def test_external_verdict_is_sanitized_and_does_not_mutate_input(pool):
    verdict = blind_verdict(pool, "context")
    verdict["item_id"] = "opaque-lease-id"
    result = external.validate_and_evaluate(pool, "context_blind", verdict)
    assert "item_id" not in result["verdict"]
    result["verdict"]["context_quality"]["natural"] = False
    assert verdict["context_quality"]["natural"] is True
    verdict["approved"] = True
    with pytest.raises(ValueError, match="unexpected fields"):
        external.validate_and_evaluate(pool, "context_blind", verdict)


@pytest.mark.parametrize("reason", [None, "Looks correct", 123, False])
def test_quality_reason_must_be_chinese_text(pool, reason):
    verdict = feedback_verdict()
    verdict["feedback_quality"]["reason_zh"] = reason
    with pytest.raises(ValueError, match="Chinese reason"):
        external.validate_and_evaluate(pool, "feedback", verdict, previous_verdicts(pool))


@pytest.mark.parametrize("invalid_pool", [None, {}, {"source": {}}, {"source": "word"}])
def test_invalid_pool_is_rejected(invalid_pool):
    with pytest.raises(ValueError):
        external.prepare(invalid_pool, "recognition_blind")


def test_external_protocol_has_same_tasks_and_result_as_existing_audit(pool, monkeypatch):
    prior = previous_verdicts(pool)
    replies = {**prior, "feedback": feedback_verdict()}
    stages = iter(external.STAGES)

    def chat(messages, max_tokens):
        stage = next(stages)
        prepared = external.prepare(pool, stage, prior)
        instructions, encoded = messages[0]["content"].split("\n\n待审数据 JSON：\n", 1)
        assert instructions == prepared["instructions"]
        assert json.loads(encoded) == [{"item_id": "q1", **prepared["task"]}]
        return json.dumps([{"item_id": "q1", **replies[stage]}], ensure_ascii=False)

    monkeypatch.setattr(questions, "_question_store", lambda: pytest.fail("unexpected storage access"))
    approved, rejected, retry = questions.audit_generation_candidate_pools({"abandon": pool}, chat)
    result = external.validate_and_evaluate(pool, "feedback", feedback_verdict(), prior)
    assert not rejected and not retry
    for field in ("recognition", "context", "candidate_id", "source_snapshot", "quality_gate"):
        assert result["record"][field] == approved["abandon"][field]

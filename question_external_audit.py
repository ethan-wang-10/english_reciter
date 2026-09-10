"""Prepare and evaluate independent audits without making model requests."""

from copy import deepcopy
from typing import Optional

import gaokao_questions as questions


STAGES = ("recognition_blind", "context_blind", "feedback")


def _validated_pool(pool: dict) -> dict:
    if not isinstance(pool, dict) or not isinstance(pool.get("source"), dict):
        raise ValueError("semantic audit received an invalid candidate pool")
    source = pool["source"]
    for field in ("english", "chinese", "context_answer", "source_hash"):
        if not isinstance(source.get(field), str) or not source[field].strip():
            raise ValueError(f"candidate pool source requires a nonempty {field}")
    rebuilt, error = questions.build_generation_candidate_pool(source, pool.get("raw"))
    if not rebuilt:
        raise ValueError(error or "semantic audit received an invalid candidate pool")
    return {**pool, "record": rebuilt["record"]}


def _validate_stage(stage: str) -> None:
    if stage not in STAGES:
        raise ValueError("unknown external audit stage")


def _validated_verdict(payload: dict, stage: str, task: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("audit verdict must be a JSON object")
    if stage == "feedback":
        fields, _ = questions._feedback_audit_spec()
        verdict_fields = ("feedback_quality",)
        error = questions._quality_error(payload.get("feedback_quality"), fields)
    else:
        kind = stage.removesuffix("_blind")
        fields, _ = questions._blind_audit_spec(kind)
        verdict_fields = (*fields, "context_quality") if kind == "context" else fields
        error = questions._validate_blind_audit(payload, task, kind)
    if error:
        raise ValueError(error)
    if set(payload) - {*verdict_fields, "item_id"}:
        raise ValueError("audit verdict contains unexpected fields")
    verdict = {field: deepcopy(payload[field]) for field in verdict_fields}
    if stage in ("context_blind", "feedback"):
        quality_field = "context_quality" if stage == "context_blind" else "feedback_quality"
        quality_fields = (
            ("natural", "decisive_clues", "answer_revealed")
            if stage == "context_blind" else fields
        )
        verdict[quality_field] = {
            field: verdict[quality_field][field] for field in (*quality_fields, "reason_zh")
        }
    return verdict


def _selected_record(pool: dict, previous_verdicts: Optional[dict]) -> dict:
    if not isinstance(previous_verdicts, dict):
        raise ValueError("feedback audit requires both approved blind audit verdicts")
    option_sets, _ = questions._candidate_pool_audit_questions(pool)
    safe = {}
    for kind in questions.QUESTION_TYPES:
        stage = f"{kind}_blind"
        if stage not in previous_verdicts:
            raise ValueError(f"feedback audit requires an approved {stage} verdict")
        verdict = _validated_verdict(previous_verdicts[stage], stage, option_sets[kind])
        safe[kind], error = questions._evaluate_blind_audit(
            option_sets[kind], verdict, kind, candidate_pool=True,
        )
        if error:
            raise ValueError(f"feedback audit requires an approved {stage} verdict: {error}")
    record, error = questions._select_audited_candidate_pool(pool, safe)
    if not record:
        raise ValueError(error)
    return record


def prepare(pool: dict, stage: str, previous_verdicts: Optional[dict] = None) -> dict:
    """Return reviewer instructions and data; the caller adds an opaque item_id."""
    _validate_stage(stage)
    pool = _validated_pool(pool)
    if stage == "feedback":
        record = _selected_record(pool, previous_verdicts)
        _, instructions = questions._feedback_audit_spec()
        task = {
            "headword": pool["source"]["english"],
            "recognition": record["recognition"],
            "context": record["context"],
        }
    else:
        kind = stage.removesuffix("_blind")
        question = questions._candidate_pool_audit_questions(pool)[0][kind]
        _, instructions = questions._blind_audit_spec(kind)
        task = {"prompt": question["prompt"], "options": question["options"]}
    return {"stage": stage, "instructions": instructions, "task": deepcopy(task)}


def validate_and_evaluate(
    pool: dict, stage: str, payload: dict, previous_verdicts: Optional[dict] = None,
) -> dict:
    """Reject malformed verdicts; only a complete approved audit yields a record."""
    _validate_stage(stage)
    pool = _validated_pool(pool)
    record = None
    if stage == "feedback":
        record = _selected_record(pool, previous_verdicts)
        verdict = _validated_verdict(payload, stage, {})
        error = questions._evaluate_feedback_audit(verdict)
    else:
        kind = stage.removesuffix("_blind")
        question = questions._candidate_pool_audit_questions(pool)[0][kind]
        verdict = _validated_verdict(payload, stage, question)
        _, error = questions._evaluate_blind_audit(question, verdict, kind, candidate_pool=True)
    result = {"status": "rejected" if error else "approved", "error": error, "verdict": verdict}
    if record is not None and not error:
        result["record"] = questions._mark_independently_audited(record)
    return result

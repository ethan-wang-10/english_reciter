"""Administrator API workflow for externally generated and independently reviewed questions."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Callable, Optional

import gaokao_backfill
import gaokao_questions as questions
import question_external_audit as audit
from question_authoring_store import JOB_KINDS, QuestionAuthoringStore


GENERATION_FIELDS = {
    "english", "recognition_distractors", "recognition_explanation_zh",
    "context_sentence", "context_translation_zh", "context_distractors", "context_explanation_zh",
}


class AuthoringError(Exception):
    def __init__(self, message: str, status: int = 400, details=None):
        super().__init__(message)
        self.status = status
        self.details = details


def _identifier(value, name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value):
        raise AuthoringError(f"{name} must be a nonempty identifier of at most 128 characters")
    return value


def _integer(value, name: str, low: int, high: int) -> int:
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        if len(value) > 6:
            raise AuthoringError(f"{name} must be between {low} and {high}")
        value = int(value)
    if type(value) is not int or not low <= value <= high:
        raise AuthoringError(f"{name} must be between {low} and {high}")
    return value


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _meta(value: Optional[dict]) -> dict:
    if not isinstance(value, dict):
        return {}
    pool = value.get("pool")
    owner = pool if isinstance(pool, dict) else value
    metadata = owner.get("external_authoring")
    return metadata if isinstance(metadata, dict) else {}


def _pool(value: Optional[dict]) -> Optional[dict]:
    if not isinstance(value, dict):
        return None
    pool = value.get("pool")
    return pool if isinstance(pool, dict) and pool.get("audit_provider") == "external" else None


class QuestionAuthoringService:
    def __init__(self, sources: Callable[[str], list[dict]], source_lookup: Callable[[str], Optional[dict]]):
        self.sources = sources
        self.source_lookup = source_lookup

    def _jobs(self) -> QuestionAuthoringStore:
        return QuestionAuthoringStore(questions.QUESTION_BANK_FILE.with_name("question_authoring.sqlite3"))

    @contextmanager
    def _job_lock(self):
        with gaokao_backfill.generation_job_lock(blocking=False) as acquired:
            if not acquired:
                raise AuthoringError("another question task is running; retry later", 409)
            yield

    @contextmanager
    def _bank(self, keys):
        with questions._thread_lock:
            with questions._interprocess_lock():
                bank = questions._read_bank_keys_unlocked(keys)
                yield bank
                questions._write_bank_keys_unlocked(bank, keys)

    def _parameters(self, data: dict) -> dict:
        kind = data.get("kind", "generation")
        if kind not in JOB_KINDS:
            raise AuthoringError("unknown authoring kind")
        level = data.get("level", "高中")
        if not isinstance(level, str) or len(level) > 40:
            raise AuthoringError("level must be a string of at most 40 characters")
        return {
            "kind": kind, "worker_id": _identifier(data.get("worker_id"), "worker_id"),
            "level": level.strip(), "limit": _integer(data.get("limit", 5), "limit", 1, 10),
            "ttl_seconds": _integer(data.get("ttl_seconds", 3600), "ttl_seconds", 60, 86400),
        }

    @staticmethod
    def _prior(value: dict) -> dict:
        pool = _pool(value)
        if not pool:
            return {}
        valid = {}
        for stage in ("recognition_blind", "context_blind"):
            row = _meta(value).get("audits", {}).get(stage)
            if not isinstance(row, dict) or row.get("status") != "approved" or row.get("audit_version") != questions.AUDIT_VERSION:
                continue
            if not QuestionAuthoringService._reviewer_allowed(value, stage, row.get("reviewer_id")):
                continue
            if stage == "context_blind" and "recognition_blind" not in valid:
                continue
            if row.get("input_fingerprint") == _digest(audit.prepare(pool, stage)):
                valid[stage] = row["verdict"]
        return valid

    @staticmethod
    def _reviewer_allowed(value: dict, stage: str, worker: str) -> bool:
        metadata = _meta(value)
        if worker == metadata.get("generator_id"):
            return False
        if stage == "recognition_blind":
            context = metadata.get("audits", {}).get("context_blind", {})
            if worker == context.get("reviewer_id"):
                return False
        if stage == "context_blind":
            recognition = metadata.get("audits", {}).get("recognition_blind", {})
            if worker == recognition.get("reviewer_id"):
                return False
        return True

    def _eligible(self, params: dict, now: datetime, count: int) -> list[dict]:
        kind, worker = params["kind"], params["worker_id"]
        active = self._jobs().active_words(now=now, kind=kind)
        sources = self.sources(params["level"])
        selected = []
        for offset in range(0, len(sources), 200):
            batch = sources[offset:offset + 200]
            bank = questions._read_bank_keys_unlocked([source["english"] for source in batch])
            for source in batch:
                key = source["english"]
                if key in active:
                    continue
                candidate = bank["candidates"].get(key)
                metadata = _meta(candidate)
                if kind == "generation":
                    if any(key in bank[name] for name in ("questions", "rejections", "failures")):
                        continue
                    if candidate is not None and not (
                        _pool(candidate) and metadata.get("state") == "claimed"
                        and not _pool(candidate).get("raw")
                    ):
                        continue
                    selected.append({"english": key, "source": source})
                else:
                    pool = _pool(candidate)
                    if not pool or candidate.get("manual_review_required") or metadata.get("state") != "generated":
                        continue
                    if questions.source_content_hash(pool["source"]) != questions.source_content_hash(source):
                        continue
                    prior = self._prior(candidate)
                    if kind in prior or not self._reviewer_allowed(candidate, kind, worker):
                        continue
                    if kind == "context_blind" and "recognition_blind" not in prior:
                        continue
                    if kind == "feedback" and not {"recognition_blind", "context_blind"}.issubset(prior):
                        continue
                    prepared = audit.prepare(pool, kind, prior)
                    selected.append({
                        "english": key, "source": source,
                        "candidate_id": questions._candidate_pool_fingerprint(pool),
                        "audit_version": questions.AUDIT_VERSION,
                        "input_fingerprint": _digest(prepared),
                        "protocol": prepared,
                    })
                if len(selected) >= count:
                    return selected
        return selected

    @staticmethod
    def _view(job: dict) -> dict:
        kind = job["kind"]
        result = {key: job[key] for key in (
            "job_id", "kind", "worker_id", "request_id", "created_at", "expires_at", "status",
        )}
        if kind == "generation":
            result["instructions"] = questions.build_generation_prompt([item["source"] for item in job["items"]])
            result["items"] = [{"item_id": item["item_id"], "source": item["source"]} for item in job["items"]]
        else:
            result["instructions"] = job["items"][0]["protocol"]["instructions"] if job["items"] else ""
            result["items"] = [{"item_id": item["item_id"], **item["protocol"]["task"]} for item in job["items"]]
        result["versions"] = {
            "generation_prompt": questions.GENERATION_PROMPT_VERSION, "audit": questions.AUDIT_VERSION,
        }
        return result

    def pending(self, data: dict, now: Optional[datetime] = None) -> dict:
        now = now or datetime.now(timezone.utc)
        params = self._parameters(data)
        items = self._eligible(params, now, params["limit"] + 1)
        visible = items[:params["limit"]]
        return {
            "kind": params["kind"], "has_more": len(items) > len(visible),
            "items": ([{"source": item["source"]} for item in visible] if params["kind"] == "generation"
                      else [item["protocol"]["task"] for item in visible]),
        }

    def _reserve(self, job: dict) -> None:
        if job["kind"] != "generation" or not job["items"] or job["status"] != "active":
            return
        with self._bank([item["english"] for item in job["items"]]) as bank:
            for item in job["items"]:
                key = item["english"]
                current = bank["candidates"].get(key)
                metadata = _meta(current)
                if any(
                    _meta(bank[name].get(key)).get("generation_job_id") == job["job_id"]
                    and _meta(bank[name].get(key)).get("state") in {"generated", "manual"}
                    for name in ("candidates", "questions", "rejections")
                ):
                    continue
                if any(key in bank[name] for name in ("questions", "rejections", "failures")):
                    raise AuthoringError("question state changed before reservation", 409)
                if current is not None and not (
                    _pool(current) and metadata.get("state") == "claimed" and not _pool(current).get("raw")
                ):
                    raise AuthoringError("generated content already exists", 409)
                if metadata.get("generation_job_id") == job["job_id"]:
                    continue
                pool = {
                    "source": item["source"], "raw": {}, "record": {},
                    "generation_prompt_version": questions.GENERATION_PROMPT_VERSION,
                    "audit_provider": "external",
                    "external_authoring": {
                        "state": "claimed", "generator_id": job["worker_id"],
                        "generation_job_id": job["job_id"], "audits": {}, "receipts": {},
                    },
                }
                bank["candidates"][key] = {
                    "pool": pool, "record": {}, "created_at": job["created_at"],
                    "audit_provider": "external",
                }

    def claim(self, data: dict, now: Optional[datetime] = None) -> dict:
        now = now or datetime.now(timezone.utc)
        params = self._parameters(data)
        request_id = _identifier(data.get("request_id"), "request_id")
        with self._job_lock():
            jobs = self._jobs()
            prior = jobs.get_request(request_id, now=now)
            if prior:
                if prior.get("request_parameters") != params:
                    raise AuthoringError("request_id was used with different parameters", 409)
                self._reserve(prior)
                return self._view(prior)
            items = self._eligible(params, now, params["limit"])
            job = jobs.claim(
                items, now=now, ttl_seconds=params["ttl_seconds"], limit=params["limit"],
                request_id=request_id, kind=params["kind"], worker_id=params["worker_id"],
                request_parameters=params,
            )
            self._reserve(job)
            if not job["items"]:
                job = jobs.complete(job["job_id"], now=now)
            return self._view(job)

    def _owned_job(self, job_id: str, worker_id: str, now: datetime) -> dict:
        job = self._jobs().get(_identifier(job_id, "job_id"), now=now)
        if job is None:
            raise AuthoringError("authoring job not found", 404)
        if job["worker_id"] != _identifier(worker_id, "worker_id"):
            raise AuthoringError("worker_id does not own this job", 403)
        return job

    def get_job(self, job_id: str, worker_id: str, now: Optional[datetime] = None) -> dict:
        return self._view(self._owned_job(job_id, worker_id, now or datetime.now(timezone.utc)))

    def renew(self, job_id: str, data: dict, now: Optional[datetime] = None) -> dict:
        now = now or datetime.now(timezone.utc)
        with self._job_lock():
            self._owned_job(job_id, data.get("worker_id"), now)
            try:
                job = self._jobs().renew(job_id, now=now, ttl_seconds=_integer(data.get("ttl_seconds", 3600), "ttl_seconds", 60, 86400))
            except ValueError as exc:
                raise AuthoringError(str(exc), 409) from exc
            return self._view(job)

    def release(self, job_id: str, data: dict, now: Optional[datetime] = None) -> dict:
        now = now or datetime.now(timezone.utc)
        with self._job_lock():
            job = self._owned_job(job_id, data.get("worker_id"), now)
            job = self._jobs().release(job_id, now=now)
            if job["kind"] == "generation":
                with self._bank([item["english"] for item in job["items"]]) as bank:
                    for item in job["items"]:
                        value = bank["candidates"].get(item["english"])
                        metadata = _meta(value)
                        if (metadata.get("generation_job_id") == job_id and metadata.get("state") == "claimed"
                                and _pool(value) and not _pool(value).get("raw")):
                            bank["candidates"].pop(item["english"], None)
            return self._view(job)

    def submit(self, job_id: str, data: dict, now: Optional[datetime] = None) -> dict:
        now = now or datetime.now(timezone.utc)
        submission_id = _identifier(data.get("submission_id"), "submission_id")
        items = data.get("items")
        if not isinstance(items, list) or not 1 <= len(items) <= 10:
            raise AuthoringError("items must contain 1 to 10 job results")
        if any(not isinstance(item, dict) or set(item) != {"item_id", "result"}
               or not isinstance(item["result"], dict) for item in items):
            raise AuthoringError("each item must contain item_id and a result object")
        ids = [_identifier(item["item_id"], "item_id") for item in items]
        if len(set(ids)) != len(ids):
            raise AuthoringError("duplicate item_id")
        by_id = {item["item_id"]: item["result"] for item in items}
        try:
            digest = _digest({"worker_id": data.get("worker_id"), "items": sorted(items, key=lambda item: item["item_id"])})
        except (ValueError, TypeError) as exc:
            raise AuthoringError("submission is not valid JSON data") from exc
        with self._job_lock():
            job = self._owned_job(job_id, data.get("worker_id"), now)
            if set(ids) != {item["item_id"] for item in job["items"]}:
                raise AuthoringError("submit exactly the items in this job", 422)
            jobs = self._jobs()
            receipt = jobs.get_submission(job_id, submission_id)
            if receipt:
                if receipt["digest"] != digest:
                    raise AuthoringError("submission_id was used with different content", 409)
                jobs.complete(job_id, now=now)
                return receipt["result"]

            keys = [item["english"] for item in job["items"]]
            receipt_key = f"{job_id}:{submission_id}"
            result = {"job_id": job_id, "kind": job["kind"], "submission_id": submission_id, "status": "completed", "items": []}
            with self._bank(keys) as bank:
                recovered = []
                for item in job["items"]:
                    saved = next((
                        _meta(bank[namespace].get(item["english"])).get("receipts", {}).get(receipt_key)
                        for namespace in ("candidates", "questions", "rejections")
                        if receipt_key in _meta(bank[namespace].get(item["english"])).get("receipts", {})
                    ), None)
                    if saved:
                        if saved["digest"] != digest:
                            raise AuthoringError("stored submission has different content", 409)
                        recovered.append(saved["result"])
                if len(recovered) == len(job["items"]):
                    result["items"] = recovered
                else:
                    if recovered or job["status"] != "active":
                        raise AuthoringError("job is no longer active; get a current claim", 409)
                    prepared = []
                    for item in job["items"]:
                        key = item["english"]
                        source = self.source_lookup(key)
                        if not source or questions.source_content_hash(source) != questions.source_content_hash(item["source"]):
                            raise AuthoringError("wordbank source changed; upload was not applied", 409)
                        candidate = bank["candidates"].get(key)
                        pool = _pool(candidate)
                        if not pool:
                            raise AuthoringError("candidate no longer belongs to external authoring", 409)
                        metadata = copy.deepcopy(_meta(candidate))
                        payload = by_id[item["item_id"]]
                        if job["kind"] == "generation":
                            if (metadata.get("generation_job_id") != job_id or metadata.get("state") != "claimed"
                                    or pool.get("raw") or key in bank["questions"] or key in bank["rejections"]):
                                raise AuthoringError("generated content already exists; overwrite refused", 409)
                            if set(payload) != GENERATION_FIELDS or payload.get("english") != key:
                                raise AuthoringError("generation result fields or english do not match the job", 422)
                            rebuilt, error = questions.build_generation_candidate_pool(source, payload)
                            pool = rebuilt or {"source": source, "raw": copy.deepcopy(payload), "record": {}, "generation_prompt_version": questions.GENERATION_PROMPT_VERSION}
                            metadata["state"] = "generated" if rebuilt else "manual"
                            outcome = {"item_id": item["item_id"], "status": "awaiting_recognition_blind" if rebuilt else "manual", "error": error}
                            prepared.append((item, pool, metadata, outcome, None))
                        else:
                            prior = self._prior(candidate)
                            if (metadata.get("state") != "generated"
                                    or questions._candidate_pool_fingerprint(pool) != item["candidate_id"]
                                    or job["kind"] in prior):
                                raise AuthoringError("candidate or audit stage changed", 409)
                            if not self._reviewer_allowed(candidate, job["kind"], job["worker_id"]):
                                raise AuthoringError("reviewer must be independent of the earlier author/reviewer", 403)
                            if job["kind"] == "context_blind" and "recognition_blind" not in prior:
                                raise AuthoringError("recognition audit is not approved", 409)
                            try:
                                current_fingerprint = _digest(audit.prepare(pool, job["kind"], prior))
                            except ValueError as exc:
                                raise AuthoringError("earlier audit is no longer valid; claim a fresh audit", 409) from exc
                            if (item.get("audit_version") != questions.AUDIT_VERSION or
                                    item.get("input_fingerprint") != current_fingerprint):
                                raise AuthoringError("audit instructions or inputs changed; claim a fresh audit", 409)
                            try:
                                if "item_id" in payload and payload["item_id"] != item["item_id"]:
                                    raise ValueError("verdict item_id does not match")
                                evaluated = audit.validate_and_evaluate(pool, job["kind"], payload, prior)
                            except ValueError as exc:
                                raise AuthoringError(str(exc), 422, {"item_id": item["item_id"]}) from exc
                            metadata.setdefault("audits", {})[job["kind"]] = {
                                "reviewer_id": job["worker_id"], "job_id": job_id,
                                "audit_version": questions.AUDIT_VERSION,
                                "input_fingerprint": item["input_fingerprint"],
                                "status": evaluated["status"], "verdict": evaluated["verdict"],
                            }
                            next_stage = {"recognition_blind": "awaiting_context_blind", "context_blind": "awaiting_feedback", "feedback": "published"}
                            outcome = {"item_id": item["item_id"], "status": next_stage[job["kind"]] if evaluated["status"] == "approved" else "rejected", "error": evaluated["error"]}
                            prepared.append((item, copy.deepcopy(pool), metadata, outcome, evaluated.get("record")))

                    for item, pool, metadata, outcome, record in prepared:
                        key = item["english"]
                        metadata.setdefault("receipts", {})[receipt_key] = {"digest": digest, "result": outcome}
                        pool.update(audit_provider="external", external_authoring=metadata)
                        value = {"pool": pool, "record": pool["record"], "audit_provider": "external", "created_at": now.isoformat()}
                        if outcome["status"] == "published":
                            record.pop("candidate_id", None)
                            record.update(audit_provider="external", external_authoring=metadata)
                            bank["questions"][key] = record
                            bank["candidates"].pop(key, None)
                            bank["failures"].pop(key, None)
                        elif outcome["status"] == "rejected":
                            bank["rejections"][key] = {**value, "last_error": outcome["error"], "manual_review_required": True}
                            bank["candidates"].pop(key, None)
                        else:
                            bank["candidates"][key] = {**value, "manual_review_required": outcome["status"] == "manual"}
                        result["items"].append(outcome)
            jobs.save_submission(job_id, submission_id, digest=digest, result=result)
            jobs.complete(job_id, now=now)
            return result

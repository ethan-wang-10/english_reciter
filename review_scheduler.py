"""Adaptive review scheduling and per-exercise mastery state.

The scheduler is deliberately stored outside individual word records so older
application versions can still read ``learning_data.json`` safely.  Scheduling
uses a small SM-2 style model; it is not presented as a full FSRS implementation.
"""

import math
from datetime import date, timedelta
from typing import Any, Dict, Optional


STATE_VERSION = 1
SCHEDULER_ALGORITHM = "adaptive-sm2-v1"
EXERCISE_TYPES = ("spelling", "listening")
RATINGS = ("again", "hard", "good", "easy")
MAX_INTERVAL_DAYS = 365
RECENT_EVENT_LIMIT = 100


class ReviewEventConflict(ValueError):
    """Raised when one idempotency key is reused for a different answer."""


def _bounded_float(value: Any, default: float, low: float, high: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        parsed = default
    if not math.isfinite(parsed):
        parsed = default
    return max(low, min(high, parsed))


def _bounded_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = default
    return max(low, min(high, parsed))


def _iso_date(value: Any) -> Optional[str]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except (TypeError, ValueError):
        return None


def _legacy_mastery_score(success_count: int, max_success_count: int) -> float:
    if max_success_count <= 0:
        return 0.0
    return round(min(0.8, max(0.0, success_count / max_success_count * 0.8)), 4)


def _normalize_dimension(raw: Any, inferred_score: float = 0.0) -> Dict[str, Any]:
    src = raw if isinstance(raw, dict) else {}
    attempts = _bounded_int(src.get("attempts"), 0, 0, 1_000_000)
    correct = _bounded_int(src.get("correct"), 0, 0, attempts)
    streak = _bounded_int(src.get("streak"), 0, 0, 10_000)
    score_default = inferred_score if attempts == 0 else (correct / attempts if attempts else 0.0)
    normalized = dict(src)
    normalized.update(
        {
            "score": round(_bounded_float(src.get("score"), score_default, 0.0, 1.0), 4),
            "attempts": attempts,
            "correct": correct,
            "streak": streak,
            "last_review_date": _iso_date(src.get("last_review_date")),
            "avg_response_ms": _bounded_int(src.get("avg_response_ms"), 0, 0, 600_000),
        }
    )
    return normalized


def normalize_review_state(
    raw: Any,
    *,
    success_count: int = 0,
    max_success_count: int = 8,
    legacy_interval_days: int = 0,
    legacy_active: bool = False,
) -> Dict[str, Any]:
    """Return a validated state without trusting persisted values blindly."""
    src = raw if isinstance(raw, dict) else {}
    scheduler_src = src.get("scheduler") if isinstance(src.get("scheduler"), dict) else {}
    mastery_src = src.get("mastery") if isinstance(src.get("mastery"), dict) else {}
    inferred_spelling = _legacy_mastery_score(success_count, max_success_count)
    repetitions_default = min(max(0, int(success_count or 0)), 2)
    interval_default = max(0, int(legacy_interval_days or 0))

    recent = src.get("recent_event_ids") if isinstance(src.get("recent_event_ids"), list) else []
    recent_ids = []
    for value in recent[-RECENT_EVENT_LIMIT:]:
        event_id = str(value or "").strip()[:96]
        if event_id and event_id not in recent_ids:
            recent_ids.append(event_id)
    fingerprints_src = (
        src.get("recent_event_fingerprints")
        if isinstance(src.get("recent_event_fingerprints"), dict)
        else {}
    )
    recent_event_fingerprints = {
        event_id: str(fingerprints_src.get(event_id) or "")[:512]
        for event_id in recent_ids
        if str(fingerprints_src.get(event_id) or "").strip()
    }
    results_src = (
        src.get("recent_event_results")
        if isinstance(src.get("recent_event_results"), dict)
        else {}
    )
    recent_event_results = {
        event_id: dict(results_src[event_id])
        for event_id in recent_ids
        if isinstance(results_src.get(event_id), dict)
    }

    scheduler = dict(scheduler_src)
    scheduler.update(
        {
            "algorithm": SCHEDULER_ALGORITHM,
            "active": bool(legacy_active or scheduler_src.get("active", False)),
            "ease_factor": round(
                _bounded_float(scheduler_src.get("ease_factor"), 2.5, 1.3, 3.2), 4
            ),
            "interval_days": _bounded_int(
                scheduler_src.get("interval_days"), interval_default, 0, MAX_INTERVAL_DAYS
            ),
            "repetitions": _bounded_int(
                scheduler_src.get("repetitions"), repetitions_default, 0, 100_000
            ),
            "lapses": _bounded_int(scheduler_src.get("lapses"), 0, 0, 100_000),
            "last_review_date": _iso_date(scheduler_src.get("last_review_date")),
            "last_rating": str(scheduler_src.get("last_rating") or ""),
        }
    )
    mastery = dict(mastery_src)
    mastery.update(
        {
            "spelling": _normalize_dimension(mastery_src.get("spelling"), inferred_spelling),
            "listening": _normalize_dimension(mastery_src.get("listening"), 0.0),
        }
    )
    normalized = dict(src)
    normalized.update(
        {
            "version": STATE_VERSION,
            "mastered_date": _iso_date(src.get("mastered_date")),
            "scheduler": scheduler,
            "mastery": mastery,
            "recent_event_ids": recent_ids,
            "recent_event_fingerprints": recent_event_fingerprints,
            "recent_event_results": recent_event_results,
        }
    )
    return normalized


def claim_review_event(
    state: Dict[str, Any],
    event_id: str = "",
    event_fingerprint: str = "",
) -> bool:
    """Reserve one idempotency key without changing mastery dimensions."""
    event_key = str(event_id or "").strip()[:96]
    fingerprint = str(event_fingerprint or "")[:512]
    recent = state.setdefault("recent_event_ids", [])
    fingerprints = state.setdefault("recent_event_fingerprints", {})
    if not isinstance(recent, list):
        recent = []
        state["recent_event_ids"] = recent
    if not isinstance(fingerprints, dict):
        fingerprints = {}
        state["recent_event_fingerprints"] = fingerprints
    if event_key and event_key in recent:
        stored_fingerprint = str(fingerprints.get(event_key) or "")
        if stored_fingerprint and fingerprint and stored_fingerprint != fingerprint:
            raise ReviewEventConflict("review event id was reused with a different payload")
        if fingerprint and not stored_fingerprint:
            fingerprints[event_key] = fingerprint
        return False
    if event_key:
        recent.append(event_key)
        del recent[:-RECENT_EVENT_LIMIT]
        if fingerprint:
            fingerprints[event_key] = fingerprint
        keep = set(recent)
        state["recent_event_fingerprints"] = {
            key: value for key, value in fingerprints.items() if key in keep
        }
        results = state.get("recent_event_results")
        if isinstance(results, dict):
            state["recent_event_results"] = {
                key: value for key, value in results.items() if key in keep
            }
    return True


def record_mastery_attempt(
    state: Dict[str, Any],
    exercise_type: str,
    correct: bool,
    *,
    today: date,
    event_id: str = "",
    event_fingerprint: str = "",
    elapsed_ms: int = 0,
) -> bool:
    """Record one unique answer attempt. Returns False for duplicate events."""
    if exercise_type not in EXERCISE_TYPES:
        exercise_type = "spelling"
    if not claim_review_event(state, event_id, event_fingerprint):
        return False

    dim = state["mastery"][exercise_type]
    old_attempts = int(dim.get("attempts") or 0)
    old_correct = int(dim.get("correct") or 0)
    old_streak = int(dim.get("streak") or 0)
    attempts = old_attempts + 1
    correct_count = old_correct + (1 if correct else 0)
    streak = old_streak + 1 if correct else 0

    # Bayesian accuracy avoids a misleading 0%/100% after a single answer.
    accuracy = (correct_count + 1.0) / (attempts + 2.0)
    streak_bonus = min(streak, 4) / 4.0 * 0.2
    score = min(1.0, accuracy * 0.8 + streak_bonus)

    response_ms = _bounded_int(elapsed_ms, 0, 0, 600_000)
    old_avg = int(dim.get("avg_response_ms") or 0)
    if response_ms > 0:
        avg_response_ms = (
            response_ms
            if old_avg <= 0
            else round(old_avg * 0.75 + response_ms * 0.25)
        )
    else:
        avg_response_ms = old_avg

    dim.update(
        {
            "score": round(score, 4),
            "attempts": attempts,
            "correct": correct_count,
            "streak": streak,
            "last_review_date": today.isoformat(),
            "avg_response_ms": avg_response_ms,
        }
    )
    return True


def schedule_review(
    state: Dict[str, Any],
    rating: str,
    *,
    today: date,
) -> int:
    """Apply an SM-2 style rating and return the new interval in days."""
    if rating not in RATINGS:
        rating = "good"
    scheduler = state["scheduler"]
    ease = _bounded_float(scheduler.get("ease_factor"), 2.5, 1.3, 3.2)
    repetitions = _bounded_int(scheduler.get("repetitions"), 0, 0, 100_000)
    interval = _bounded_int(scheduler.get("interval_days"), 0, 0, MAX_INTERVAL_DAYS)
    lapses = _bounded_int(scheduler.get("lapses"), 0, 0, 100_000)

    quality = {"again": 1, "hard": 3, "good": 4, "easy": 5}[rating]
    ease = max(1.3, ease + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))

    if rating == "again":
        repetitions = 0
        interval = 1
        lapses += 1
    elif repetitions <= 0:
        repetitions = 1
        interval = 1 if rating in ("hard", "good") else 2
    elif repetitions == 1:
        repetitions = 2
        interval = 3 if rating == "hard" else (6 if rating == "good" else 8)
    else:
        repetitions += 1
        multiplier = 1.2 if rating == "hard" else ease * (1.3 if rating == "easy" else 1.0)
        interval = max(1, round(max(1, interval) * multiplier))

    interval = min(MAX_INTERVAL_DAYS, interval)
    scheduler.update(
        {
            "algorithm": SCHEDULER_ALGORITHM,
            "active": True,
            "ease_factor": round(ease, 4),
            "interval_days": interval,
            "repetitions": repetitions,
            "lapses": lapses,
            "last_review_date": today.isoformat(),
            "last_rating": rating,
        }
    )
    return interval


def next_review_date(state: Dict[str, Any], today: date) -> date:
    interval = _bounded_int(
        state.get("scheduler", {}).get("interval_days"), 1, 1, MAX_INTERVAL_DAYS
    )
    return today + timedelta(days=interval)


def choose_exercise_type(state: Dict[str, Any]) -> str:
    """Choose the least-practised/weakest available ability dimension."""
    mastery = state.get("mastery") or {}
    ranked = []
    for order, exercise_type in enumerate(EXERCISE_TYPES):
        dim = mastery.get(exercise_type) if isinstance(mastery.get(exercise_type), dict) else {}
        ranked.append(
            (
                _bounded_int(dim.get("attempts"), 0, 0, 1_000_000),
                _bounded_float(dim.get("score"), 0.0, 0.0, 1.0),
                order,
                exercise_type,
            )
        )
    ranked.sort()
    return ranked[0][3]


def mastery_snapshot(state: Dict[str, Any]) -> Dict[str, Any]:
    by_type: Dict[str, Any] = {}
    scores = []
    for exercise_type in EXERCISE_TYPES:
        dim = state["mastery"][exercise_type]
        score = _bounded_float(dim.get("score"), 0.0, 0.0, 1.0)
        scores.append(score)
        by_type[exercise_type] = {
            "score": round(score, 4),
            "percent": round(score * 100),
            "attempts": int(dim.get("attempts") or 0),
            "correct": int(dim.get("correct") or 0),
            "streak": int(dim.get("streak") or 0),
        }
    overall = sum(scores) / len(scores) if scores else 0.0
    return {
        "overall": round(overall, 4),
        "overall_percent": round(overall * 100),
        "by_type": by_type,
    }


def mastery_ready(state: Dict[str, Any], *, require_listening: bool = True) -> bool:
    """Require demonstrated ability in every exercise the client can perform."""
    snapshot = mastery_snapshot(state)
    required_types = EXERCISE_TYPES if require_listening else ("spelling",)
    dimensions = [snapshot["by_type"][name] for name in required_types]
    overall = sum(float(dim["score"]) for dim in dimensions) / len(dimensions)
    return (
        overall >= 0.6
        and all(dim["attempts"] >= 1 and dim["score"] >= 0.55 for dim in dimensions)
    )


def scheduler_snapshot(state: Dict[str, Any]) -> Dict[str, Any]:
    scheduler = state["scheduler"]
    return {
        "algorithm": SCHEDULER_ALGORITHM,
        "active": bool(scheduler.get("active")),
        "interval_days": int(scheduler.get("interval_days") or 0),
        "ease_factor": round(float(scheduler.get("ease_factor") or 2.5), 2),
        "repetitions": int(scheduler.get("repetitions") or 0),
        "lapses": int(scheduler.get("lapses") or 0),
        "last_rating": str(scheduler.get("last_rating") or ""),
    }

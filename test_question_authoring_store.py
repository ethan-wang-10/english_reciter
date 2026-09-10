import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest

from question_authoring_store import QuestionAuthoringStore


NOW = datetime(2026, 9, 9, 1, 0, tzinfo=timezone.utc)
SOURCES = [{"english": "apple", "source_hash": "a"}, {"english": "banana", "source_hash": "b"}]


@pytest.fixture
def store(tmp_path):
    return QuestionAuthoringStore(tmp_path / "authoring.sqlite3")


def claim(store, **kwargs):
    return store.claim(SOURCES, now=NOW, ttl_seconds=60, limit=2, **kwargs)


def source_items(job):
    return [{key: value for key, value in item.items() if key != "item_id"} for item in job["items"]]


def test_claim_survives_reopen_and_does_not_expose_mutable_snapshots(store):
    job = claim(store)
    assert job["status"] == "active"
    assert source_items(job) == SOURCES
    assert job["kind"] == "generation"
    assert job["worker_id"] == "external"
    assert len({item["item_id"] for item in job["items"]}) == 2
    assert all(len(item["item_id"]) == 32 for item in job["items"])
    assert datetime.fromisoformat(job["created_at"]) == NOW
    assert datetime.fromisoformat(job["expires_at"]) == NOW + timedelta(seconds=60)
    reopened = QuestionAuthoringStore(store.db_path)
    job["items"][0]["english"] = "changed"
    assert source_items(reopened.get(job["job_id"], now=NOW)) == SOURCES
    assert reopened.active_words(now=NOW) == {"apple", "banana"}
    assert reopened.get("missing", now=NOW) is None


def test_two_workers_claim_disjoint_normalized_words(store):
    other = QuestionAuthoringStore(store.db_path)
    barrier = Barrier(2)

    def run(worker):
        barrier.wait()
        return worker.claim(
            [{"english": "  APPLE  "}, {"english": "apple"}, {"english": "banana"}],
            now=NOW,
            ttl_seconds=60,
            limit=1,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        jobs = list(executor.map(run, [store, other]))
    assert sorted(item["english"] for job in jobs for item in job["items"]) == ["apple", "banana"]
    assert len({job["job_id"] for job in jobs}) == 2
    assert store.active_words(now=NOW) == {"apple", "banana"}


def test_expiry_frees_words_but_preserves_original_job_and_submission(store):
    job = claim(store, request_id="first")
    store.save_submission(job["job_id"], "receipt", digest="digest", result={"accepted": ["apple"]})
    later = NOW + timedelta(seconds=60)
    assert store.active_words(now=later) == set()
    expired = store.get(job["job_id"], now=later)
    assert expired["status"] == "expired"
    assert source_items(expired) == SOURCES
    replacement = store.claim(SOURCES, now=later, ttl_seconds=60, limit=2)
    assert source_items(replacement) == SOURCES
    assert {item["item_id"] for item in replacement["items"]}.isdisjoint(
        item["item_id"] for item in expired["items"]
    )
    assert replacement["job_id"] != job["job_id"]
    assert store.get(job["job_id"], now=later)["status"] == "expired"
    assert store.get_submission(job["job_id"], "receipt")["result"] == {"accepted": ["apple"]}
    with pytest.raises(ValueError, match="unexpired"):
        store.renew(job["job_id"], now=later, ttl_seconds=60)
    assert store.release(job["job_id"], now=later)["status"] == "expired"
    assert store.active_words(now=later) == {"apple", "banana"}


def test_release_is_idempotent_and_does_not_reactivate_request(store):
    job = claim(store, request_id="first")
    released = store.release(job["job_id"], now=NOW)
    assert released["status"] == "released"
    assert source_items(released) == SOURCES
    assert store.release(job["job_id"], now=NOW) == released
    assert store.active_words(now=NOW) == set()
    assert claim(store, request_id="first") == released
    with pytest.raises(ValueError, match="active"):
        store.renew(job["job_id"], now=NOW, ttl_seconds=60)
    with pytest.raises(KeyError):
        store.release("missing", now=NOW)
    with pytest.raises(KeyError):
        store.renew("missing", now=NOW, ttl_seconds=60)


@pytest.mark.parametrize("previous_status", ["expired", "released"])
def test_durable_receipt_allows_completion_after_inactive_lease_without_touching_new_claim(store, previous_status):
    original = claim(store, request_id="original")
    later = NOW + timedelta(seconds=60)
    if previous_status == "released":
        store.release(original["job_id"], now=NOW)
    assert store.complete(original["job_id"], now=later)["status"] == previous_status
    replacement = store.claim(SOURCES, now=later, ttl_seconds=60, limit=2, request_id="replacement")
    replacement = store.renew(replacement["job_id"], now=later, ttl_seconds=120)
    receipt = store.save_submission(
        original["job_id"], "receipt", digest="committed-content", result={"accepted": ["apple", "banana"]}
    )
    completed = store.complete(original["job_id"], now=later)
    assert completed["status"] == "completed"
    assert completed["items"] == original["items"]
    assert store.complete(original["job_id"], now=later) == completed
    assert store.release(original["job_id"], now=later) == completed
    assert store.get_submission(original["job_id"], "receipt") == receipt
    assert store.get(replacement["job_id"], now=later) == replacement
    assert store.active_words(now=later) == {"apple", "banana"}
    with sqlite3.connect(store.db_path) as connection:
        leases = connection.execute("SELECT job_id FROM authoring_leases").fetchall()
    assert leases == [(replacement["job_id"],), (replacement["job_id"],)]


def test_renew_extends_without_shortening_existing_lease(store):
    job = claim(store)
    renewed = store.renew(job["job_id"], now=NOW + timedelta(seconds=20), ttl_seconds=90)
    assert datetime.fromisoformat(renewed["expires_at"]) == NOW + timedelta(seconds=110)
    shortened = store.renew(job["job_id"], now=NOW + timedelta(seconds=30), ttl_seconds=10)
    assert shortened == renewed
    assert store.active_words(now=NOW + timedelta(seconds=100)) == {"apple", "banana"}


def test_claim_request_id_is_idempotent_across_workers_and_time(store):
    job = claim(store, request_id="request")
    other = QuestionAuthoringStore(store.db_path)
    assert other.claim(SOURCES, now=NOW + timedelta(seconds=10), ttl_seconds=60, limit=2, request_id="request") == job
    assert other.get_request("request", now=NOW) == job
    assert other.get_request("missing", now=NOW) is None
    assert other.claim(SOURCES, now=NOW + timedelta(seconds=60), ttl_seconds=60, limit=2, request_id="request")["status"] == "expired"
    assert other.active_words(now=NOW + timedelta(seconds=60)) == set()


def test_request_parameters_are_snapshotted_and_replayed(store):
    parameters = {"kind": "generation", "worker_id": "external", "level": "gaokao", "limit": 2, "ttl_seconds": 60}
    job = claim(store, request_id="parameters", request_parameters=parameters)
    reopened = QuestionAuthoringStore(store.db_path)
    assert job["request_parameters"] == parameters
    assert reopened.get_request("parameters", now=NOW) == job
    assert reopened.get(job["job_id"], now=NOW) == job
    reordered = dict(reversed(list(parameters.items())))
    assert claim(reopened, request_id="parameters", request_parameters=reordered) == job
    parameters["level"] = "cet6"
    assert reopened.get_request("parameters", now=NOW)["request_parameters"]["level"] == "gaokao"
    with pytest.raises(ValueError, match="different claim parameters"):
        claim(reopened, request_id="parameters", request_parameters=parameters)
    with pytest.raises(ValueError, match="different claim parameters"):
        claim(reopened, request_id="parameters")
    assert source_items(reopened.get(job["job_id"], now=NOW)) == SOURCES


def test_legacy_schema_adds_request_parameters_without_rewriting_job(store):
    digest = hashlib.sha256(json.dumps(
        {"sources": SOURCES, "ttl_seconds": 60, "limit": 2, "kind": "generation", "worker_id": "external"},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    items = [{**source, "item_id": f"item-{index}"} for index, source in enumerate(SOURCES)]
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            """CREATE TABLE authoring_jobs (
                job_id TEXT PRIMARY KEY, kind TEXT NOT NULL, worker_id TEXT NOT NULL,
                request_id TEXT UNIQUE, request_digest TEXT NOT NULL,
                created_at TEXT NOT NULL, expires_at TEXT NOT NULL, status TEXT NOT NULL,
                items_json TEXT NOT NULL
            )"""
        )
        connection.execute(
            "INSERT INTO authoring_jobs VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy", "generation", "external", "old-request", digest,
                NOW.isoformat(timespec="microseconds"),
                (NOW + timedelta(seconds=60)).isoformat(timespec="microseconds"),
                "active", json.dumps(items),
            ),
        )
        before = connection.execute("SELECT * FROM authoring_jobs").fetchone()
    barrier = Barrier(2)

    def read(worker):
        barrier.wait()
        return worker.get_request("old-request", now=NOW)

    with ThreadPoolExecutor(max_workers=2) as executor:
        jobs = list(executor.map(read, [store, QuestionAuthoringStore(store.db_path)]))
    assert jobs[0] == jobs[1]
    assert jobs[0]["request_parameters"] is None
    assert jobs[0]["items"] == items
    assert claim(store, request_id="old-request") == jobs[0]
    with sqlite3.connect(store.db_path) as connection:
        after = connection.execute("SELECT * FROM authoring_jobs").fetchone()
    assert after[:-1] == before
    assert after[-1] == "null"


@pytest.mark.parametrize(
    "changed",
    [
        {"sources": SOURCES[:1]},
        {"sources": [{"english": "apple", "source_hash": "new"}, SOURCES[1]]},
        {"ttl_seconds": 120},
        {"limit": 1},
        {"kind": "context_blind"},
        {"worker_id": "different-worker"},
    ],
)
def test_conflicting_claim_request_does_not_mutate_original_job(store, changed):
    job = claim(store, request_id="request")
    args = {"sources": SOURCES, "now": NOW, "ttl_seconds": 60, "limit": 2, "request_id": "request", **changed}
    with pytest.raises(ValueError, match="different claim parameters"):
        store.claim(**args)
    assert store.get(job["job_id"], now=NOW) == job
    assert store.active_words(now=NOW) == {"apple", "banana"}


def test_simultaneous_idempotent_claim_returns_same_job(store):
    barrier = Barrier(2)

    def run(worker):
        barrier.wait()
        return claim(worker, request_id="same-request")

    with ThreadPoolExecutor(max_workers=2) as executor:
        jobs = list(executor.map(run, [store, QuestionAuthoringStore(store.db_path)]))
    assert jobs[0] == jobs[1]
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM authoring_jobs").fetchone()[0] == 1


def test_independent_stage_claims_are_partitioned_and_completed_jobs_release_leases(store):
    generation = claim(store)
    audit = claim(store, kind="context_blind", worker_id="auditor")
    assert source_items(audit) == SOURCES
    assert audit["kind"] == "context_blind"
    assert audit["worker_id"] == "auditor"
    assert store.active_words(now=NOW, kind="context_blind") == {"apple", "banana"}
    assert store.active_words(now=NOW, kind="recognition_blind") == set()
    completed = store.complete(audit["job_id"], now=NOW)
    assert completed["status"] == "completed"
    assert store.complete(audit["job_id"], now=NOW) == completed
    assert store.release(audit["job_id"], now=NOW) == completed
    assert store.active_words(now=NOW, kind="context_blind") == set()
    assert store.get(generation["job_id"], now=NOW)["status"] == "active"
    assert store.active_words(now=NOW) == {"apple", "banana"}
    with pytest.raises(ValueError, match="active"):
        store.renew(audit["job_id"], now=NOW, ttl_seconds=60)


def test_receipts_are_immutable_idempotent_and_scoped_to_job(store):
    job = claim(store)
    assert store.get_submission(job["job_id"], "receipt") is None
    receipt = store.save_submission(job["job_id"], "receipt", digest="first", result={"accepted": ["apple"]})
    assert QuestionAuthoringStore(store.db_path).get_submission(job["job_id"], "receipt") == receipt
    assert store.save_submission(job["job_id"], "receipt", digest="first", result={"accepted": []}) == receipt
    with pytest.raises(ValueError, match="different digest"):
        store.save_submission(job["job_id"], "receipt", digest="other", result={})
    assert store.get_submission(job["job_id"], "receipt") == receipt
    another = claim(store)
    assert store.save_submission(another["job_id"], "receipt", digest="other", result={}) == {"digest": "other", "result": {}}
    with pytest.raises(KeyError):
        store.save_submission("missing", "receipt", digest="first", result={})


def test_claim_rolls_back_both_job_and_leases_after_partial_insert(store):
    store.active_words(now=NOW)
    with sqlite3.connect(store.db_path) as connection:
        connection.executescript(
            """CREATE TRIGGER reject_banana BEFORE INSERT ON authoring_leases
               WHEN NEW.word_key='banana'
               BEGIN SELECT RAISE(ABORT, 'test write failure'); END;"""
        )
    with pytest.raises(sqlite3.IntegrityError, match="test write failure"):
        claim(store, request_id="atomic")
    assert store.active_words(now=NOW) == set()
    assert store.get_request("atomic", now=NOW) is None
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM authoring_jobs").fetchone()[0] == 0


def test_dates_are_normalized_to_utc_without_losing_subseconds(store):
    local = NOW.astimezone(timezone(timedelta(hours=8))).replace(microsecond=123456)
    job = store.claim(SOURCES, now=local, ttl_seconds=60, limit=2)
    assert job["created_at"].endswith(".123456+00:00")
    assert store.get(job["job_id"], now=local + timedelta(seconds=60, microseconds=-1))["status"] == "active"
    assert store.get(job["job_id"], now=local + timedelta(seconds=60))["status"] == "expired"
    with pytest.raises(ValueError, match="timezone-aware"):
        store.active_words(now=NOW.replace(tzinfo=None))


@pytest.mark.parametrize("value", [0, -1, 1.5, True, "1"])
def test_invalid_limits_are_rejected_without_creating_database(store, value):
    with pytest.raises(ValueError, match="positive integer"):
        store.claim(SOURCES, now=NOW, ttl_seconds=60, limit=value)
    assert not store.db_path.exists()


def test_invalid_sources_are_rejected_without_creating_database(store):
    for sources in ([{}], [{"english": 12}], [{"english": " "}], SOURCES + [{"english": "APPLE", "source_hash": "different"}]):
        with pytest.raises(ValueError):
            store.claim(sources, now=NOW, ttl_seconds=60, limit=2)
    assert not store.db_path.exists()

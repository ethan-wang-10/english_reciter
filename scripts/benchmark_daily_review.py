#!/usr/bin/env python3
"""Benchmark isolated daily review functions and authenticated Flask routes.

All accounts, sessions, rewards, learning databases and logs are temporary.
Run each source version in a fresh process; --code-overlay can select a saved
copy of the changed modules while other imports come from --root.
"""

from __future__ import annotations

import argparse
from datetime import timedelta
import gzip
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import platform
import statistics
import sys
import tempfile
from time import perf_counter


def timings(callback, repeats, validate=None):
    samples = []
    for _ in range(repeats):
        started = perf_counter()
        result = callback()
        samples.append((perf_counter() - started) * 1000)
        if validate:
            validate(result)
    ordered = sorted(samples)
    return {
        "samples": len(samples),
        "p50_ms": round(statistics.median(samples), 3),
        "p95_ms": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 3),
        "min_ms": round(ordered[0], 3),
        "max_ms": round(ordered[-1], 3),
        "samples_ms": [round(value, 3) for value in samples],
    }


def word_name(index):
    suffix = ""
    while True:
        index, remainder = divmod(index, 26)
        suffix = chr(ord("a") + remainder) + suffix
        if not index:
            return "benchword" + suffix.rjust(4, "a")


def seed_reciter(core, config, word_count, task_count, history_events):
    from review_scheduler import normalize_review_state

    config.BACKUP_ENABLED = config.TTS_ENABLED = False
    # A persisted 1,000-item task tests the stated workload without changing
    # the application's current 300-item settings limit.
    config.DAILY_REVIEW_LIMIT = task_count
    instance = core.WordReciter(config)
    states = {}
    pending_count = word_count // 2
    for index in range(word_count):
        mastered = index >= pending_count
        word = core.Word(
            english=word_name(index),
            chinese="synthetic definition",
            example="A synthetic example for isolated benchmarking.",
            success_count=8 if mastered else 1,
            review_count=8 if mastered else 2,
            next_review_date=instance.today + timedelta(days=30 if mastered else 0),
        )
        (instance.mastered_words if mastered else instance.all_words).append(word)
        state = normalize_review_state(
            None,
            success_count=word.success_count,
            max_success_count=config.MAX_SUCCESS_COUNT,
            legacy_interval_days=60 if mastered else 1,
            legacy_active=mastered,
        )
        event_ids = [f"seed-{index}-{event}" for event in range(history_events)]
        state["recent_event_ids"] = event_ids
        state["recent_event_fingerprints"] = {
            event_id: hashlib.sha256(event_id.encode()).hexdigest()
            for event_id in event_ids
        }
        state["recent_event_results"] = {
            event_id: {
                "recorded": True,
                "message": "Synthetic persisted answer result",
                "attempt_number": 1,
                "attempt_limit": 3,
                "correct": True,
                "mastered_now": False,
                "old_success_count": 0,
                "new_success_count": 1,
                "final_attempt": False,
                "remedial": False,
            }
            for event_id in event_ids
        }
        states[instance.word_state_key(word)] = state
    instance.learning_state_v2["review_states"] = states
    started = perf_counter()
    bundle = instance.get_today_learning_plan(listening_available=False)
    first_plan_ms = (perf_counter() - started) * 1000
    assert len(bundle["words"]) == task_count, "Synthetic task size changed"
    # Exercise persistence and task access without invoking question generation,
    # semantic approval data, NLP, TTS or any other external service.
    for item in instance.learning_state_v2["daily_task"]["items"]:
        item["exercise_type"] = "spelling"
    instance.save_learning_data(backup=False)
    return instance, first_plan_ms


def function_benchmark(core, directory, word_count, task_count, repeats, history_events):
    config = core.Config(str(directory / "absent-config.json"))
    config.DATA_FILE = str(directory / "learning.json")
    config.LEARNING_DB_FILE = str(directory / "learning.sqlite3")
    config.EXAMPLE_DB = str(directory / "examples.json")
    instance, first_plan_ms = seed_reciter(
        core, config, word_count, task_count, history_events,
    )
    instance.get_today_learning_plan(listening_available=False)

    def change_and_save():
        word = instance.all_words[0]
        word.review_count += 1
        state = instance.get_review_state(word)
        state["mastery"]["spelling"]["attempts"] += 1
        instance.save_learning_data(backup=False)

    return {
        "first_plan_ms": round(first_plan_ms, 3),
        "warm_plan": timings(
            lambda: instance.get_today_learning_plan(listening_available=False), repeats,
        ),
        "count_due": timings(instance.count_due_words, repeats),
        "unchanged_save": timings(
            lambda: instance.save_learning_data(backup=False), repeats,
        ),
        "one_word_save": timings(change_and_save, repeats),
        "cold_store_load": timings(instance.repository._sqlite_store().load, repeats),
    }


def json_response(response):
    if response.status_code != 200:
        raise AssertionError(
            f"Benchmark route returned {response.status_code}: "
            f"{response.get_data(as_text=True)[:200]}"
        )
    data = response.get_data()
    if response.headers.get("Content-Encoding") == "gzip":
        data = gzip.decompress(data)
    return json.loads(data)


def http_benchmark(web, core, username, word_count, task_count, repeats, history_events):
    web._write_user_learning_settings(username, {"daily_review_limit": task_count})
    config = web._build_user_config(username)
    user_config = web.DATA_DIR / username / "config.json"
    user_config.parent.mkdir(parents=True, exist_ok=True)
    user_config.write_text(
        json.dumps({"backup_enabled": False, "tts_enabled": False}), encoding="utf-8",
    )
    instance, _ = seed_reciter(core, config, word_count, task_count, history_events)
    del instance
    token = web.create_token(username)
    headers = {"Authorization": "Bearer " + token, "Accept-Encoding": "gzip"}
    cached_rows = []
    response_bytes = []
    with web.app.test_client() as client:
        assert client.get("/api/words/review").status_code == 401

        def read_review():
            return client.get("/api/words/review", headers=headers)

        def validate_review(response):
            body = json_response(response)
            assert body["count"] == task_count
            assert len(body["words"]) == task_count
            assert all(row["task_id"] and row["task_item_id"] for row in body["words"])
            cached_rows[:] = body["words"]
            response_bytes.append(len(response.get_data()))

        validate_review(read_review())
        get_metrics = timings(read_review, repeats, validate_review)
        rows = cached_rows[:repeats + 1]
        submitted = []
        submitted_results = []

        def answer_next():
            row = rows[len(submitted)]
            body = {
                "word_id": row["english"],
                "answer": row["english"],
                "task_id": row["task_id"],
                "task_item_id": row["task_item_id"],
                "exercise_type": "spelling",
                "review_event_id": f"benchmark-answer-{len(submitted)}",
                "attempt_number": 1,
                "elapsed_ms": 1200,
                "hint_count": 0,
            }
            return client.post("/api/words/practice", headers=headers, json=body), body

        def validate_answer(result):
            response, body = result
            answer = json_response(response)
            assert answer["correct"] is True and answer["recorded"] is True
            assert answer["word"]["english"] == body["word_id"]
            assert answer["task_progress"]["completed"] == len(submitted) + 1
            submitted.append(body)
            submitted_results.append(answer)

        validate_answer(answer_next())
        post_metrics = timings(answer_next, repeats, validate_answer)
        last_body = submitted[-1]

        def replay_last():
            return client.post("/api/words/practice", headers=headers, json=last_body)

        def validate_replay(response):
            replay = json_response(response)
            assert replay == submitted_results[-1]
            assert replay["task_progress"]["completed"] == len(submitted)

        replay_metrics = timings(replay_last, repeats, validate_replay)
        web._invalidate_user_reciter_cache(username)
        persisted = web._build_user_reciter(username)
        assert persisted.daily_task_progress()["completed"] == len(submitted)
        for body in submitted:
            word = persisted.find_word(body["word_id"])
            assert word is not None and word.review_count == 3
            state = persisted.get_review_state(word)
            assert body["review_event_id"] in state["recent_event_ids"]
        assert len(persisted.all_words) + len(persisted.mastered_words) == word_count
        web._invalidate_user_reciter_cache(username)
    return {
        "review_get": get_metrics,
        "answer_post": post_metrics,
        "answer_replay_post": replay_metrics,
        "review_gzip_bytes": max(response_bytes),
        "durable_answers_verified": len(submitted),
        "idempotent_replay_verified": True,
    }


def parse_arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--code-overlay", type=Path)
    parser.add_argument("--cases", nargs="+", default=["1000:100", "2000:100", "2000:1000"])
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--history-events", type=int, default=20)
    parser.add_argument("--functions-only", action="store_true")
    parser.add_argument("--temp-root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 31:
        parser.error("--repeats must be between 1 and 31")
    if not 0 <= args.history_events <= 100:
        parser.error("--history-events must be between 0 and 100")
    cases = []
    for value in args.cases:
        try:
            word_count, task_count = map(int, value.split(":"))
        except ValueError:
            parser.error("--cases must contain total_words:daily_task_words pairs")
        if not (1 <= word_count <= 10000 and args.repeats + 1 <= task_count <= 1000):
            parser.error("Each case needs up to 10,000 total words and repeats+1 to 1,000 tasks")
        if task_count > word_count // 2:
            parser.error("Each case needs at least twice as many total words as daily tasks")
        cases.append((word_count, task_count))
    args.cases = cases
    for name in ("root", "code_overlay", "temp_root", "output"):
        value = getattr(args, name)
        if value is not None:
            setattr(args, name, value.resolve())
    return args


def main():
    args = parse_arguments()
    sys.path.insert(0, str(args.root))
    if args.code_overlay:
        sys.path.insert(0, str(args.code_overlay))
    if args.temp_root:
        args.temp_root.mkdir(parents=True, exist_ok=True)
    original_directory = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="daily-review-benchmark-", dir=args.temp_root) as temporary:
        directory = Path(temporary).resolve()
        os.chdir(directory)
        os.environ.update({
            "ENGLISH_RECITER_DATA_DIR": str(directory / "web-data"),
            "GAOKAO_AUTO_BACKFILL_ENABLED": "false",
            "IMPORT_JOBS_ASYNC": "false",
            "PERFORMANCE_ENABLED": "false",
            "FLASK_ENV": "testing",
            "SECRET_KEY": "isolated-synthetic-benchmark",
        })
        logging.disable(logging.CRITICAL)
        try:
            import reciter as core

            web = None
            if not args.functions_only:
                import simple_web_app as web

                web.app.config.update(TESTING=True)
                web._reliable_listening_available = lambda: False

                def seed_accounts(users):
                    for index in range(len(args.cases)):
                        users[f"benchmark_{index}"] = {
                            "enabled": True, "password_hash": "unused-benchmark-password",
                        }

                web.mutate_users(seed_accounts)
            report = {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "scope": (
                    "Synthetic sequential warm-cache functions and in-process Flask test-client "
                    "HTTP routes; real authentication, user locks, persistence and rewards; "
                    "gzip review responses; no TCP/TLS/network latency or concurrent load. "
                    "All daily exercises use spelling. Sample p95 is not a production SLO."
                ),
                "history_events_per_word": args.history_events,
                "pending_share": 0.5,
                "mastered_due_after_days": 30,
                "source_sha256": {},
                "results": [],
            }
            for module_name in ("reciter", "learning_sqlite_store", "web_learning_routes", "simple_web_app"):
                module = sys.modules.get(module_name)
                if module and getattr(module, "__file__", None):
                    report["source_sha256"][module_name] = hashlib.sha256(
                        Path(module.__file__).read_bytes(),
                    ).hexdigest()
            for index, (word_count, task_count) in enumerate(args.cases):
                function_directory = directory / f"functions-{index}"
                function_directory.mkdir()
                result = {
                    "total_words": word_count,
                    "daily_task_words": task_count,
                    "functions": function_benchmark(
                        core, function_directory, word_count, task_count,
                        args.repeats, args.history_events,
                    ),
                }
                if web:
                    result["http"] = http_benchmark(
                        web, core, f"benchmark_{index}", word_count, task_count,
                        args.repeats, args.history_events,
                    )
                report["results"].append(result)
                print(json.dumps({"case": result}), flush=True)
        finally:
            if "web" in locals() and web:
                web._atexit_close_app_sqlite()
            logging.shutdown()
            os.chdir(original_directory)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": report}), flush=True)


if __name__ == "__main__":
    main()

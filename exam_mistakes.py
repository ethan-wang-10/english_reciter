"""Durable mistake snapshots shared by normal exams and focused reviews."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import unicodedata

from app_time import china_now_iso


MIGRATION = "submitted-attempts-v1"


def _text(value: object) -> str:
    value = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(value.replace("’", "'").replace("‘", "'").casefold().split())


def question_key(question: dict) -> str:
    """Identify semantics independently of attempt IDs and choice order/labels."""
    kind = question["type"]
    semantic = {"type": kind, "prompt": _text(question["prompt"])}
    if kind == 'listening_choice':
        semantic['listening_text'] = _text(question['listening_text'])
    if kind in ("translation", "fill_blank", "rewrite"):
        semantic["answers"] = sorted({
            _text(answer).rstrip(".!?。！？ ") for answer in question["accepted_answers"]
        })
    elif kind == "spelling":
        semantic["answer"] = _text(question["answer"])
    else:
        options = question["options"]
        semantic["options"] = sorted(_text(option["text"]) for option in options)
        semantic["answer"] = next(
            _text(option["text"]) for option in options if option["id"] == question["answer"]
        )
    encoded = json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def ensure_schema(connection: sqlite3.Connection) -> None:
    # Do not use executescript: submissions update these rows inside their own transaction.
    for statement in (
        """CREATE TABLE IF NOT EXISTS exam_mistakes (
               paper_id TEXT NOT NULL,
               question_key TEXT NOT NULL,
               question_json TEXT NOT NULL,
               wrong_count INTEGER NOT NULL,
               pending INTEGER NOT NULL CHECK (pending IN (0, 1)),
               first_wrong_at TEXT NOT NULL,
               last_wrong_at TEXT NOT NULL,
               last_answered_at TEXT NOT NULL,
               PRIMARY KEY(paper_id, question_key)
           )""",
        "CREATE INDEX IF NOT EXISTS exam_mistakes_pending ON exam_mistakes(paper_id, pending)",
        """CREATE TABLE IF NOT EXISTS exam_mistake_migrations (
               name TEXT PRIMARY KEY,
               completed_at TEXT NOT NULL
           )""",
        """CREATE TABLE IF NOT EXISTS exam_mistake_reviews (
               attempt_id TEXT PRIMARY KEY,
               paper_id TEXT NOT NULL
           )""",
    ):
        connection.execute(statement)


def record_submission(connection: sqlite3.Connection, attempt: dict, result: dict) -> None:
    """Called exactly once per submitted attempt, under the submission write lock."""
    outcomes = {row["id"]: row["correct"] for row in result["questions"]}
    paper_id = attempt["paper_id"]
    answered_at = result["submitted_at"]
    grouped = {}
    for question in attempt["questions"]:
        if question["id"] not in outcomes:
            continue
        key = question_key(question)
        item = grouped.setdefault(key, {"question": question, "wrong_count": 0})
        if outcomes[question["id"]] is not True:
            item["question"] = question
            item["wrong_count"] += 1
    for key, item in grouped.items():
        # A duplicated sentence can occur more than once in a paper. Any wrong
        # answer keeps its semantic question pending, regardless of shuffled order.
        if item["wrong_count"] == 0:
            connection.execute(
                "UPDATE exam_mistakes SET pending=0, last_answered_at=? WHERE paper_id=? AND question_key=?",
                (answered_at, paper_id, key),
            )
        else:
            connection.execute(
                """INSERT INTO exam_mistakes(
                       paper_id, question_key, question_json, wrong_count, pending,
                       first_wrong_at, last_wrong_at, last_answered_at
                   ) VALUES (?, ?, ?, ?, 1, ?, ?, ?)
                   ON CONFLICT(paper_id, question_key) DO UPDATE SET
                       question_json=excluded.question_json,
                       wrong_count=exam_mistakes.wrong_count+excluded.wrong_count,
                       pending=1,
                       last_wrong_at=excluded.last_wrong_at,
                       last_answered_at=excluded.last_answered_at""",
                (paper_id, key, json.dumps(item["question"], ensure_ascii=False), item["wrong_count"],
                 answered_at, answered_at, answered_at),
            )


def ensure_migrated(connection: sqlite3.Connection) -> None:
    if connection.execute("SELECT 1 FROM exam_mistake_migrations WHERE name=?", (MIGRATION,)).fetchone():
        return
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        # Another worker may have completed migration while this one waited for its lock.
        if connection.execute("SELECT 1 FROM exam_mistake_migrations WHERE name=?", (MIGRATION,)).fetchone():
            return
        for row in connection.execute(
            "SELECT id, attempt_json, result_json FROM exam_attempts WHERE result_json IS NOT NULL "
            "ORDER BY submitted_at, rowid"
        ):
            attempt = json.loads(row["attempt_json"])
            result = json.loads(row["result_json"])
            if attempt.get("mode") == "wrong_review":
                connection.execute(
                    "INSERT OR IGNORE INTO exam_mistake_reviews(attempt_id, paper_id) VALUES (?, ?)",
                    (row["id"], attempt["paper_id"]),
                )
            record_submission(connection, attempt, result)
        connection.execute(
            "INSERT INTO exam_mistake_migrations(name, completed_at) VALUES (?, ?)",
            (MIGRATION, china_now_iso()),
        )


def pending_count(connection: sqlite3.Connection, paper_id: str) -> int:
    return connection.execute(
        "SELECT COUNT(*) FROM exam_mistakes WHERE paper_id=? AND pending=1", (paper_id,),
    ).fetchone()[0]


def pending_questions(connection: sqlite3.Connection, paper_id: str) -> list[dict]:
    return [json.loads(row["question_json"]) for row in connection.execute(
        "SELECT question_json FROM exam_mistakes WHERE paper_id=? AND pending=1 ORDER BY first_wrong_at, rowid",
        (paper_id,),
    )]

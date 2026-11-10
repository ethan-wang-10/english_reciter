"""Private exam papers and attempts, independent of daily learning progress."""

from __future__ import annotations

import json
import random
import re
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from app_time import china_now_iso
from wordbank_v2 import normalize_english_key


MAX_EXAM_WORDS = 300
MAX_PAPER_NAME = 80
MAX_WORD_TEXT = 20000
CHOICE_TYPES = ("recognition", "context")


class ExamPreparationError(ValueError):
    def __init__(self, message: str, status: int = 400, **details):
        super().__init__(message)
        self.status = status
        self.details = details


def normalize_answer(value: str) -> str:
    return normalize_english_key(value).replace("’", "'").replace("‘", "'")


def parse_exam_words(text: str) -> tuple[list[str], int]:
    if not isinstance(text, str) or len(text) > MAX_WORD_TEXT:
        raise ExamPreparationError(f"单词内容不能超过 {MAX_WORD_TEXT} 个字符")
    words = []
    seen = set()
    duplicates = 0
    for raw in re.split(r"[,，、;；\r\n]+", text):
        word = normalize_answer(raw)
        if not word:
            continue
        if len(word) > 120 or not re.fullmatch(r"[a-z0-9 .'-]+", word) or not re.search(r"[a-z]", word):
            raise ExamPreparationError(f"请填写英文单词或短语，并用逗号分隔：{raw.strip()[:60]}")
        if word in seen:
            duplicates += 1
            continue
        words.append(word)
        seen.add(word)
    if not words:
        raise ExamPreparationError("请先输入本次考试的单词")
    if len(words) > MAX_EXAM_WORDS:
        raise ExamPreparationError(f"每张考卷最多包含 {MAX_EXAM_WORDS} 个不同的单词")
    return words, duplicates


def build_exam_questions(words: list[dict], include_choices: bool, question_lookup) -> tuple[list[dict], int]:
    """Keep all spelling questions before choices so their prompts cannot reveal spelling answers."""
    spelling = []
    choices = []
    for word in words:
        spelling.append({
            "id": uuid.uuid4().hex,
            "type": "spelling",
            "prompt": word["chinese"],
            "english": word["english"],
            "chinese": word["chinese"],
            "answer": word["english"],
        })
        if include_choices:
            for kind in CHOICE_TYPES:
                question = question_lookup(word["english"], kind)
                if question:
                    choices.append({
                        "id": uuid.uuid4().hex,
                        "type": kind,
                        "prompt": question["prompt"],
                        "options": question["options"],
                        "english": word["english"],
                        "chinese": word["chinese"],
                        "answer": question["answer_option_id"],
                        "explanation": question.get("explanation_zh", ""),
                        "translation": question.get("translation_zh", ""),
                    })
    random.SystemRandom().shuffle(spelling)
    random.SystemRandom().shuffle(choices)
    return spelling + choices, len(choices)


def public_attempt(attempt: dict) -> dict:
    return {
        key: value for key, value in attempt.items() if key != "questions"
    } | {
        "questions": [
            {key: question[key] for key in ("id", "type", "prompt", "options") if key in question}
            for question in attempt["questions"]
        ],
    }


def grade_attempt(attempt: dict, answers: object) -> dict:
    if not isinstance(answers, dict):
        raise ExamPreparationError("作答内容必须是题目编号与答案的对应表")
    questions = attempt["questions"]
    question_ids = {question["id"] for question in questions}
    if set(answers) - question_ids:
        raise ExamPreparationError("作答中包含不属于本次考试的题目")
    rows = []
    for question in questions:
        answer = answers.get(question["id"], "")
        if not isinstance(answer, str) or len(answer) > 200:
            raise ExamPreparationError("答案格式不正确或长度超过限制")
        answer = answer.strip()
        if question["type"] == "spelling":
            correct = normalize_answer(answer) == normalize_answer(question["answer"])
            correct_answer = question["answer"]
            user_answer = answer
        else:
            options = {option["id"]: option["text"] for option in question["options"]}
            if answer and answer not in options:
                raise ExamPreparationError("请选择本次题目提供的选项")
            correct = answer == question["answer"]
            correct_answer = options[question["answer"]]
            user_answer = options.get(answer, "")
        rows.append({
            "id": question["id"],
            "type": question["type"],
            "prompt": question["prompt"],
            "english": question["english"],
            "chinese": question["chinese"],
            "user_answer": user_answer,
            "correct_answer": correct_answer,
            "correct": correct,
            "explanation": question.get("explanation", ""),
            "translation": question.get("translation", ""),
        })
    correct_count = sum(row["correct"] for row in rows)
    return {
        "attempt_id": attempt["id"],
        "paper_id": attempt["paper_id"],
        "paper_name": attempt["paper_name"],
        "total": len(rows),
        "correct_count": correct_count,
        "wrong_count": len(rows) - correct_count,
        "score": round(correct_count * 100 / len(rows), 1),
        "submitted_at": china_now_iso(),
        "questions": rows,
    }


class ExamPreparationStore:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def _connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS exam_papers (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    words_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS exam_attempts (
                    id TEXT PRIMARY KEY,
                    paper_id TEXT NOT NULL,
                    attempt_json TEXT NOT NULL,
                    result_json TEXT,
                    score REAL,
                    started_at TEXT NOT NULL,
                    submitted_at TEXT
                );
                CREATE INDEX IF NOT EXISTS exam_attempts_paper ON exam_attempts(paper_id, submitted_at);
            """)
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _paper(row) -> dict:
        words = json.loads(row["words_json"])
        return {
            "id": row["id"], "name": row["name"], "created_at": row["created_at"],
            "words": words, "word_count": len(words),
        }

    def create_paper(self, name: str, words: list[dict]) -> dict:
        paper_id = uuid.uuid4().hex
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO exam_papers VALUES (?, ?, ?, ?)",
                (paper_id, name, json.dumps(words, ensure_ascii=False), china_now_iso()),
            )
            return self._paper(connection.execute("SELECT * FROM exam_papers WHERE id=?", (paper_id,)).fetchone())

    def get_paper(self, paper_id: str) -> dict:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM exam_papers WHERE id=?", (paper_id,)).fetchone()
            if row is None:
                raise ExamPreparationError("考卷不存在，请重新选择", 404)
            return self._paper(row)

    def list_papers(self) -> list[dict]:
        with self._connection() as connection:
            papers = []
            for row in connection.execute("SELECT * FROM exam_papers ORDER BY created_at DESC, rowid DESC"):
                paper = self._paper(row)
                results = connection.execute(
                    "SELECT score FROM exam_attempts WHERE paper_id=? AND result_json IS NOT NULL "
                    "ORDER BY submitted_at DESC, rowid DESC", (paper["id"],),
                )
                scores = [result["score"] for result in results]
                paper["attempt_count"] = len(scores)
                paper["last_score"] = scores[0] if scores else None
                paper["best_score"] = max(scores) if scores else None
                papers.append(paper)
            return papers

    def start_attempt(self, paper: dict, include_choices: bool, question_lookup) -> dict:
        questions, choice_count = build_exam_questions(paper["words"], include_choices, question_lookup)
        attempt = {
            "id": uuid.uuid4().hex,
            "paper_id": paper["id"], "paper_name": paper["name"],
            "include_choices": include_choices, "choice_count": choice_count,
            "word_count": paper["word_count"], "questions": questions,
            "started_at": china_now_iso(),
        }
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO exam_attempts(id, paper_id, attempt_json, started_at) VALUES (?, ?, ?, ?)",
                (attempt["id"], paper["id"], json.dumps(attempt, ensure_ascii=False), attempt["started_at"]),
            )
        return public_attempt(attempt)

    def get_attempt(self, attempt_id: str) -> dict:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM exam_attempts WHERE id=?", (attempt_id,)).fetchone()
            if row is None:
                raise ExamPreparationError("考试记录不存在，请重新开始", 404)
            return {
                "attempt": public_attempt(json.loads(row["attempt_json"])),
                "result": json.loads(row["result_json"]) if row["result_json"] else None,
            }

    def submit_attempt(self, attempt_id: str, answers: object) -> dict:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM exam_attempts WHERE id=?", (attempt_id,)).fetchone()
            if row is None:
                raise ExamPreparationError("考试记录不存在，请重新开始", 404)
            if row["result_json"]:
                return json.loads(row["result_json"])
            result = grade_attempt(json.loads(row["attempt_json"]), answers)
            connection.execute(
                "UPDATE exam_attempts SET result_json=?, score=?, submitted_at=? WHERE id=?",
                (json.dumps(result, ensure_ascii=False), result["score"], result["submitted_at"], attempt_id),
            )
            return result

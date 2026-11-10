"""Private exam papers and attempts, independent of daily learning progress."""

from __future__ import annotations

import json
import random
import re
import sqlite3
import unicodedata
import uuid
from contextlib import contextmanager
from pathlib import Path

from app_time import china_now_iso
import exam_mistakes
from wordbank_v2 import normalize_english_key


MAX_EXAM_WORDS = 300
MAX_PAPER_NAME = 80
MAX_WORD_TEXT = 20000
MAX_EXAM_SENTENCES = 100
MAX_SENTENCE_TEXT = 500
MAX_SENTENCE_ANSWERS = 10
CHOICE_TYPES = ("recognition", "context")


class ExamPreparationError(ValueError):
    def __init__(self, message: str, status: int = 400, **details):
        super().__init__(message)
        self.status = status
        self.details = details


def normalize_answer(value: str) -> str:
    return normalize_english_key(value).replace("’", "'").replace("‘", "'")


def parse_exam_words(text: str, *, allow_empty: bool = False) -> tuple[list[str], int]:
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
    if not words and not allow_empty:
        raise ExamPreparationError("请先输入本次考试的单词")
    if len(words) > MAX_EXAM_WORDS:
        raise ExamPreparationError(f"每张考卷最多包含 {MAX_EXAM_WORDS} 个不同的单词")
    return words, duplicates


def normalize_sentence_answer(value: str) -> str:
    value = normalize_answer(unicodedata.normalize("NFKC", value))
    return value.rstrip(".!?。！？ ")


def parse_exam_sentences(value: object) -> list[dict]:
    if not isinstance(value, list) or len(value) > MAX_EXAM_SENTENCES:
        raise ExamPreparationError(f"句子题必须为列表，每张考卷最多 {MAX_EXAM_SENTENCES} 道")
    sentences = []
    for index, item in enumerate(value, 1):
        if not isinstance(item, dict):
            raise ExamPreparationError(f"第 {index} 道句子题格式不正确")
        chinese, answers = item.get("chinese"), item.get("answers")
        if not isinstance(chinese, str) or not chinese.strip() or len(chinese) > MAX_SENTENCE_TEXT:
            raise ExamPreparationError(f"第 {index} 道句子题请填写中文题干，最多 {MAX_SENTENCE_TEXT} 个字符")
        if not isinstance(answers, list) or not 1 <= len(answers) <= MAX_SENTENCE_ANSWERS:
            raise ExamPreparationError(f"第 {index} 道句子题请填写 1～{MAX_SENTENCE_ANSWERS} 个英文参考答案")
        unique_answers = {}
        for answer in answers:
            if (not isinstance(answer, str) or not answer.strip() or len(answer) > MAX_SENTENCE_TEXT
                    or not re.search(r"[a-zA-Z]", answer) or re.search(r"[\u3400-\u9fff]", answer)):
                raise ExamPreparationError(f"第 {index} 道句子题请填写英文参考答案，每个最多 {MAX_SENTENCE_TEXT} 个字符")
            unique_answers.setdefault(normalize_sentence_answer(answer), answer.strip())
        sentences.append({"chinese": chinese.strip(), "answers": list(unique_answers.values())})
    return sentences


def build_exam_questions(words: list[dict], include_choices: bool, question_lookup,
                         sentences: list[dict] | None = None) -> tuple[list[dict], int]:
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
    translations = [{
        "id": uuid.uuid4().hex, "type": "translation", "prompt": sentence["chinese"],
        "chinese": sentence["chinese"], "english": sentence["answers"][0],
        "answer": sentence["answers"][0], "accepted_answers": sentence["answers"],
    } for sentence in (sentences or [])]
    random.SystemRandom().shuffle(spelling)
    random.SystemRandom().shuffle(translations)
    random.SystemRandom().shuffle(choices)
    return spelling + translations + choices, len(choices)


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
        limit = MAX_SENTENCE_TEXT if question["type"] == "translation" else 200
        if not isinstance(answer, str) or len(answer) > limit:
            raise ExamPreparationError("答案格式不正确或长度超过限制")
        answer = answer.strip()
        if question["type"] == "translation":
            correct = normalize_sentence_answer(answer) in {
                normalize_sentence_answer(value) for value in question["accepted_answers"]
            }
            correct_answer = " / ".join(question["accepted_answers"])
            user_answer = answer
        elif question["type"] == "spelling":
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
    result = {
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
    if attempt.get("mode") == "wrong_review":
        result["mode"] = "wrong_review"
    return result


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
                    sentences_json TEXT NOT NULL DEFAULT '[]',
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
            if "sentences_json" not in {row["name"] for row in connection.execute("PRAGMA table_info(exam_papers)")}:
                connection.execute("BEGIN IMMEDIATE")
                if "sentences_json" not in {row["name"] for row in connection.execute("PRAGMA table_info(exam_papers)")}:
                    connection.execute("ALTER TABLE exam_papers ADD COLUMN sentences_json TEXT NOT NULL DEFAULT '[]'")
                connection.commit()
            exam_mistakes.ensure_schema(connection)
            exam_mistakes.ensure_migrated(connection)
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _paper(row) -> dict:
        words = json.loads(row["words_json"])
        sentences = json.loads(row["sentences_json"])
        return {
            "id": row["id"], "name": row["name"], "created_at": row["created_at"],
            "words": words, "word_count": len(words),
            "sentences": sentences, "sentence_count": len(sentences),
        }

    def create_paper(self, name: str, words: list[dict], sentences: list[dict] | None = None) -> dict:
        paper_id = uuid.uuid4().hex
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO exam_papers(id, name, words_json, sentences_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (paper_id, name, json.dumps(words, ensure_ascii=False),
                 json.dumps(sentences or [], ensure_ascii=False), china_now_iso()),
            )
            return self._paper(connection.execute("SELECT * FROM exam_papers WHERE id=?", (paper_id,)).fetchone())

    def update_paper(self, paper_id: str, name: str, words: list[dict], sentences: list[dict]) -> dict:
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE exam_papers SET name=?, words_json=?, sentences_json=? WHERE id=?",
                (name, json.dumps(words, ensure_ascii=False), json.dumps(sentences, ensure_ascii=False), paper_id),
            )
            if not cursor.rowcount:
                raise ExamPreparationError("考卷不存在，请重新选择", 404)
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
                    "SELECT score FROM exam_attempts a WHERE paper_id=? AND result_json IS NOT NULL "
                    "AND NOT EXISTS (SELECT 1 FROM exam_mistake_reviews r WHERE r.attempt_id=a.id) "
                    "ORDER BY submitted_at DESC, a.rowid DESC", (paper["id"],),
                )
                scores = [result["score"] for result in results]
                paper["attempt_count"] = len(scores)
                paper["last_score"] = scores[0] if scores else None
                paper["best_score"] = max(scores) if scores else None
                paper["mistake_count"] = exam_mistakes.pending_count(connection, paper["id"])
                papers.append(paper)
            return papers

    def start_attempt(self, paper: dict, include_choices: bool, question_lookup) -> dict:
        questions, choice_count = build_exam_questions(paper["words"], include_choices, question_lookup, paper.get("sentences"))
        attempt = {
            "id": uuid.uuid4().hex,
            "paper_id": paper["id"], "paper_name": paper["name"],
            "include_choices": include_choices, "choice_count": choice_count,
            "word_count": paper["word_count"], "questions": questions,
            "sentence_count": paper.get("sentence_count", 0),
            "started_at": china_now_iso(),
        }
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO exam_attempts(id, paper_id, attempt_json, started_at) VALUES (?, ?, ?, ?)",
                (attempt["id"], paper["id"], json.dumps(attempt, ensure_ascii=False), attempt["started_at"]),
            )
        return public_attempt(attempt)

    def mistake_count(self, paper_id: str) -> int:
        with self._connection() as connection:
            if connection.execute("SELECT 1 FROM exam_papers WHERE id=?", (paper_id,)).fetchone() is None:
                raise ExamPreparationError("考卷不存在，请重新选择", 404)
            return exam_mistakes.pending_count(connection, paper_id)

    def start_mistake_review(self, paper_id: str) -> dict:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            paper = connection.execute("SELECT * FROM exam_papers WHERE id=?", (paper_id,)).fetchone()
            if paper is None:
                raise ExamPreparationError("考卷不存在，请重新选择", 404)
            questions = exam_mistakes.pending_questions(connection, paper_id)
            if not questions:
                raise ExamPreparationError("这张考卷暂无待复习错题", 409)
            written = []
            choices = []
            for question in questions:
                question["id"] = uuid.uuid4().hex
                (choices if question["type"] in CHOICE_TYPES else written).append(question)
            random.SystemRandom().shuffle(written)
            random.SystemRandom().shuffle(choices)
            attempt = {
                "id": uuid.uuid4().hex,
                "paper_id": paper_id, "paper_name": paper["name"],
                "mode": "wrong_review",
                "include_choices": bool(choices), "choice_count": len(choices),
                "word_count": sum(question["type"] == "spelling" for question in written),
                "sentence_count": sum(question["type"] == "translation" for question in written),
                "questions": written + choices, "started_at": china_now_iso(),
            }
            connection.execute(
                "INSERT INTO exam_attempts(id, paper_id, attempt_json, started_at) VALUES (?, ?, ?, ?)",
                (attempt["id"], paper_id, json.dumps(attempt, ensure_ascii=False), attempt["started_at"]),
            )
            connection.execute(
                "INSERT INTO exam_mistake_reviews(attempt_id, paper_id) VALUES (?, ?)",
                (attempt["id"], paper_id),
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
            attempt = json.loads(row["attempt_json"])
            result = grade_attempt(attempt, answers)
            exam_mistakes.record_submission(connection, attempt, result)
            connection.execute(
                "UPDATE exam_attempts SET result_json=?, score=?, submitted_at=? WHERE id=?",
                (json.dumps(result, ensure_ascii=False), result["score"], result["submitted_at"], attempt_id),
            )
            return result

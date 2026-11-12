"""Persistent, private mistake review across exams and saved question snapshots."""

import json
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from functools import wraps

import pytest
from flask import Flask, g, jsonify, request

import exam_mistakes
from exam_preparation import ExamPreparationError, ExamPreparationStore
from exam_preparation_routes import register_exam_preparation_routes


WORDS = [
    {"english": "apple", "chinese": "苹果"},
    {"english": "look after", "chinese": "照顾"},
]
SENTENCES = [{"chinese": "我每天步行去学校。", "answers": [
    "I walk to school every day.", "I go to school on foot every day.",
]}]


def choices(word, kind):
    if word != "apple":
        return None
    if kind == "recognition":
        return {
            "prompt": "apple", "answer_option_id": "r-good",
            "options": [{"id": "r-bad", "text": "香蕉"}, {"id": "r-good", "text": "苹果"}],
            "explanation_zh": "apple 表示苹果。",
        }
    return {
        "prompt": "She took an ____ from the bowl.", "answer_option_id": "c-good",
        "options": [{"id": "c-good", "text": "apple"}, {"id": "c-bad", "text": "banana"}],
        "translation_zh": "她从碗中拿了一个苹果。",
    }


def correct_answers(attempt):
    answers = {}
    for question in attempt["questions"]:
        kind = question["type"]
        answers[question["id"]] = (
            SENTENCES[0]["answers"][1] if kind == "translation" else
            "r-good" if kind == "recognition" else
            "c-good" if kind == "context" else
            "apple" if question["prompt"] == "苹果" else "look after"
        )
    return answers


def private_attempt(store, attempt_id):
    with sqlite3.connect(store.path) as connection:
        return json.loads(connection.execute(
            "SELECT attempt_json FROM exam_attempts WHERE id=?", (attempt_id,),
        ).fetchone()[0])


def start_exam(store, paper, include_choices, lookup=choices, sentences=SENTENCES):
    """Seed historical translation snapshots without requiring a sentence editor/schema."""
    attempt = store.start_attempt(paper, include_choices, lookup)
    snapshot = private_attempt(store, attempt["id"])
    translations = [{
        "id": uuid.uuid4().hex, "type": "translation", "prompt": sentence["chinese"],
        "chinese": sentence["chinese"], "english": sentence["answers"][0],
        "answer": sentence["answers"][0], "accepted_answers": list(sentence["answers"]),
    } for sentence in sentences]
    written = [question for question in snapshot["questions"] if question["type"] == "spelling"]
    options = [question for question in snapshot["questions"] if question["type"] != "spelling"]
    snapshot["questions"] = written + translations + options
    snapshot["sentence_count"] = len(translations)
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE exam_attempts SET attempt_json=? WHERE id=?", (json.dumps(snapshot), attempt["id"]))
    return store.get_attempt(attempt["id"])["attempt"]


def mistake_rows(store):
    with sqlite3.connect(store.path) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute("SELECT * FROM exam_mistakes")]


def clear_migration(store):
    with sqlite3.connect(store.path) as connection:
        connection.execute("DELETE FROM exam_mistakes")
        connection.execute("DELETE FROM exam_mistake_migrations")
        connection.execute("DELETE FROM exam_mistake_reviews")


@pytest.fixture
def prepared(tmp_path):
    store = ExamPreparationStore(tmp_path / "exams.sqlite3")
    paper = store.create_paper("期中考试", WORDS)
    return store, paper


def test_mistakes_persist_and_review_preserves_all_private_question_types(prepared):
    store, paper = prepared
    original = start_exam(store, paper, True)
    result = store.submit_attempt(original["id"], {})
    assert result["wrong_count"] == 5
    reopened = ExamPreparationStore(store.path)
    assert reopened.mistake_count(paper["id"]) == 5
    assert reopened.list_papers()[0]["mistake_count"] == 5

    review = reopened.start_mistake_review(paper["id"])
    assert review["mode"] == "wrong_review"
    assert (review["word_count"], review["sentence_count"], review["choice_count"]) == (2, 1, 2)
    assert all(question["type"] in ("spelling", "translation") for question in review["questions"][:3])
    assert all(question["type"] in ("recognition", "context") for question in review["questions"][3:])
    assert not ({question["id"] for question in review["questions"]} & {question["id"] for question in original["questions"]})
    for question in review["questions"]:
        assert set(question) <= {"id", "type", "prompt", "options"}
    full_review = private_attempt(store, review["id"])
    sentence = next(question for question in full_review["questions"] if question["type"] == "translation")
    assert sentence["accepted_answers"] == SENTENCES[0]["answers"]
    assert reopened.get_attempt(review["id"]) == {"attempt": review, "result": None}
    reviewed = reopened.submit_attempt(review["id"], correct_answers(review))
    assert (reviewed["score"], reviewed["mode"]) == (100, "wrong_review")
    assert reopened.mistake_count(paper["id"]) == 0
    assert all(row["pending"] == 0 for row in mistake_rows(store))
    assert reopened.get_attempt(original["id"])["result"] == result


def test_editing_paper_does_not_replace_original_mistake_snapshots(prepared):
    store, paper = prepared
    original = start_exam(store, paper, True)
    store.submit_attempt(original["id"], {})
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE exam_papers SET name=?, words_json=? WHERE id=?", (
            "新版考卷", json.dumps([{"english": "banana", "chinese": "香蕉"}]), paper["id"],
        ))
    review = store.start_mistake_review(paper["id"])
    assert review["paper_name"] == "新版考卷"
    assert {question["prompt"] for question in review["questions"]} == {question["prompt"] for question in original["questions"]}
    assert store.submit_attempt(review["id"], correct_answers(review))["score"] == 100


def test_semantic_deduplication_ignores_new_ids_and_choice_order(prepared):
    store, paper = prepared
    first = start_exam(store, paper, True)
    store.submit_attempt(first["id"], {})

    def relabeled(word, kind):
        question = choices(word, kind)
        if question is None:
            return None
        return {**question, "answer_option_id": "new-" + question["answer_option_id"],
                "options": [{**option, "id": "new-" + option["id"]} for option in reversed(question["options"])]}

    second = start_exam(store, paper, True, relabeled)
    store.submit_attempt(second["id"], {})
    assert store.mistake_count(paper["id"]) == 5
    assert {row["wrong_count"] for row in mistake_rows(store)} == {2}
    review = store.start_mistake_review(paper["id"])
    for question in review["questions"]:
        if question["type"] in ("recognition", "context"):
            assert all(option["id"].startswith("new-") for option in question["options"])


def test_mistakes_reactivate_after_a_later_wrong_answer(prepared):
    store, paper = prepared
    first = start_exam(store, paper, False)
    store.submit_attempt(first["id"], {})
    review = store.start_mistake_review(paper["id"])
    store.submit_attempt(review["id"], correct_answers(review))
    assert store.mistake_count(paper["id"]) == 0
    another = start_exam(store, paper, False)
    store.submit_attempt(another["id"], {})
    assert store.mistake_count(paper["id"]) == 3
    assert {row["wrong_count"] for row in mistake_rows(store)} == {2}


@pytest.mark.parametrize("correct_index", [0, 1])
def test_any_wrong_duplicate_sentence_stays_pending_regardless_of_order(tmp_path, correct_index):
    store = ExamPreparationStore(tmp_path / "exams.sqlite3")
    paper = store.create_paper("重复句子", [])
    attempt = start_exam(store, paper, False, sentences=SENTENCES * 2)
    store.submit_attempt(attempt["id"], {attempt["questions"][correct_index]["id"]: SENTENCES[0]["answers"][0]})
    assert store.mistake_count(paper["id"]) == 1
    assert [row["wrong_count"] for row in mistake_rows(store)] == [1]
    review = store.start_mistake_review(paper["id"])
    assert len(review["questions"]) == 1
    store.submit_attempt(review["id"], correct_answers(review))
    assert store.mistake_count(paper["id"]) == 0


def test_repeated_and_concurrent_submission_do_not_double_count(prepared):
    store, paper = prepared
    attempt = start_exam(store, paper, True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: ExamPreparationStore(store.path).submit_attempt(attempt["id"], {}), range(2)))
    assert results[0] == results[1]
    assert store.submit_attempt(attempt["id"], correct_answers(attempt)) == results[0]
    assert store.mistake_count(paper["id"]) == 5
    assert {row["wrong_count"] for row in mistake_rows(store)} == {1}


def test_submission_rolls_back_mistakes_with_the_result(prepared, monkeypatch):
    store, paper = prepared
    attempt = start_exam(store, paper, False)
    record = exam_mistakes.record_submission

    def interrupted(connection, attempt, result):
        record(connection, attempt, result)
        raise RuntimeError("interrupted before saving result")

    monkeypatch.setattr(exam_mistakes, "record_submission", interrupted)
    with pytest.raises(RuntimeError):
        store.submit_attempt(attempt["id"], {})
    assert store.get_attempt(attempt["id"])["result"] is None
    assert store.mistake_count(paper["id"]) == 0


def test_legacy_results_are_replayed_once_in_submission_order(prepared):
    store, paper = prepared
    first = start_exam(store, paper, False)
    original_result = store.submit_attempt(first["id"], {})
    later = start_exam(store, paper, False)
    answers = {q["id"]: "apple" for q in later["questions"] if q["prompt"] == "苹果"}
    store.submit_attempt(later["id"], answers)
    clear_migration(store)
    with ThreadPoolExecutor(max_workers=2) as pool:
        counts = list(pool.map(lambda _: ExamPreparationStore(store.path).mistake_count(paper["id"]), range(2)))
    assert counts == [2, 2]
    assert sum(row["wrong_count"] for row in mistake_rows(store)) == 5
    assert sum(row["pending"] for row in mistake_rows(store)) == 2
    assert store.get_attempt(first["id"])["result"] == original_result
    assert store.list_papers()[0]["mistake_count"] == 2
    assert sum(row["wrong_count"] for row in mistake_rows(store)) == 5


def test_review_scores_never_replace_normal_exam_scores_even_after_rebuild(prepared):
    store, paper = prepared
    attempt = start_exam(store, paper, False)
    store.submit_attempt(attempt["id"], {})
    review = store.start_mistake_review(paper["id"])
    store.submit_attempt(review["id"], correct_answers(review))
    clear_migration(store)
    summary = store.list_papers()[0]
    assert (summary["attempt_count"], summary["last_score"], summary["best_score"], summary["mistake_count"]) == (1, 0, 0, 0)


def test_empty_review_and_other_user_are_distinct_errors(prepared, tmp_path):
    store, paper = prepared
    with pytest.raises(ExamPreparationError) as error:
        store.start_mistake_review(paper["id"])
    assert error.value.status == 409
    stranger = ExamPreparationStore(tmp_path / "bob" / "exams.sqlite3")
    for operation in (stranger.start_mistake_review, stranger.mistake_count):
        with pytest.raises(ExamPreparationError) as error:
            operation(paper["id"])
        assert error.value.status == 404


@pytest.fixture
def api(tmp_path):
    app = Flask(__name__)
    app.config["TESTING"] = True

    def authenticated(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            username = request.headers.get("X-Test-User")
            if username not in ("alice", "bob", "alice_parent"):
                return jsonify({"error": "需要认证"}), 401
            g.is_parent = username == "alice_parent"
            return function("alice" if g.is_parent else username, *args, **kwargs)
        return wrapped

    def student_only(function):
        @wraps(function)
        def wrapped(username, *args, **kwargs):
            if g.is_parent:
                return jsonify({"error": "请使用学生账号"}), 403
            return function(username, *args, **kwargs)
        return wrapped

    register_exam_preparation_routes(
        app, authenticated, student_only, lambda: tmp_path,
        lambda username, keys: {word["english"]: word for word in WORDS if word["english"] in keys}, choices,
    )
    with app.test_client() as client:
        yield client


def test_review_http_flow_reports_current_mistake_count_and_enforces_access(api):
    prefix = "/api/exam-preparation"
    alice = {"X-Test-User": "alice"}
    parent = {"X-Test-User": "alice_parent"}
    bob = {"X-Test-User": "bob"}
    paper = api.post(prefix + "/papers", headers=parent, json={"name": "测试卷", "words": "apple"}).get_json()["paper"]
    review_url = prefix + f"/papers/{paper['id']}/mistake-reviews"
    assert api.post(review_url, json={}).status_code == 401
    assert api.post(review_url, headers=parent, json={}).status_code == 403
    assert api.post(review_url, headers=bob, json={}).status_code == 404
    assert api.post(review_url, headers=alice, json={}).status_code == 409
    original = api.post(prefix + f"/papers/{paper['id']}/attempts", headers=alice, json={}).get_json()["attempt"]
    submission = api.post(prefix + f"/attempts/{original['id']}/submit", headers=alice, json={"answers": {}})
    assert submission.status_code == 200
    assert submission.get_json()["mistake_count"] == 1
    assert api.get(prefix + f"/attempts/{original['id']}", headers=alice).get_json()["mistake_count"] == 1
    assert api.get(prefix + "/papers", headers=parent).get_json()["papers"][0]["mistake_count"] == 1
    assert api.post(review_url, headers=alice, json=[]).status_code == 400
    response = api.post(review_url, headers=alice, json={})
    assert response.status_code == 201
    review = response.get_json()["attempt"]
    assert review["mode"] == "wrong_review"
    assert all(set(question) == {"id", "type", "prompt"} for question in review["questions"])
    result = api.post(prefix + f"/attempts/{review['id']}/submit", headers=alice, json={"answers": correct_answers(review)}).get_json()
    assert result["mistake_count"] == 0
    assert result["result"]["mode"] == "wrong_review"
    assert api.post(review_url, headers=alice, json={}).status_code == 409
    summary = api.get(prefix + "/papers", headers=alice).get_json()["papers"][0]
    assert (summary["attempt_count"], summary["last_score"], summary["best_score"]) == (1, 0, 0)

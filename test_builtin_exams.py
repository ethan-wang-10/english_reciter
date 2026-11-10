"""Fixed curriculum exams, private attempts, and their matching study material."""

from collections import Counter
from functools import wraps
import json
from pathlib import Path
import sqlite3

from flask import Flask, g, jsonify, request
import pytest

from exam_preparation import ExamPreparationError, ExamPreparationStore
from exam_preparation_routes import register_exam_preparation_routes


BUILTIN_ID = "builtin-shanghai-grade4-u1-u4"
WORDS = [{"english": "apple", "chinese": "苹果"}]
PRIVATE_FIELDS = {"answer", "answers", "accepted_answers", "correct_answer", "answer_option_id", "explanation"}


def no_generated_questions(*args):
    pytest.fail("A fixed built-in exam must not look up or generate wordbank questions")


def private_attempt(store, attempt_id):
    with sqlite3.connect(store.path) as connection:
        row = connection.execute("SELECT attempt_json FROM exam_attempts WHERE id=?", (attempt_id,)).fetchone()
    return json.loads(row[0])


def correct_answers(store, attempt_id):
    return {
        question["id"]: question["answer"]
        for question in private_attempt(store, attempt_id)["questions"]
    }


def builtin_summary(store):
    return next(paper for paper in store.list_papers() if paper["id"] == BUILTIN_ID)


def assert_question_privacy(questions):
    for question in questions:
        assert set(question) <= {"id", "type", "prompt", "options"}
        assert not (set(question) & PRIVATE_FIELDS)


@pytest.fixture
def store(tmp_path):
    return ExamPreparationStore(tmp_path / "alice" / "exam_preparation.sqlite3")


@pytest.fixture
def client(tmp_path):
    app = Flask(__name__)
    app.config.update(TESTING=True)

    def token_required(function):
        @wraps(function)
        def authenticated(*args, **kwargs):
            token = request.headers.get("Authorization", "").removeprefix("Bearer ")
            if token not in {"alice", "bob", "alice_parent"}:
                return jsonify({"error": "请先登录"}), 401
            g.parent = token == "alice_parent"
            return function("alice" if g.parent else token, *args, **kwargs)

        return authenticated

    def parent_forbidden(function):
        @wraps(function)
        def student_only(*args, **kwargs):
            if g.parent:
                return jsonify({"error": "请使用学生账号参加考试"}), 403
            return function(*args, **kwargs)

        return student_only

    register_exam_preparation_routes(
        app, token_required, parent_forbidden, lambda: tmp_path,
        lambda username, keys: {word["english"]: word for word in WORDS if word["english"] in keys},
        no_generated_questions,
    )
    with app.test_client() as browser:
        yield browser


def test_builtin_is_available_without_copying_or_importing_and_private_papers_remain_separate(store):
    paper = store.get_paper(BUILTIN_ID)
    assert paper["builtin"] is True
    assert paper["question_count"] == 16
    assert paper["words"] == []
    assert paper["sentences"] == []
    assert not (set(paper) & PRIVATE_FIELDS)
    assert "questions" not in paper
    assert len([row for row in store.list_papers() if row["id"] == BUILTIN_ID]) == 1

    private = store.create_paper("我的单词考卷", WORDS)
    assert store.get_paper(private["id"])["words"] == WORDS
    assert not store.get_paper(private["id"]).get("builtin", False)
    assert {row["id"] for row in store.list_papers()} >= {BUILTIN_ID, private["id"]}
    assert ExamPreparationStore(store.path).get_paper(BUILTIN_ID)["question_count"] == 16


def test_listing_builtin_catalog_does_not_create_a_personal_paper_row(store):
    assert builtin_summary(store)["question_count"] == 16
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM exam_papers WHERE id=?", (BUILTIN_ID,)).fetchone()[0] == 0
    store.get_paper(BUILTIN_ID)
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM exam_papers WHERE id=?", (BUILTIN_ID,)).fetchone()[0] == 1


def test_builtin_keeps_the_fixed_sixteen_question_paper_and_hides_grading_data(store):
    paper = store.get_paper(BUILTIN_ID)
    first = store.start_attempt(paper, False, no_generated_questions)
    second = store.start_attempt(paper, True, no_generated_questions)
    expected = Counter({"fill_blank": 8, "rewrite": 4, "reading_choice": 4})
    assert Counter(question["type"] for question in first["questions"]) == expected
    assert first["paper_id"] == BUILTIN_ID
    assert first["choice_count"] == 4
    assert [question["prompt"] for question in first["questions"]] == [question["prompt"] for question in second["questions"]]
    assert not ({question["id"] for question in first["questions"]} & {question["id"] for question in second["questions"]})
    assert_question_privacy(first["questions"])
    assert_question_privacy(store.get_attempt(first["id"])["attempt"]["questions"])
    for question in first["questions"]:
        if question["type"] == "reading_choice":
            assert len(question["options"]) == 4
            assert len({option["id"] for option in question["options"]}) == 4


def test_starting_a_listed_builtin_creates_its_private_attempt_history_lazily(store):
    paper = builtin_summary(store)
    attempt = store.start_attempt(paper, False, no_generated_questions)
    store.submit_attempt(attempt["id"], {})
    assert store.mistake_count(BUILTIN_ID) == 16
    assert len(store.start_mistake_review(BUILTIN_ID)["questions"]) == 16


def test_builtin_written_answers_follow_the_handbook_and_accept_sentence_variants(store):
    attempt = store.start_attempt(store.get_paper(BUILTIN_ID), False, no_generated_questions)
    private = private_attempt(store, attempt["id"])
    written = [question for question in private["questions"] if question["type"] != "reading_choice"]
    assert [question["answer"] for question in written[:8]] == ["has", "helps", "drawing", "are", "is swimming", "to eat", "on", "meet"]
    answers = correct_answers(store, attempt["id"])
    variants = [
        "The building does not have any walls.",
        "Does he help his brother learn English?",
        "The monkeys are not playing in the trees.",
        "What is the baby elephant doing?",
    ]
    for question, answer in zip(written[8:], variants, strict=True):
        answers[question["id"]] = "  " + answer.upper().replace(" ", "   ") + "  "
    result = store.submit_attempt(attempt["id"], answers)
    assert (result["score"], result["total"], result["correct_count"]) == (100, 16, 16)
    assert all(row["explanation"] for row in result["questions"])
    assert store.get_attempt(attempt["id"])["result"] == result


def test_builtin_mistakes_cover_written_and_reading_questions_without_changing_exam_scores(store):
    attempt = store.start_attempt(store.get_paper(BUILTIN_ID), False, no_generated_questions)
    original = store.submit_attempt(attempt["id"], {})
    assert original["wrong_count"] == 16
    assert store.mistake_count(BUILTIN_ID) == 16
    review = store.start_mistake_review(BUILTIN_ID)
    assert review["mode"] == "wrong_review"
    assert review["choice_count"] == 4
    assert len(review["questions"]) == 16
    assert all(question["type"] != "reading_choice" for question in review["questions"][:12])
    assert all(question["type"] == "reading_choice" for question in review["questions"][12:])
    assert_question_privacy(review["questions"])
    reviewed = store.submit_attempt(review["id"], correct_answers(store, review["id"]))
    assert reviewed["score"] == 100
    assert store.mistake_count(BUILTIN_ID) == 0
    summary = builtin_summary(store)
    assert (summary["attempt_count"], summary["last_score"], summary["best_score"]) == (1, 0, 0)
    assert store.get_attempt(attempt["id"])["result"] == original


def test_repeated_builtin_attempts_reuse_the_mistake_identity_instead_of_duplicating_it(store):
    paper = store.get_paper(BUILTIN_ID)
    for _ in range(2):
        attempt = store.start_attempt(paper, False, no_generated_questions)
        store.submit_attempt(attempt["id"], {})
    assert store.mistake_count(BUILTIN_ID) == 16
    assert builtin_summary(store)["attempt_count"] == 2


def test_builtin_attempts_and_scores_are_private_even_when_the_paper_is_shared(tmp_path):
    alice = ExamPreparationStore(tmp_path / "alice" / "exam_preparation.sqlite3")
    bob = ExamPreparationStore(tmp_path / "bob" / "exam_preparation.sqlite3")
    attempt = alice.start_attempt(alice.get_paper(BUILTIN_ID), False, no_generated_questions)
    alice.submit_attempt(attempt["id"], correct_answers(alice, attempt["id"]))
    assert builtin_summary(alice)["last_score"] == 100
    assert builtin_summary(bob)["attempt_count"] == 0
    assert builtin_summary(bob)["last_score"] is None
    for operation in (
        lambda: bob.get_attempt(attempt["id"]),
        lambda: bob.submit_attempt(attempt["id"], {}),
    ):
        with pytest.raises(ExamPreparationError) as error:
            operation()
        assert error.value.status == 404
    with pytest.raises(ExamPreparationError) as error:
        bob.start_mistake_review(BUILTIN_ID)
    assert error.value.status == 409


def test_builtin_cannot_be_overwritten_and_unknown_catalog_ids_do_not_resolve(store):
    original = store.get_paper(BUILTIN_ID)
    with pytest.raises(ExamPreparationError) as error:
        store.update_paper(BUILTIN_ID, "被修改", WORDS, [])
    assert error.value.status == 403
    assert store.get_paper(BUILTIN_ID) == original
    for paper_id in ("builtin-missing", "../../config.json", BUILTIN_ID + "/../../config.json"):
        with pytest.raises(ExamPreparationError) as error:
            store.get_paper(paper_id)
        assert error.value.status == 404


def test_forged_reading_option_is_rejected_without_recording_a_result(store):
    attempt = store.start_attempt(store.get_paper(BUILTIN_ID), False, no_generated_questions)
    question = next(question for question in attempt["questions"] if question["type"] == "reading_choice")
    with pytest.raises(ExamPreparationError):
        store.submit_attempt(attempt["id"], {question["id"]: "injected-answer"})
    assert store.get_attempt(attempt["id"])["result"] is None
    assert store.mistake_count(BUILTIN_ID) == 0


def test_builtin_and_knowledge_are_selectable_by_students_and_parents_without_quiz_answers(client):
    root = "/api/exam-preparation/papers"
    assert client.get(root).status_code == 401
    assert client.get(f"{root}/{BUILTIN_ID}/knowledge").status_code == 401
    for username in ("alice", "alice_parent"):
        headers = {"Authorization": f"Bearer {username}"}
        catalog = client.get(root, headers=headers).get_json()
        papers = catalog["papers"] + catalog["builtin_papers"]
        paper = next(row for row in papers if row["id"] == BUILTIN_ID)
        assert not any(row["id"] == BUILTIN_ID for row in catalog["papers"])
        assert paper["builtin"] is True
        assert "questions" not in paper
        response = client.get(f"{root}/{BUILTIN_ID}/knowledge", headers=headers)
        assert response.status_code == 200
        payload = response.get_json()
        knowledge = payload.get("knowledge", payload)
        assert knowledge["title"]
        assert len(knowledge["sections"]) >= 4
        assert payload["paper_name"] == paper["name"]
        assert payload["pdf_url"].startswith("/static/")
        assert payload["pdf_url"].endswith("shanghai-grade4-u1-u4.pdf")
        assert "questions" not in payload and "questions" not in knowledge
        assert not (set(knowledge) & PRIVATE_FIELDS)


def test_builtin_routes_protect_editing_attempts_and_private_results(client):
    root = f"/api/exam-preparation/papers/{BUILTIN_ID}"
    alice = {"Authorization": "Bearer alice"}
    parent = {"Authorization": "Bearer alice_parent"}
    assert client.put(root, headers=alice, json={"name": "改写", "words": "apple"}).status_code == 403
    assert client.post(root + "/attempts", headers=parent, json={}).status_code == 403
    assert client.post(root + "/mistake-reviews", headers=parent, json={}).status_code == 403
    response = client.post(root + "/attempts", headers=alice, json={})
    assert response.status_code == 201
    attempt = response.get_json()["attempt"]
    assert len(attempt["questions"]) == 16
    assert_question_privacy(attempt["questions"])
    assert client.get(f"/api/exam-preparation/attempts/{attempt['id']}", headers=parent).status_code == 403
    assert client.get(f"/api/exam-preparation/attempts/{attempt['id']}", headers={"Authorization": "Bearer bob"}).status_code == 404
    result = client.post(f"/api/exam-preparation/attempts/{attempt['id']}/submit", headers=alice, json={"answers": {}})
    assert result.status_code == 200
    assert result.get_json()["mistake_count"] == 16
    assert client.get(f"{root}/knowledge", headers=alice).status_code == 200
    assert client.get("/api/exam-preparation/papers/builtin-missing/knowledge", headers=alice).status_code == 404


def test_catalog_source_contains_the_expected_fixed_question_ids_and_complete_study_units():
    source = Path(__file__).resolve().parent / "builtin_exams/shanghai-grade4-u1-u4.json"
    catalog = json.loads(source.read_text())
    assert catalog["id"] == BUILTIN_ID
    assert [question["id"] for question in catalog["questions"]] == [f"q{index:02d}" for index in range(1, 17)]
    assert catalog["time_minutes"] == 20
    assert catalog["source_file"] == "shanghai-grade4-u1-u4.pdf"
    assert len(catalog["knowledge"]["sections"]) >= 4
    reading_answers = [
        next(option["text"] for option in question["options"] if option["id"] == question["answer"])
        for question in catalog["questions"] if question["type"] == "reading_choice"
    ]
    assert reading_answers == [
        "9:00 a.m., 8 November", "The hall in School Building 2", "Watch a play.", "A birthday card.",
    ]
    for question in catalog["questions"]:
        assert question["prompt"] and question["explanation"]
        if question["type"] == "reading_choice":
            options = {option["id"]: option["text"] for option in question["options"]}
            assert question["answer"] in options
            assert len(set(options.values())) == 4
        else:
            assert question["answer"] in question["accepted_answers"]

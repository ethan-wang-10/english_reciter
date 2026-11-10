"""Exam parsing, private question snapshots, and durable server grading."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from exam_preparation import ExamPreparationError, ExamPreparationStore, parse_exam_words


WORDS = [
    {"english": "apple", "chinese": "n. 苹果"},
    {"english": "look after", "chinese": "照顾"},
]


def choice_lookup(word, kind):
    if word != "apple" or kind != "recognition":
        return None
    return {
        "prompt": "apple", "answer_option_id": "b", "explanation_zh": "apple 表示苹果。",
        "options": [{"id": "a", "text": "香蕉"}, {"id": "b", "text": "苹果"}],
    }


def test_parse_accepts_commas_newlines_phrases_and_deduplicates():
    words, duplicates = parse_exam_words(" Apple,apple，LOOK   AFTER\nlook after; one's，one’s,, ")
    assert words == ["apple", "look after", "one's"]
    assert duplicates == 3


@pytest.mark.parametrize("text", [None, [], " ,， \n", "苹果", "apple / banana", "apple<script>", "1,2", "a" * 121])
def test_parse_rejects_invalid_or_empty_words(text):
    with pytest.raises(ExamPreparationError):
        parse_exam_words(text)


def test_parse_limits_unique_words():
    assert len(parse_exam_words(",".join(f"word{i}" for i in range(300)))[0]) == 300
    with pytest.raises(ExamPreparationError):
        parse_exam_words(",".join(f"word{i}" for i in range(301)))


def test_papers_and_default_spelling_attempt_persist_without_answers(tmp_path):
    path = tmp_path / "exam.sqlite3"
    store = ExamPreparationStore(path)
    paper = store.create_paper("期中复习", WORDS)
    reopened = ExamPreparationStore(path)
    assert reopened.get_paper(paper["id"]) == paper

    def no_choices(*args):
        pytest.fail("Default dictation must not load multiple-choice questions")

    attempt = reopened.start_attempt(paper, False, no_choices)
    assert len(attempt["questions"]) == 2
    assert attempt["choice_count"] == 0
    assert all(question["type"] == "spelling" for question in attempt["questions"])
    assert all(set(question) == {"id", "type", "prompt"} for question in attempt["questions"])
    assert reopened.get_attempt(attempt["id"]) == {"attempt": attempt, "result": None}


def test_choices_are_optional_and_missing_choices_keep_all_spelling_questions(tmp_path):
    store = ExamPreparationStore(tmp_path / "exam.sqlite3")
    paper = store.create_paper("混合卷", WORDS)
    attempt = store.start_attempt(paper, True, choice_lookup)
    assert [question["type"] for question in attempt["questions"]] == ["spelling", "spelling", "recognition"]
    assert attempt["choice_count"] == 1
    assert not any("answer" in key or "explanation" in key for question in attempt["questions"] for key in question)
    answers = {}
    for question in attempt["questions"]:
        if question["type"] == "recognition":
            answers[question["id"]] = "a"
        elif question["prompt"] == "n. 苹果":
            answers[question["id"]] = "  APPLE  "
        else:
            answers[question["id"]] = "Look    After"
    result = store.submit_attempt(attempt["id"], answers)
    assert (result["score"], result["correct_count"], result["wrong_count"]) == (66.7, 2, 1)
    wrong = next(question for question in result["questions"] if not question["correct"])
    assert wrong["user_answer"] == "香蕉"
    assert wrong["correct_answer"] == "苹果"
    assert wrong["explanation"] == "apple 表示苹果。"
    assert store.get_attempt(attempt["id"])["result"] == result


def test_blank_answers_are_wrong_and_submissions_cannot_rewrite_scores(tmp_path):
    store = ExamPreparationStore(tmp_path / "exam.sqlite3")
    paper = store.create_paper("默写", WORDS)
    attempt = store.start_attempt(paper, False, choice_lookup)
    first = store.submit_attempt(attempt["id"], {})
    assert first["score"] == 0
    assert first["wrong_count"] == 2
    corrected = {
        question["id"]: "apple" if question["prompt"] == "n. 苹果" else "look after"
        for question in attempt["questions"]
    }
    assert store.submit_attempt(attempt["id"], corrected) == first
    second = store.start_attempt(paper, False, choice_lookup)
    store.submit_attempt(second["id"], {
        question["id"]: "apple" if question["prompt"] == "n. 苹果" else "look after"
        for question in second["questions"]
    })
    summary = store.list_papers()[0]
    assert (summary["attempt_count"], summary["last_score"], summary["best_score"]) == (2, 100, 100)


@pytest.mark.parametrize("answers", [None, [], {"injected-id": "apple"}])
def test_grading_rejects_unissued_questions_and_malformed_answers(tmp_path, answers):
    store = ExamPreparationStore(tmp_path / "exam.sqlite3")
    paper = store.create_paper("测试", WORDS)
    attempt = store.start_attempt(paper, True, choice_lookup)
    with pytest.raises(ExamPreparationError):
        store.submit_attempt(attempt["id"], answers)
    assert store.get_attempt(attempt["id"])["result"] is None


def test_grading_rejects_forged_option_ids(tmp_path):
    store = ExamPreparationStore(tmp_path / "exam.sqlite3")
    paper = store.create_paper("测试", WORDS)
    attempt = store.start_attempt(paper, True, choice_lookup)
    question = attempt["questions"][-1]
    with pytest.raises(ExamPreparationError):
        store.submit_attempt(attempt["id"], {question["id"]: "forged"})
    with pytest.raises(ExamPreparationError):
        store.submit_attempt(attempt["id"], {question["id"]: ["b"]})


def test_concurrent_submission_records_one_result(tmp_path):
    path = tmp_path / "exam.sqlite3"
    store = ExamPreparationStore(path)
    paper = store.create_paper("并发交卷", WORDS)
    attempt = store.start_attempt(paper, False, choice_lookup)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: ExamPreparationStore(path).submit_attempt(attempt["id"], {}), range(2)))
    assert results[0] == results[1]
    assert store.list_papers()[0]["attempt_count"] == 1


def test_users_cannot_access_each_others_papers_or_attempts(tmp_path):
    alice = ExamPreparationStore(tmp_path / "alice" / "exam.sqlite3")
    bob = ExamPreparationStore(tmp_path / "bob" / "exam.sqlite3")
    paper = alice.create_paper("私有卷", WORDS)
    attempt = alice.start_attempt(paper, False, choice_lookup)
    assert bob.list_papers() == []
    for operation in (
        lambda: bob.get_paper(paper["id"]),
        lambda: bob.get_attempt(attempt["id"]),
        lambda: bob.submit_attempt(attempt["id"], {}),
    ):
        with pytest.raises(ExamPreparationError) as error:
            operation()
        assert error.value.status == 404

"""Exam parsing, private question snapshots, and durable server grading."""

from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3

import pytest

from exam_preparation import ExamPreparationError, ExamPreparationStore, parse_exam_sentences, parse_exam_words


WORDS = [
    {"english": "apple", "chinese": "n. 苹果"},
    {"english": "look after", "chinese": "照顾"},
]
SENTENCES = [{
    "chinese": "我每天步行去学校。",
    "answers": ["I walk to school every day.", "I go to school on foot every day."],
}]


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
    assert not [paper for paper in bob.list_papers() if not paper.get('builtin')]
    for operation in (
        lambda: bob.get_paper(paper["id"]),
        lambda: bob.get_attempt(attempt["id"]),
        lambda: bob.submit_attempt(attempt["id"], {}),
    ):
        with pytest.raises(ExamPreparationError) as error:
            operation()
        assert error.value.status == 404


def test_sentence_parser_preserves_text_and_deduplicates_equivalent_answers():
    parsed = parse_exam_sentences([{
        "chinese": "  我每天步行去学校。  ",
        "answers": ["I walk to school every day.", " i  walk to school every day！ ", "I go to school on foot every day."],
    }])
    assert parsed == SENTENCES
    assert parse_exam_words("", allow_empty=True) == ([], 0)


@pytest.mark.parametrize("sentences", [
    None, {}, [None], [{}], [{"chinese": "", "answers": ["Hello"]}],
    [{"chinese": "中文", "answers": []}], [{"chinese": "中文", "answers": "Hello"}],
    [{"chinese": "中文", "answers": [None]}], [{"chinese": "中文", "answers": ["中文"]}],
    [{"chinese": "中文", "answers": ["Hello 中文"]}], [{"chinese": "中文", "answers": ["!?"]}],
    [{"chinese": "中" * 501, "answers": ["Hello"]}], [{"chinese": "中文", "answers": ["a" * 501]}],
    [{"chinese": "中文", "answers": ["Hello"] * 11}], SENTENCES * 101,
])
def test_sentence_parser_rejects_incomplete_or_oversized_questions(sentences):
    with pytest.raises(ExamPreparationError):
        parse_exam_sentences(sentences)


@pytest.mark.parametrize("answer,correct", [
    ("I WALK   to school every day！", True), ("I go to school on foot every day", True),
    ("I walked to school every day.", False), ("", False),
])
def test_sentence_only_exam_is_private_and_graded_against_accepted_answers(tmp_path, answer, correct):
    store = ExamPreparationStore(tmp_path / "exam.sqlite3")
    paper = store.create_paper("句子中译英", [], SENTENCES)
    assert ExamPreparationStore(store.path).get_paper(paper["id"])["sentences"] == SENTENCES
    attempt = store.start_attempt(paper, False, choice_lookup)
    assert (attempt["word_count"], attempt["sentence_count"]) == (0, 1)
    question = attempt["questions"][0]
    assert question["type"] == "translation"
    assert set(question) == {"id", "type", "prompt"}
    result = store.submit_attempt(attempt["id"], {question["id"]: answer})
    assert result["questions"][0]["correct"] is correct
    assert result["score"] == (100 if correct else 0)
    assert all(value in result["questions"][0]["correct_answer"] for value in SENTENCES[0]["answers"])


def test_mixed_exam_keeps_written_questions_before_choices(tmp_path):
    store = ExamPreparationStore(tmp_path / "exam.sqlite3")
    paper = store.create_paper("混合卷", WORDS, SENTENCES)
    attempt = store.start_attempt(paper, True, choice_lookup)
    assert [q["type"] for q in attempt["questions"]] == ["spelling", "spelling", "translation", "recognition"]


def test_edit_keeps_paper_identity_and_existing_attempt_snapshot(tmp_path):
    store = ExamPreparationStore(tmp_path / "exam.sqlite3")
    paper = store.create_paper("修改前", WORDS, SENTENCES)
    old_attempt = store.start_attempt(paper, False, choice_lookup)
    changed_sentences = [{"chinese": "昨天我步行去学校。", "answers": ["I walked to school yesterday."]}]
    edited = store.update_paper(paper["id"], "修改后", WORDS[:1], changed_sentences)
    assert edited["id"] == paper["id"]
    assert edited["created_at"] == paper["created_at"]
    assert store.get_attempt(old_attempt["id"])["attempt"] == old_attempt
    answers = {q["id"]: {
        "spelling": "apple" if q["prompt"] == "n. 苹果" else "look after",
        "translation": SENTENCES[0]["answers"][1],
    }[q["type"]] for q in old_attempt["questions"]}
    result = store.submit_attempt(old_attempt["id"], answers)
    assert result["paper_name"] == "修改前"
    assert result["score"] == 100
    new_attempt = store.start_attempt(edited, False, choice_lookup)
    assert new_attempt["paper_name"] == "修改后"
    assert [q["prompt"] for q in new_attempt["questions"] if q["type"] == "translation"] == ["昨天我步行去学校。"]
    assert len([paper for paper in store.list_papers() if not paper.get('builtin')]) == 1
    assert store.list_papers()[0]["attempt_count"] == 1
    with pytest.raises(ExamPreparationError) as error:
        store.update_paper("missing", "不存在", WORDS, [])
    assert error.value.status == 404


def test_legacy_database_migrates_without_losing_words_or_attempts(tmp_path):
    path = tmp_path / "exam.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE exam_papers(id TEXT PRIMARY KEY, name TEXT NOT NULL, words_json TEXT NOT NULL, created_at TEXT NOT NULL)")
        connection.execute("INSERT INTO exam_papers VALUES (?, ?, ?, ?)", ("legacy", "旧考卷", json.dumps(WORDS), "2026-11-09"))
    store = ExamPreparationStore(path)
    paper = store.get_paper("legacy")
    assert paper["words"] == WORDS
    assert paper["sentences"] == []
    assert paper["sentence_count"] == 0
    attempt = store.start_attempt(paper, False, choice_lookup)
    store.update_paper("legacy", "添加句子后", WORDS, SENTENCES)
    assert store.get_attempt(attempt["id"])["attempt"] == attempt
    assert store.get_paper("legacy")["sentences"] == SENTENCES

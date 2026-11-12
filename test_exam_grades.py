"""Grade validation and safe one-time classification of existing papers."""

import json
import sqlite3

import pytest

from exam_grades import infer_grade, infer_term, normalize_grade, normalize_term
from exam_preparation import ExamPreparationError, ExamPreparationStore


@pytest.mark.parametrize('title,expected', [
    ('四上期中考试', 4), ('上海四年级上册英语', 4), ('4年级期末', 4),
    ('六下', 6), ('三年级复习', 3), ('初一英语', 7), ('初中二年级', 8),
    ('九年级', 9), ('高三模拟卷', 12), ('高中一年级', 10), ('高二年级', 11),
    ('Grade 5 Unit 2', 5), ('Grade12 final', 12), ('十一年级', 11),
    ('期中考试 Unit 1–3', None), ('2026年期末', None), ('13年级', None),
    ('四年级和五年级综合', None), ('初一到初三复习', None),
])
def test_old_paper_titles_only_classify_unambiguous_grades(title, expected):
    assert infer_grade(title) == expected


@pytest.mark.parametrize('value', [True, False, 0, 13, -1, 4.0, [], {}, '四年级', '04', '4.5'])
def test_invalid_grades_are_rejected(value):
    with pytest.raises(ValueError):
        normalize_grade(value)


@pytest.mark.parametrize('title,expected', [
    ('四上期中考试', 1), ('四下期末考试', 2), ('四年级上册英语', 1),
    ('四年级下', 2), ('初一上学期', 1), ('高二下册', 2),
    ('四年级上下册综合', None), ('期末复习', None), ('上海英语卷', None),
])
def test_old_titles_recognize_upper_and_lower_terms_without_guessing(title, expected):
    assert infer_term(title) == expected


@pytest.mark.parametrize('value', [True, 0, 3, 1.0, [], '上册'])
def test_invalid_terms_are_rejected(value):
    with pytest.raises(ValueError):
        normalize_term(value)


def test_grade_migration_and_manual_reclassification_preserve_papers_and_attempts(tmp_path):
    path = tmp_path / 'exam.sqlite3'
    words = [{'english': 'apple', 'chinese': '苹果'}]
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE exam_papers(id TEXT PRIMARY KEY, name TEXT, words_json TEXT, sentences_json TEXT, created_at TEXT)')
        connection.executemany('INSERT INTO exam_papers VALUES (?, ?, ?, ?, ?)', [
            ('old-four', '四上期中考试', json.dumps(words), '[]', '2026-11-10'),
            ('unknown', '期末模拟卷', json.dumps(words), '[]', '2026-11-09'),
        ])
    store = ExamPreparationStore(path)
    assert store.get_paper('old-four')['grade'] == 4
    assert store.get_paper('old-four')['term'] == 1
    assert store.get_paper('unknown')['grade'] is None
    original = store.get_paper('old-four')
    attempt = store.start_attempt(original, False, lambda *_: None)
    result = store.submit_attempt(attempt['id'], {question['id']: 'apple' for question in attempt['questions']})

    changed = store.update_paper('old-four', original['name'], words, [], grade=6, term=2)
    assert changed['grade'] == 6
    assert changed['term'] == 2
    assert changed['id'] == original['id']
    assert changed['created_at'] == original['created_at']
    assert store.get_attempt(attempt['id']) == {'attempt': attempt, 'result': result}
    assert store.list_papers()[0]['attempt_count'] == 1

    store.update_paper('old-four', original['name'], words, [], grade=None, term=None)
    reopened = ExamPreparationStore(path)
    assert reopened.get_paper('old-four')['grade'] is None
    assert reopened.get_paper('old-four')['term'] is None
    assert reopened.get_paper('old-four')['words'] == words
    assert reopened.get_attempt(attempt['id'])['result']['score'] == 100


def test_store_validates_grade_and_keeps_omitted_grade_on_older_updates(tmp_path):
    store = ExamPreparationStore(tmp_path / 'exam.sqlite3')
    words = [{'english': 'apple', 'chinese': '苹果'}]
    paper = store.create_paper('四上英语', words)
    assert paper['grade'] == 4
    assert paper['term'] == 1
    assert store.update_paper(paper['id'], '改名的考卷', words, [])['grade'] == 4
    assert store.get_paper(paper['id'])['term'] == 1
    with pytest.raises(ExamPreparationError):
        store.update_paper(paper['id'], '无效修改', words, [], grade=True)
    assert store.get_paper(paper['id'])['name'] == '改名的考卷'
    assert store.create_paper('四年级卷', words, grade=None)['grade'] is None

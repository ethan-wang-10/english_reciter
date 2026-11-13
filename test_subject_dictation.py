"""Chinese wordlists are reusable family preparation, not graded exam attempts."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from functools import wraps
import json
import logging
import sqlite3
from types import SimpleNamespace

from flask import Flask, g, jsonify, request
import pytest

from subject_dictation import DictationError, DictationStore, register_subject_dictation_routes, validate_words

PREFIX = '/api/subjects/chinese/dictation'
BOOK_ID = 'shanghai-chinese-grade4-term1-2026'
LESSON = 'lesson-01'
DEFAULT = object()


def catalog():
    return {'schema_version': 1, 'books': [{
        'id': BOOK_ID, 'title': '语文四年级上册', 'grade': 4, 'term': 1,
        'edition': '上海2026新版', 'catalog_note': '按已经核对的课文收录。',
        'lessons': [{'id': LESSON, 'number': 1, 'title': '观潮', 'unit': '第一单元'},
                    {'id': 'lesson-02', 'number': 2, 'title': '繁星'}],
    }]}


@pytest.fixture
def browser(tmp_path):
    app = Flask(__name__)
    path = tmp_path / 'books.json'
    path.write_text(json.dumps(catalog(), ensure_ascii=False))
    app.config.update(TESTING=True, DICTATION_CATALOG_FILE=path)
    identities = {'alice-token': ('alice', 'alice'), 'bob-token': ('bob', 'bob'),
                  'parent-token': ('alice', 'alice_parent')}

    def token_required(function):
        @wraps(function)
        def authenticated(*args, **kwargs):
            token = request.headers.get('Authorization', '').removeprefix('Bearer ')
            if token not in identities:
                return jsonify({'error': '需要认证'}), 401
            student, login = identities[token]
            g.is_parent = login.endswith('_parent')
            g.login_username = login
            return function(student, *args, **kwargs)
        return authenticated

    data_dir = tmp_path / 'users'
    services = SimpleNamespace(DATA_DIR=data_dir, token_required=token_required, logger=logging.getLogger(__name__))
    register_subject_dictation_routes(app, services)
    return SimpleNamespace(client=app.test_client(), app=app, catalog_path=path, data_dir=data_dir,
                           headers={'Authorization': 'Bearer alice-token'},
                           parent_headers={'Authorization': 'Bearer parent-token'},
                           bob_headers={'Authorization': 'Bearer bob-token'})


def uri(lesson_id=LESSON):
    return PREFIX + '/wordlists/' + BOOK_ID + '/' + lesson_id


def words(*texts):
    return [{'text': text} for text in texts]


def database(browser, student='alice'):
    return browser.data_dir / student / 'subjects/chinese/dictation.sqlite3'


def save(browser, values=DEFAULT, revision=0, headers=None, lesson_id=LESSON):
    return browser.client.put(uri(lesson_id), headers=headers or browser.headers,
                              json={'base_revision': revision, 'words': words('潮水', '笼罩') if values is DEFAULT else values})


def test_empty_reads_are_private_and_create_no_learning_files(browser):
    for endpoint in ('/books', '/lessons?book_id=' + BOOK_ID, '/wordlists?book_id=' + BOOK_ID,
                     '/wordlists/' + BOOK_ID + '/' + LESSON):
        response = browser.client.get(PREFIX + endpoint, headers=browser.headers)
        assert response.status_code == 200
        assert 'no-store' in response.headers['Cache-Control']
    wordlist = browser.client.get(uri(), headers=browser.headers).json['wordlist']
    assert wordlist['revision'] == 0 and wordlist['words'] == [] and wordlist['word_count'] == 0
    assert not browser.data_dir.exists()


def test_catalog_anchors_remain_factual_without_global_words(browser):
    body = browser.client.get(PREFIX + '/books', headers=browser.headers).json
    assert body['count'] == 1
    book = body['items'][0]
    assert (book['grade'], book['term']) == (4, 1)
    assert [(item['number'], item['title']) for item in book['lessons']] == [(1, '观潮'), (2, '繁星')]
    assert all(item['custom'] is False for item in book['lessons'])
    assert 'words' not in json.dumps(body)
    assert not browser.data_dir.exists()


def test_routes_require_existing_authentication(browser):
    for method, endpoint, body in (
        ('get', '/books', None), ('get', '/lessons?book_id=' + BOOK_ID, None),
        ('get', '/wordlists?book_id=' + BOOK_ID, None), ('get', '/wordlists/' + BOOK_ID + '/' + LESSON, None),
        ('put', '/wordlists/' + BOOK_ID + '/' + LESSON, {'base_revision': 0, 'words': words('潮水')}),
        ('post', '/lessons', {'book_id': BOOK_ID, 'title': '我的课文'}),
    ):
        assert getattr(browser.client, method)(PREFIX + endpoint, headers={}, json=body).status_code == 401
    assert not browser.data_dir.exists()


def test_save_reload_and_first_occurrence_deduplication(browser):
    response = save(browser, [{'text': '  潮水  '}, {'text': '笼罩', 'spoken_text': '薄雾笼罩大地'},
                              {'text': '潮水', 'spoken_text': '后来的提示不替换第一项'},
                              {'text': 'e\u0301'}, {'text': 'é'}])
    assert response.status_code == 200 and response.json['duplicates_removed'] == 2
    first = response.json['wordlist']
    assert first['words'] == [{'text': '潮水', 'spoken_text': '潮水'}, {'text': '笼罩', 'spoken_text': '薄雾笼罩大地'},
                              {'text': 'é', 'spoken_text': 'é'}]
    assert (first['revision'], first['word_count'], first['updated_by']) == (1, 3, 'alice')
    assert browser.client.get(uri(), headers=browser.headers).json['wordlist'] == first
    assert database(browser).is_file()
    assert not (browser.data_dir / 'alice/learning.sqlite3').exists()
    assert not (browser.data_dir / 'alice/exam_preparation.sqlite3').exists()
    assert not (browser.data_dir / 'alice/subjects/math').exists()


def test_parent_prepares_the_linked_students_words(browser):
    response = save(browser, words('观潮', '繁星'), headers=browser.parent_headers)
    assert response.status_code == 200
    assert response.json['wordlist']['updated_by'] == 'alice_parent'
    assert database(browser).is_file() and not database(browser, 'alice_parent').exists()
    student = browser.client.get(uri(), headers=browser.headers).json['wordlist']
    assert [item['text'] for item in student['words']] == ['观潮', '繁星']
    changed = save(browser, words('潮水'), revision=student['revision'])
    assert changed.status_code == 200
    assert browser.client.get(uri(), headers=browser.parent_headers).json['wordlist'] == changed.json['wordlist']


def test_users_and_lessons_have_separate_wordlists(browser):
    assert save(browser).status_code == 200
    assert save(browser, words('星光'), lesson_id='lesson-02').status_code == 200
    bob = browser.client.get(uri(), headers=browser.bob_headers).json['wordlist']
    assert bob['revision'] == 0 and bob['words'] == []
    assert browser.client.get(PREFIX + '/wordlists?book_id=' + BOOK_ID, headers=browser.bob_headers).json['items'] == []
    assert not database(browser, 'bob').exists()
    rows = browser.client.get(PREFIX + '/wordlists?book_id=' + BOOK_ID, headers=browser.headers).json
    assert rows['count'] == 2 and all('score' not in item and 'correct_count' not in item for item in rows['items'])


def test_stale_revision_preserves_the_current_list(browser):
    assert save(browser, words('潮水')).status_code == 200
    stale = save(browser, words('错误覆盖'), revision=0)
    assert stale.status_code == 409 and stale.json['code'] == 'conflict' and stale.json['current_revision'] == 1
    current = browser.client.get(uri(), headers=browser.headers).json['wordlist']
    assert [item['text'] for item in current['words']] == ['潮水']
    assert save(browser, words('笼罩'), revision=1).json['wordlist']['revision'] == 2


def test_simultaneous_first_saves_have_one_winner(tmp_path):
    path = tmp_path / 'users/alice/subjects/chinese/dictation.sqlite3'
    def write(text):
        try:
            return DictationStore(path).save_wordlist(BOOK_ID, LESSON, 0, words(text), 'alice')
        except DictationError as error:
            return error
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, ['潮水', '繁星']))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert [result.status for result in results if isinstance(result, DictationError)] == [409]
    assert DictationStore(path).get_wordlist(BOOK_ID, LESSON)['revision'] == 1


def test_concurrent_updates_from_one_revision_preserve_a_winner(browser):
    assert save(browser).status_code == 200
    def update(text):
        try:
            return DictationStore(database(browser)).save_wordlist(BOOK_ID, LESSON, 1, words(text), 'alice')
        except DictationError as error:
            return error
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ['薄雾', '潮水']))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert [result.status for result in results if isinstance(result, DictationError)] == [409]
    assert DictationStore(database(browser)).get_wordlist(BOOK_ID, LESSON)['revision'] == 2


def test_an_empty_list_can_be_saved_then_filled_again(browser):
    first = save(browser, [])
    assert first.status_code == 200 and first.json['wordlist']['revision'] == 1
    assert first.json['wordlist']['words'] == []
    assert save(browser, words('潮水'), revision=1).json['wordlist']['word_count'] == 1


def test_custom_lessons_are_private_and_parent_editable(browser):
    body = {'book_id': BOOK_ID, 'title': '  课外 阅读  ', 'number': 3}
    response = browser.client.post(PREFIX + '/lessons', headers=browser.parent_headers, json=body)
    assert response.status_code == 201 and response.json['lesson']['custom'] is True
    item = response.json['lesson']
    assert item['title'] == '课外 阅读' and item['created_by'] == 'alice_parent'
    again = browser.client.post(PREFIX + '/lessons', headers=browser.headers, json=body)
    assert again.status_code == 200 and again.json['lesson']['id'] == item['id'] and again.json['duplicate'] is True
    lessons = browser.client.get(PREFIX + '/lessons?book_id=' + BOOK_ID, headers=browser.headers).json['items']
    assert [entry['id'] for entry in lessons] == [LESSON, 'lesson-02', item['id']]
    assert save(browser, words('阅读'), headers=browser.parent_headers, lesson_id=item['id']).status_code == 200
    assert browser.client.get(uri(item['id']), headers=browser.bob_headers).status_code == 404
    assert len(browser.client.get(PREFIX + '/lessons?book_id=' + BOOK_ID, headers=browser.bob_headers).json['items']) == 2


def test_custom_lesson_without_number_sorts_last(browser):
    response = browser.client.post(PREFIX + '/lessons', headers=browser.headers, json={'book_id': BOOK_ID, 'title': '老师补充词语'})
    assert response.status_code == 201 and response.json['lesson']['number'] is None
    items = browser.client.get(PREFIX + '/lessons?book_id=' + BOOK_ID, headers=browser.headers).json['items']
    assert items[-1]['id'] == response.json['lesson']['id']


@pytest.mark.parametrize('values', [
    None, {}, ['潮水'], [{'text': ''}], [{'text': '   '}], [{'text': '！'}],
    [{'text': 'x' * 41}], [{'text': '潮\n水'}], [{'text': '潮\u200b水'}],
    [{'text': '潮水', 'spoken_text': None}], [{'text': '潮水', 'spoken_text': 'x' * 101}],
    [{'text': '潮水', 'spoken_text': '朝\t水'}], [{'text': '潮水', 'answer': 'untrusted'}],
    [{'spoken_text': '潮水'}], [{'text': 7}], words(*map(str, range(201))), words(*(['潮水'] * 501)),
])
def test_invalid_word_payloads_create_no_files(browser, values):
    assert save(browser, values).status_code == 400
    assert not browser.data_dir.exists()


@pytest.mark.parametrize('revision', [None, True, -1, 1.0, '1', 9_007_199_254_740_992])
def test_revision_is_a_bounded_nonnegative_integer(browser, revision):
    assert save(browser, revision=revision).status_code == 400
    assert not browser.data_dir.exists()


def test_stale_create_does_not_create_a_database(browser):
    result = save(browser, revision=1)
    assert result.status_code == 409 and result.json['current_revision'] == 0
    assert not browser.data_dir.exists()


@pytest.mark.parametrize('body', [None, [], {}, {'base_revision': 0}, {'base_revision': 0, 'words': [], 'student': 'bob'}])
def test_put_body_rejects_unknown_fields_and_user_selection(browser, body):
    assert browser.client.put(uri(), headers=browser.headers, json=body).status_code == 400
    assert not browser.data_dir.exists()


@pytest.mark.parametrize('body', [
    {'book_id': BOOK_ID, 'title': ''}, {'book_id': BOOK_ID, 'title': 'x' * 81},
    {'book_id': BOOK_ID, 'title': '课文', 'number': True}, {'book_id': BOOK_ID, 'title': '课文', 'number': 0},
    {'book_id': BOOK_ID, 'title': '课文', 'number': 1000}, {'book_id': BOOK_ID, 'title': '课文', 'number': '3'},
    {'book_id': BOOK_ID, 'title': '课文', 'student': 'bob'},
])
def test_invalid_custom_lessons_leave_shared_catalog_unchanged(browser, body):
    before = browser.catalog_path.read_bytes()
    assert browser.client.post(PREFIX + '/lessons', headers=browser.headers, json=body).status_code == 400
    assert browser.catalog_path.read_bytes() == before and not browser.data_dir.exists()


def test_unknown_catalog_identifiers_never_create_student_files(browser):
    for endpoint in ('/wordlists/unknown/' + LESSON, '/wordlists/' + BOOK_ID + '/unknown',
                     '/lessons?book_id=unknown', '/wordlists?book_id=unknown'):
        assert browser.client.get(PREFIX + endpoint, headers=browser.headers).status_code == 404
    assert browser.client.get(uri('custom_' + 'a' * 32), headers=browser.headers).status_code == 404
    assert browser.client.put(uri('unknown'), headers=browser.headers, json={'base_revision': 0, 'words': []}).status_code == 404
    assert not browser.data_dir.exists()


def test_corrupted_saved_words_fail_without_replacing_them(browser):
    assert save(browser).status_code == 200
    with sqlite3.connect(database(browser)) as connection:
        connection.execute("UPDATE dictation_wordlists SET words_json='not-json'")
    assert browser.client.get(uri(), headers=browser.headers).status_code == 503
    with sqlite3.connect(database(browser)) as connection:
        assert connection.execute('SELECT words_json FROM dictation_wordlists').fetchone()[0] == 'not-json'


def test_a_still_open_editor_cannot_overwrite_corrupted_saved_words(browser):
    assert save(browser).status_code == 200
    with sqlite3.connect(database(browser)) as connection:
        connection.execute("UPDATE dictation_wordlists SET words_json='not-json'")
    response = save(browser, words('潮水'), revision=1)
    assert response.status_code == 503
    with sqlite3.connect(database(browser)) as connection:
        assert connection.execute('SELECT words_json, revision FROM dictation_wordlists').fetchone() == ('not-json', 1)


def test_filesystem_write_errors_return_retryable_failure_and_preserve_words(browser, monkeypatch):
    assert save(browser).status_code == 200
    def unavailable_lock(_):
        raise PermissionError('read-only volume')
    monkeypatch.setattr('subject_dictation.interprocess_file_lock', unavailable_lock)
    response = save(browser, words('新词语'), revision=1)
    assert response.status_code == 503
    current = browser.client.get(uri(), headers=browser.headers).json['wordlist']
    assert current['revision'] == 1 and [item['text'] for item in current['words']] == ['潮水', '笼罩']


def test_exhausted_revision_never_returns_an_unsafe_javascript_integer(browser):
    assert save(browser).status_code == 200
    limit = 9_007_199_254_740_991
    with sqlite3.connect(database(browser)) as connection:
        connection.execute('UPDATE dictation_wordlists SET revision=?', (limit,))
    assert save(browser, words('新词语'), revision=limit).status_code == 503
    current = browser.client.get(uri(), headers=browser.headers).json['wordlist']
    assert current['revision'] == limit and current['word_count'] == 2


@pytest.mark.parametrize('mutation', [
    lambda d: d.update(schema_version=True), lambda d: d.update(books={}),
    lambda d: d['books'].append(deepcopy(d['books'][0])), lambda d: d['books'][0].update(grade=True),
    lambda d: d['books'][0].update(term=3),
    lambda d: d['books'][0]['lessons'].append(deepcopy(d['books'][0]['lessons'][0])),
    lambda d: d['books'][0]['lessons'][0].update(id='../escape'),
    lambda d: d['books'][0]['lessons'][0].update(id='custom_' + 'a' * 32),
])
def test_invalid_catalogs_preserve_existing_private_words(browser, mutation):
    assert save(browser).status_code == 200
    data = catalog(); mutation(data)
    browser.catalog_path.write_text(json.dumps(data, ensure_ascii=False))
    assert browser.client.get(PREFIX + '/books', headers=browser.headers).status_code == 503
    assert DictationStore(database(browser)).get_wordlist(BOOK_ID, LESSON)['word_count'] == 2


def test_missing_or_malformed_catalog_is_not_replaced(browser):
    browser.catalog_path.write_text('not-json')
    assert browser.client.get(PREFIX + '/books', headers=browser.headers).status_code == 503
    browser.catalog_path.unlink()
    assert browser.client.get(PREFIX + '/books', headers=browser.headers).status_code == 503
    assert not browser.catalog_path.exists() and not browser.data_dir.exists()


def test_direct_store_empty_reads_create_nothing(tmp_path):
    path = tmp_path / 'not-yet-created/subjects/chinese/dictation.sqlite3'
    store = DictationStore(path)
    assert store.list_wordlists(BOOK_ID) == [] and store.list_custom_lessons(BOOK_ID) == []
    assert store.get_wordlist(BOOK_ID, LESSON)['revision'] == 0
    assert not path.parent.exists()


def test_normalization_keeps_case_and_spacing_order():
    result, count = validate_words([{'text': '  AB  CD  ', 'spoken_text': ''}, {'text': 'ab cd'}, {'text': 'AB CD'}])
    assert result == [{'text': 'AB CD', 'spoken_text': 'AB CD'}, {'text': 'ab cd', 'spoken_text': 'ab cd'}]
    assert count == 1

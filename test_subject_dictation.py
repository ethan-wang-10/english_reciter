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
    with monkeypatch.context() as patch:
        patch.setattr('subject_dictation.interprocess_file_lock', unavailable_lock)
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


def mistake_item(texts=('潮水',), lesson_id=LESSON, book_id=BOOK_ID):
    return {'book_id': book_id, 'lesson_id': lesson_id,
            'words': [({'text': value} if isinstance(value, str) else value) for value in texts]}


def add_mistakes(browser, items=None, headers=None):
    return browser.client.post(PREFIX + '/mistakes', headers=headers or browser.headers,
                               json={'items': [mistake_item()] if items is None else items})


def get_mistakes(browser, headers=None, book_id=BOOK_ID):
    return browser.client.get(PREFIX + '/mistakes?book_id=' + book_id, headers=headers or browser.headers)


def remove_mistakes(browser, texts, lesson_id=LESSON, headers=None):
    return browser.client.delete(PREFIX + '/mistakes/' + BOOK_ID + '/' + lesson_id,
                                 headers=headers or browser.headers, json={'words': texts})


def test_mistake_routes_require_authentication_and_empty_reads_create_nothing(browser):
    assert get_mistakes(browser).json == {'items': [], 'count': 0, 'word_count': 0}
    assert 'no-store' in get_mistakes(browser).headers['Cache-Control']
    assert add_mistakes(browser, []).json == {'added_count': 0, 'existing_count': 0}
    assert add_mistakes(browser, [mistake_item([])]).json == {'added_count': 0, 'existing_count': 0}
    assert remove_mistakes(browser, []).json == {'removed_count': 0}
    assert remove_mistakes(browser, ['潮水']).json == {'removed_count': 0}
    for method, endpoint, payload in (
        ('get', '/mistakes?book_id=' + BOOK_ID, None),
        ('post', '/mistakes', {'items': [mistake_item()]}),
        ('delete', '/mistakes/' + BOOK_ID + '/' + LESSON, {'words': ['潮水']}),
    ):
        assert getattr(browser.client, method)(PREFIX + endpoint, json=payload).status_code == 401
    assert not browser.data_dir.exists()


def test_mistakes_share_with_linked_parent_and_preserve_independent_word_snapshots(browser):
    assert save(browser, words('潮水', '笼罩')).status_code == 200
    original = browser.client.get(uri(), headers=browser.headers).json['wordlist']
    marked = add_mistakes(browser, [mistake_item([{'text': '薄', 'spoken_text': '单薄的薄'}, '潮水'])], browser.parent_headers)
    assert marked.status_code == 200 and marked.json == {'added_count': 2, 'existing_count': 0}
    result = get_mistakes(browser).json
    assert (result['count'], result['word_count']) == (1, 2)
    group = result['items'][0]
    assert (group['book_id'], group['lesson_id'], group['lesson']['title'], group['word_count']) == (BOOK_ID, LESSON, '观潮', 2)
    assert all(word['added_at'] and word['updated_at'] and word['updated_by'] == 'alice_parent' for word in group['words'])
    assert get_mistakes(browser, browser.parent_headers).json == result
    assert get_mistakes(browser, browser.bob_headers).json['items'] == []
    assert not database(browser, 'alice_parent').exists() and not database(browser, 'bob').exists()
    assert browser.client.get(uri(), headers=browser.headers).json['wordlist'] == original
    assert save(browser, [], revision=1).status_code == 200
    assert get_mistakes(browser).json == result
    assert not (browser.data_dir / 'alice/learning.sqlite3').exists()
    assert not (browser.data_dir / 'alice/exam_preparation.sqlite3').exists()
    assert not (browser.data_dir / 'alice/subjects/math').exists()


def test_repeat_mistake_updates_the_reading_hint_but_keeps_the_first_added_time(browser):
    assert add_mistakes(browser, [mistake_item([{'text': '薄', 'spoken_text': '单薄的薄'}])]).status_code == 200
    before = get_mistakes(browser).json['items'][0]['words'][0]
    response = add_mistakes(browser, [mistake_item([{'text': '薄', 'spoken_text': '薄雾的薄'}])], browser.parent_headers)
    assert response.json == {'added_count': 0, 'existing_count': 1}
    after = get_mistakes(browser).json['items'][0]['words'][0]
    assert after['added_at'] == before['added_at']
    assert after['spoken_text'] == '薄雾的薄' and after['updated_by'] == 'alice_parent'
    assert after['updated_at'] >= before['updated_at']


def test_multiple_lessons_and_repeated_input_groups_keep_their_source_boundaries(browser):
    items = [mistake_item(['潮水', '繁星', '潮水']), mistake_item(['潮水', '星光'], 'lesson-02'),
             mistake_item([{'text': '潮水', 'spoken_text': '海边的潮水'}])]
    assert add_mistakes(browser, items).json == {'added_count': 4, 'existing_count': 0}
    assert add_mistakes(browser, items).json == {'added_count': 0, 'existing_count': 4}
    payload = get_mistakes(browser).json
    assert (payload['count'], payload['word_count']) == (2, 4)
    first, second = payload['items']
    assert (first['lesson']['title'], second['lesson']['title']) == ('观潮', '繁星')
    assert next(word['spoken_text'] for word in first['words'] if word['text'] == '潮水') == '海边的潮水'
    assert next(word['spoken_text'] for word in second['words'] if word['text'] == '潮水') == '潮水'
    assert remove_mistakes(browser, ['潮水']).json == {'removed_count': 1}
    assert any(word['text'] == '潮水' for word in get_mistakes(browser).json['items'][1]['words'])


def test_cross_book_batches_do_not_collapse_identical_lesson_ids(browser):
    catalog_data = catalog()
    second = deepcopy(catalog_data['books'][0]); second.update(id='grade4-term2', title='四年级下册', term=2)
    catalog_data['books'].append(second)
    browser.catalog_path.write_text(json.dumps(catalog_data, ensure_ascii=False))
    response = add_mistakes(browser, [mistake_item(['潮水']), mistake_item(['潮水'], book_id=second['id'])])
    assert response.json == {'added_count': 2, 'existing_count': 0}
    assert get_mistakes(browser).json['word_count'] == 1
    assert get_mistakes(browser, book_id=second['id']).json['word_count'] == 1


def test_custom_lesson_mistakes_are_private_and_can_be_prepared_by_parents(browser):
    custom = browser.client.post(PREFIX + '/lessons', headers=browser.parent_headers,
                                json={'book_id': BOOK_ID, 'title': '老师补充', 'number': 3}).json['lesson']
    assert add_mistakes(browser, [mistake_item(['星光'], custom['id'])], browser.parent_headers).json['added_count'] == 1
    assert get_mistakes(browser).json['items'][0]['lesson']['title'] == '老师补充'
    assert add_mistakes(browser, [mistake_item(['星光'], custom['id'])], browser.bob_headers).status_code == 404
    assert remove_mistakes(browser, ['星光'], custom['id'], browser.bob_headers).status_code == 404
    assert remove_mistakes(browser, ['星光'], custom['id'], browser.parent_headers).json == {'removed_count': 1}
    assert get_mistakes(browser).json['items'] == []


def test_mistake_removal_is_exact_idempotent_and_leaves_the_source_wordlist(browser):
    assert save(browser, words('AB', 'ab', '潮水')).status_code == 200
    source = browser.client.get(uri(), headers=browser.headers).json['wordlist']
    assert add_mistakes(browser, [mistake_item(['AB', 'ab', '潮水'])]).status_code == 200
    assert remove_mistakes(browser, ['A', '潮']).json == {'removed_count': 0}
    assert remove_mistakes(browser, ['AB', 'AB', '不存在'], headers=browser.parent_headers).json == {'removed_count': 1}
    assert remove_mistakes(browser, ['AB']).json == {'removed_count': 0}
    assert {word['text'] for word in get_mistakes(browser).json['items'][0]['words']} == {'ab', '潮水'}
    assert browser.client.get(uri(), headers=browser.headers).json['wordlist'] == source
    assert remove_mistakes(browser, ['ab', '潮水']).json == {'removed_count': 2}
    assert get_mistakes(browser).json == {'items': [], 'count': 0, 'word_count': 0}
    with sqlite3.connect(database(browser)) as connection:
        assert connection.execute('SELECT COUNT(*) FROM dictation_mistake_groups').fetchone()[0] == 0


def test_mistakes_survive_source_catalog_changes_and_use_current_names_when_available(browser):
    assert add_mistakes(browser).status_code == 200
    current = catalog(); current['books'][0]['lessons'][0]['title'] = '观潮（老师整理）'
    browser.catalog_path.write_text(json.dumps(current, ensure_ascii=False))
    assert get_mistakes(browser).json['items'][0]['lesson']['title'] == '观潮（老师整理）'
    current['books'][0]['lessons'].pop(0)
    browser.catalog_path.write_text(json.dumps(current, ensure_ascii=False))
    assert get_mistakes(browser).json['items'][0]['lesson']['title'] == '观潮'
    assert remove_mistakes(browser, ['潮水']).json == {'removed_count': 1}


def test_mistake_lists_can_accumulate_and_readd_a_full_thousand_words(browser):
    first = ['词' + str(index) for index in range(500)]
    second = ['词' + str(index) for index in range(500, 1000)]
    assert add_mistakes(browser, [mistake_item(first)]).json['added_count'] == 500
    assert add_mistakes(browser, [mistake_item(second)]).json['added_count'] == 500
    assert get_mistakes(browser).json['items'][0]['word_count'] == 1000
    assert add_mistakes(browser, [mistake_item(first + second)]).json == {'added_count': 0, 'existing_count': 1000}
    assert remove_mistakes(browser, first + second).json == {'removed_count': 1000}


def test_an_overflowing_group_rejects_the_entire_multi_lesson_merge(browser):
    assert add_mistakes(browser, [mistake_item(['词' + str(index) for index in range(1000)])]).status_code == 200
    before = get_mistakes(browser).json
    response = add_mistakes(browser, [mistake_item(['另课不应插入'], 'lesson-02'), mistake_item(['超过上限'])])
    assert response.status_code == 400
    assert get_mistakes(browser).json == before


def test_concurrent_mistake_merges_do_not_lose_either_list(browser):
    def merge(text):
        with browser.app.test_client() as client:
            response = client.post(PREFIX + '/mistakes', headers=browser.headers,
                                   json={'items': [mistake_item(['共同词语', text])]})
            return response.status_code, response.json
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(merge, ['潮水', '星光']))
    assert [status for status, _ in results] == [200, 200]
    assert sum(result['added_count'] for _, result in results) == 3
    assert sum(result['existing_count'] for _, result in results) == 1
    assert {word['text'] for word in get_mistakes(browser).json['items'][0]['words']} == {'共同词语', '潮水', '星光'}


def test_route_waits_for_another_workers_first_schema_commit(browser, monkeypatch):
    """Hold real DDL uncommitted so the second route sees an existing empty DB."""
    from contextlib import contextmanager
    from threading import Event, get_ident
    import subject_dictation

    schema_uncommitted, allow_commit, reader_progress = Event(), Event(), Event()
    original_connect = sqlite3.connect
    original_lock = subject_dictation.interprocess_file_lock
    reader_identity, early_results = [], []

    class PausedInitialization(sqlite3.Connection):
        def executescript(self, sql):
            if not schema_uncommitted.is_set():
                assert sql.rstrip().endswith('COMMIT;')
                cursor = super().executescript(sql.rstrip()[:-len('COMMIT;')])
                assert self.in_transaction
                schema_uncommitted.set()
                assert allow_commit.wait(10), 'Test did not release the initializing worker'
                self.commit()
                return cursor
            return super().executescript(sql)

    def connect(*args, **kwargs):
        kwargs['factory'] = PausedInitialization
        return original_connect(*args, **kwargs)

    @contextmanager
    def observed_lock(path):
        if reader_identity and get_ident() == reader_identity[0]:
            # Signal before blocking on the real schema lock, without sleeping.
            reader_progress.set()
        with original_lock(path):
            yield

    monkeypatch.setattr(subject_dictation.sqlite3, 'connect', connect)
    monkeypatch.setattr(subject_dictation, 'interprocess_file_lock', observed_lock)

    def merge(text, reader=False):
        if reader:
            reader_identity.append(get_ident())
        with browser.app.test_client() as client:
            response = client.post(PREFIX + '/mistakes', headers=browser.headers,
                                   json={'items': [mistake_item(['共同词语', text])]})
            result = response.status_code, response.json
            if reader and not allow_commit.is_set():
                early_results.append(result)
                reader_progress.set()
            return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(merge, '潮水')
        try:
            assert schema_uncommitted.wait(5), 'Initializing worker did not reach its schema transaction'
            assert database(browser).is_file()
            reader = pool.submit(merge, '星光', True)
            assert reader_progress.wait(5), 'Concurrent route did not reach its schema check'
            assert early_results == [], 'A concurrent reader treated initialization as corruption: ' + repr(early_results)
        finally:
            allow_commit.set()
        results = [writer.result(timeout=5), reader.result(timeout=5)]
    assert [status for status, _ in results] == [200, 200]
    assert sum(result['added_count'] for _, result in results) == 3
    assert sum(result['existing_count'] for _, result in results) == 1
    assert {word['text'] for word in get_mistakes(browser).json['items'][0]['words']} == {'共同词语', '潮水', '星光'}


def test_read_processing_does_not_hold_the_short_schema_lock(browser):
    assert save(browser).status_code == 200
    store = DictationStore(database(browser))
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        with store._connection() as connection:
            assert connection.execute('SELECT COUNT(*) FROM dictation_wordlists').fetchone()[0] == 1
            writer = pool.submit(store.save_wordlist, BOOK_ID, LESSON, 1, words('另一个页面的更新'), 'alice')
            # The read connection stays open while another worker acquires the
            # schema lock and commits, so it cannot cover the whole read scope.
            assert writer.result(timeout=5)['wordlist']['revision'] == 2
    finally:
        pool.shutdown(wait=True)
    assert store.get_wordlist(BOOK_ID, LESSON)['words'][0]['text'] == '另一个页面的更新'


def test_concurrent_additions_cannot_exceed_the_persisted_lesson_limit(browser):
    assert add_mistakes(browser, [mistake_item(['词' + str(index) for index in range(999)])]).status_code == 200
    def merge(text):
        with browser.app.test_client() as client:
            return client.post(PREFIX + '/mistakes', headers=browser.headers,
                               json={'items': [mistake_item([text])]}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(merge, ['最后一词', '另一个词']))
    assert sorted(statuses) == [200, 400]
    assert get_mistakes(browser).json['word_count'] == 1000


@pytest.mark.parametrize('payload', [
    None, {}, {'items': None}, {'items': {}}, {'items': [], 'student': 'bob'},
    {'items': [mistake_item()] * 101},
    {'items': [{'book_id': BOOK_ID, 'lesson_id': LESSON, 'words': ['潮水']}]},
    {'items': [{**mistake_item(), 'student': 'bob'}]},
    {'items': [mistake_item([{'text': '潮水', 'answer': 'payload'}])]},
    {'items': [mistake_item(['词'] * 1001)]},
    {'items': [mistake_item(['词'] * 1000), mistake_item(['词'] * 1000), mistake_item(['词'])]},
    {'items': [mistake_item(['词' + str(i) for i in range(600)]), mistake_item(['词' + str(i) for i in range(600, 1200)])]},
])
def test_invalid_mistake_batches_are_rejected_without_creating_student_data(browser, payload):
    assert browser.client.post(PREFIX + '/mistakes', headers=browser.headers, json=payload).status_code == 400
    assert not browser.data_dir.exists()


@pytest.mark.parametrize('payload', [
    None, {}, {'words': None}, {'words': {}}, {'words': [], 'student': 'bob'},
    {'words': ['词'] * 1001}, {'words': [None]}, {'words': [{'text': '潮水'}]},
    {'words': ['潮\n水']}, {'words': ['！']},
])
def test_invalid_mistake_removals_do_not_create_files(browser, payload):
    assert browser.client.delete(PREFIX + '/mistakes/' + BOOK_ID + '/' + LESSON,
                                 headers=browser.headers, json=payload).status_code == 400
    assert not browser.data_dir.exists()


def test_unknown_mistake_sources_reject_the_whole_batch_before_any_write(browser):
    for bad in (mistake_item(['词'], 'unknown'), mistake_item(['词'], book_id='unknown'),
                mistake_item(['词'], 'custom_' + 'a' * 32), mistake_item(['词'], '../escape')):
        assert add_mistakes(browser, [mistake_item(), bad]).status_code == 404
    assert get_mistakes(browser, book_id='unknown').status_code == 404
    assert remove_mistakes(browser, ['词'], 'unknown').status_code == 404
    assert not browser.data_dir.exists()


def test_legacy_database_mistake_reads_and_noop_deletes_do_not_migrate_the_schema(browser):
    assert save(browser).status_code == 200
    source = browser.client.get(uri(), headers=browser.headers).json['wordlist']
    with sqlite3.connect(database(browser)) as connection:
        connection.execute('DROP TABLE dictation_mistakes')
        connection.execute('DROP TABLE dictation_mistake_groups')
    before = database(browser).read_bytes()
    assert get_mistakes(browser).json == {'items': [], 'count': 0, 'word_count': 0}
    assert remove_mistakes(browser, ['潮水']).json == {'removed_count': 0}
    assert database(browser).read_bytes() == before
    with sqlite3.connect(database(browser)) as connection:
        assert not connection.execute("SELECT name FROM sqlite_master WHERE name LIKE 'dictation_mistake%'").fetchall()
    assert add_mistakes(browser).status_code == 200
    assert get_mistakes(browser).json['word_count'] == 1
    assert browser.client.get(uri(), headers=browser.headers).json['wordlist'] == source


def test_failed_schema_migration_rolls_back_both_new_tables_and_keeps_legacy_words(browser, monkeypatch):
    import subject_dictation
    assert save(browser).status_code == 200
    source = browser.client.get(uri(), headers=browser.headers).json['wordlist']
    with sqlite3.connect(database(browser)) as connection:
        connection.execute('DROP TABLE dictation_mistakes')
        connection.execute('DROP TABLE dictation_mistake_groups')
    failing_schema = subject_dictation.SCHEMA.replace('CREATE TABLE IF NOT EXISTS dictation_mistakes (',
                                                     'INVALID_MIGRATION_STATEMENT;\nCREATE TABLE IF NOT EXISTS dictation_mistakes (')
    monkeypatch.setattr(subject_dictation, 'SCHEMA', failing_schema)
    assert add_mistakes(browser).status_code == 503
    with sqlite3.connect(database(browser)) as connection:
        assert not connection.execute("SELECT name FROM sqlite_master WHERE name LIKE 'dictation_mistake%'").fetchall()
    assert get_mistakes(browser).json == {'items': [], 'count': 0, 'word_count': 0}
    assert browser.client.get(uri(), headers=browser.headers).json['wordlist'] == source


@pytest.mark.parametrize('statement', [
    'DROP TABLE dictation_mistakes',
    'DROP TABLE dictation_lessons',
    "UPDATE dictation_mistake_groups SET lesson_json='not-json'",
    "UPDATE dictation_mistakes SET spoken_text=''",
    "UPDATE dictation_mistakes SET updated_at='not-a-date'",
    "UPDATE dictation_mistakes SET updated_by=''",
    'DELETE FROM dictation_mistake_groups',
])
def test_corrupted_mistake_content_is_not_hidden_or_overwritten(browser, statement):
    assert add_mistakes(browser).status_code == 200
    with sqlite3.connect(database(browser)) as connection:
        connection.execute(statement)
    before = database(browser).read_bytes()
    assert get_mistakes(browser).status_code == 503
    assert add_mistakes(browser, [mistake_item(['不可覆盖'])]).status_code == 503
    assert remove_mistakes(browser, ['潮水']).status_code == 503
    assert database(browser).read_bytes() == before


@pytest.mark.parametrize('original', [b'', b'broken-sqlite-file'])
def test_malformed_existing_sqlite_file_is_not_reinitialized_as_an_empty_mistake_book(browser, original):
    path = database(browser)
    path.parent.mkdir(parents=True)
    path.write_bytes(original)
    assert get_mistakes(browser).status_code == 503
    assert add_mistakes(browser).status_code == 503
    assert path.read_bytes() == original


def test_mistake_storage_failure_preserves_the_existing_snapshot(browser, monkeypatch):
    assert add_mistakes(browser).status_code == 200
    before = get_mistakes(browser).json
    def unavailable_lock(_):
        raise PermissionError('read-only volume')
    with monkeypatch.context() as patch:
        patch.setattr('subject_dictation.interprocess_file_lock', unavailable_lock)
        assert add_mistakes(browser, [mistake_item(['新词'])]).status_code == 503
        assert remove_mistakes(browser, ['潮水']).status_code == 503
    assert get_mistakes(browser).json == before

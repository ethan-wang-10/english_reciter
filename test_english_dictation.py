"""English paper dictation shares account identity, never learning progress."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from functools import wraps
import io
import json
import sqlite3
from types import SimpleNamespace
import wave

from flask import Flask, g, jsonify, request
import pytest

from english_dictation import BOOK_ID, DEFAULT_LESSON_ID, GRADE4_BOOK_ID, register_english_dictation_routes
from subject_dictation import DictationStore, register_subject_dictation_routes


PREFIX = '/api/subjects/english/dictation'


@pytest.fixture
def browser(tmp_path):
    app = Flask(__name__)
    catalog = tmp_path / 'chinese-books.json'
    catalog.write_text(json.dumps({'schema_version': 1, 'books': [{
        'id': 'chinese-book', 'title': '语文四年级上册', 'grade': 4, 'term': 1, 'edition': '上海2026版',
        'lessons': [{'id': 'lesson-01', 'number': 1, 'title': '观潮'}],
    }]}))
    app.config.update(TESTING=True, DICTATION_CATALOG_FILE=catalog)
    identities = {'alice-token': ('alice', 'alice'), 'parent-token': ('alice', 'alice_parent'),
                  'bob-token': ('bob', 'bob')}

    def token_required(function):
        @wraps(function)
        def authenticated(*args, **kwargs):
            token = request.headers.get('Authorization', '').removeprefix('Bearer ')
            if token not in identities:
                return jsonify({'error': '需要认证'}), 401
            child, login = identities[token]
            g.login_username, g.effective_username = login, child
            g.is_parent = login != child
            return function(child, *args, **kwargs)
        return authenticated

    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as output:
        output.setnchannels(1); output.setsampwidth(2); output.setframerate(22050)
        output.writeframes(b'\0\0' * 32)
    state = SimpleNamespace(ready=True, allowed=True, spoken=[], rate_keys=[], wav=buffer.getvalue())

    def synthesize(text):
        state.spoken.append(text)
        return state.wav

    def rate_allow(key, limit):
        state.rate_keys.append((key, limit))
        return state.allowed

    def forbidden_sanitizer(*args, **kwargs):
        raise AssertionError('The truncating legacy sanitizer must never be called')

    services = SimpleNamespace(DATA_DIR=tmp_path / 'users', token_required=token_required,
                               _rate_allow=rate_allow, _RATE_MAX_TTS_AUDIO=17,
                               piper_runtime_ready=lambda: state.ready, piper_synthesize_wav=synthesize,
                               piper_last_result_metadata=lambda: {'cache_hit': True, 'duration_ms': 12.25},
                               sanitize_tts_text=forbidden_sanitizer)
    register_english_dictation_routes(app, services)
    register_subject_dictation_routes(app, services)
    return SimpleNamespace(app=app, client=app.test_client(), services=services, state=state,
                           data_dir=services.DATA_DIR, headers={'Authorization': 'Bearer alice-token'},
                           parent={'Authorization': 'Bearer parent-token'}, bob={'Authorization': 'Bearer bob-token'})


def word_url(lesson_id=DEFAULT_LESSON_ID, book_id=BOOK_ID):
    return PREFIX + '/wordlists/' + book_id + '/' + lesson_id


def database(browser, name='alice'):
    return browser.data_dir / name / 'subjects/english/dictation.sqlite3'


def entries(*texts):
    return [{'text': value} for value in texts]


def save(browser, words, revision=0, headers=None, lesson_id=DEFAULT_LESSON_ID, book_id=BOOK_ID):
    return browser.client.put(word_url(lesson_id, book_id), headers=headers or browser.headers,
                              json={'base_revision': revision, 'words': words})


def group(words, lesson_id=DEFAULT_LESSON_ID, book_id=BOOK_ID):
    return {'book_id': book_id, 'lesson_id': lesson_id, 'words': words}


def mistakes(browser, items, headers=None):
    return browser.client.post(PREFIX + '/mistakes', headers=headers or browser.headers, json={'items': items})


def read_mistakes(browser, headers=None, book_id=BOOK_ID):
    return browser.client.get(PREFIX + '/mistakes?book_id=' + book_id, headers=headers or browser.headers)


def audio(browser, text='Hello, world!', headers=None):
    return browser.client.post(PREFIX + '/audio', headers=headers or browser.headers, json={'text': text})


def test_legacy_default_list_keeps_its_original_ids_after_the_textbook_directory_is_added(browser):
    books = browser.client.get(PREFIX + '/books', headers=browser.headers)
    assert books.status_code == 200 and books.json['count'] == 2
    book = books.json['items'][1]
    assert (book['id'], book['title']) == (BOOK_ID, '我的英语默写')
    assert book['lessons'] == [{'id': DEFAULT_LESSON_ID, 'title': '我的单词与句子', 'number': None, 'custom': False, 'unit': '我的清单'}]
    for path in ('/lessons?book_id=' + BOOK_ID, '/wordlists?book_id=' + BOOK_ID,
                 '/wordlists/' + BOOK_ID + '/' + DEFAULT_LESSON_ID, '/mistakes?book_id=' + BOOK_ID):
        response = browser.client.get(PREFIX + path, headers=browser.headers)
        assert response.status_code == 200 and 'no-store' in response.headers['Cache-Control']
    assert not browser.data_dir.exists()


def test_grade_four_catalog_has_ten_unified_empty_units_without_creating_storage(browser):
    response = browser.client.get(PREFIX + '/books', headers=browser.headers)
    assert [book['id'] for book in response.json['items']] == [GRADE4_BOOK_ID, BOOK_ID]
    book = response.json['items'][0]
    assert (book['title'], book['grade'], book['term']) == ('英语四年级上册', 4, 1)
    assert '上海五四' in book['edition'] and '2026年7月第一版' in book['edition']
    units = ['My school', 'My classmates', 'Animals and their homes', 'Our birthday', 'Visiting places',
             "It's autumn!", 'My healthy breakfast', 'Be honest', 'What time is it?', 'Weather']
    expected = [{'id': f'unit-{index:02d}', 'title': '默写内容', 'number': None,
                 'custom': False, 'unit': f'Unit {index} · {unit}'}
                for index, unit in enumerate(units, 1)]
    assert book['lessons'] == expected
    directory = browser.client.get(PREFIX + '/lessons?book_id=' + GRADE4_BOOK_ID, headers=browser.parent)
    assert directory.json == {'items': expected, 'count': 10}
    for lesson in expected:
        empty = browser.client.get(word_url(lesson['id'], GRADE4_BOOK_ID), headers=browser.headers)
        assert empty.status_code == 200 and empty.json['wordlist']['words'] == [] and empty.json['wordlist']['revision'] == 0
        assert empty.json['lesson'] == lesson
    assert browser.client.get(PREFIX + '/wordlists?book_id=' + GRADE4_BOOK_ID, headers=browser.headers).json == {'items': [], 'count': 0}
    assert read_mistakes(browser, book_id=GRADE4_BOOK_ID).json == {'items': [], 'count': 0, 'word_count': 0}
    assert not browser.data_dir.exists()


def test_grade_four_unified_list_keeps_words_sentences_and_legacy_lists(browser):
    legacy = save(browser, entries('My original list')).json['wordlist']
    sentence = "It's a beautiful school. " + 'A' * 475
    assert len(sentence) == 500
    prepared = save(browser, entries('school', 'classmate', sentence), headers=browser.parent,
                    lesson_id='unit-01', book_id=GRADE4_BOOK_ID)
    assert prepared.status_code == 200 and prepared.json['wordlist']['updated_by'] == 'alice_parent'
    assert save(browser, entries('helpful'), lesson_id='unit-02', book_id=GRADE4_BOOK_ID).status_code == 200
    assert browser.client.get(word_url(), headers=browser.headers).json['wordlist'] == legacy
    assert browser.client.get(word_url('unit-01', GRADE4_BOOK_ID), headers=browser.headers).json['wordlist']['words'] == [
        {'text': text, 'spoken_text': text} for text in ('school', 'classmate', sentence)]
    listed = browser.client.get(PREFIX + '/wordlists?book_id=' + GRADE4_BOOK_ID, headers=browser.headers).json
    assert [item['lesson_id'] for item in listed['items']] == ['unit-01', 'unit-02']
    assert browser.client.get(PREFIX + '/wordlists?book_id=' + BOOK_ID, headers=browser.headers).json['items'] == [legacy]
    assert browser.client.get(word_url('unit-01', GRADE4_BOOK_ID), headers=browser.bob).json['wordlist']['words'] == []
    assert not database(browser, 'alice_parent').exists() and not database(browser, 'bob').exists()


def test_custom_lessons_are_scoped_to_each_book_and_follow_the_fixed_directory(browser):
    custom = {}
    for book_id in (GRADE4_BOOK_ID, BOOK_ID):
        response = browser.client.post(PREFIX + '/lessons', headers=browser.parent,
                                       json={'book_id': book_id, 'title': 'My additional list', 'number': 1})
        assert response.status_code == 201 and response.json['lesson']['book_id'] == book_id
        custom[book_id] = response.json['lesson']['id']
    assert custom[GRADE4_BOOK_ID] != custom[BOOK_ID]
    for book_id, lesson_id in custom.items():
        directory = browser.client.get(PREFIX + '/lessons?book_id=' + book_id, headers=browser.headers).json['items']
        assert directory[-1]['id'] == lesson_id and directory[-1]['custom'] is True
        assert directory[0]['id'] == ('unit-01' if book_id == GRADE4_BOOK_ID else DEFAULT_LESSON_ID)
        assert save(browser, entries('Own material'), book_id=book_id, lesson_id=lesson_id).status_code == 200
        other_book = BOOK_ID if book_id == GRADE4_BOOK_ID else GRADE4_BOOK_ID
        assert browser.client.get(word_url(lesson_id, other_book), headers=browser.headers).status_code == 404
        assert browser.client.get(word_url(lesson_id, book_id), headers=browser.bob).status_code == 404


def test_mixed_book_mistakes_validate_each_directory_and_keep_exact_sources(browser):
    assert save(browser, entries('school'), book_id=GRADE4_BOOK_ID, lesson_id='unit-01').status_code == 200
    assert save(browser, entries('school')).status_code == 200
    marked = mistakes(browser, [group(entries('school')),
                                group(entries('This is our school.'), 'unit-02', GRADE4_BOOK_ID),
                                group(entries('school'), 'unit-01', GRADE4_BOOK_ID)], headers=browser.parent)
    assert marked.status_code == 200 and marked.json == {'added_count': 3, 'existing_count': 0}
    textbook = read_mistakes(browser, book_id=GRADE4_BOOK_ID).json
    legacy = read_mistakes(browser).json
    assert textbook['word_count'] == 2 and legacy['word_count'] == 1
    assert [item['lesson_id'] for item in textbook['items']] == ['unit-01', 'unit-02']
    assert {item['lesson']['unit'] for item in textbook['items']} == {'Unit 1 · My school', 'Unit 2 · My classmates'}
    assert legacy['items'][0]['lesson']['id'] == DEFAULT_LESSON_ID and legacy['items'][0]['lesson']['unit'] == '我的清单'
    assert all(word['updated_by'] == 'alice_parent' for item in [*textbook['items'], *legacy['items']] for word in item['words'])
    removed = browser.client.delete(PREFIX + '/mistakes/' + GRADE4_BOOK_ID + '/unit-01',
                                    headers=browser.parent, json={'words': ['school']})
    assert removed.json == {'removed_count': 1} and read_mistakes(browser).json == legacy
    assert browser.client.get(word_url('unit-01', GRADE4_BOOK_ID), headers=browser.headers).json['wordlist']['word_count'] == 1
    assert read_mistakes(browser, headers=browser.bob, book_id=GRADE4_BOOK_ID).json['items'] == []


@pytest.mark.parametrize('first_book', [GRADE4_BOOK_ID, BOOK_ID])
def test_invalid_cross_book_list_in_a_mixed_batch_rolls_back_every_item(browser, first_book):
    first = (group(entries('Keep'), 'unit-01', GRADE4_BOOK_ID) if first_book == GRADE4_BOOK_ID
             else group(entries('Keep')))
    invalid = (group(entries('Invalid'), 'unit-01', BOOK_ID) if first_book == GRADE4_BOOK_ID
               else group(entries('Invalid'), DEFAULT_LESSON_ID, GRADE4_BOOK_ID))
    assert mistakes(browser, [first, invalid]).status_code == 404
    assert not browser.data_dir.exists()
    assert mistakes(browser, [first]).status_code == 200
    before = read_mistakes(browser, book_id=first_book).json
    assert mistakes(browser, [group(entries('Must not be added'), first['lesson_id'], first_book), invalid]).status_code == 404
    assert read_mistakes(browser, book_id=first_book).json == before


def test_reused_list_ids_in_two_books_still_have_independent_words_and_mistakes(browser, monkeypatch):
    import english_dictation
    # Future books can reuse a list ID; the database key must include book_id.
    books = deepcopy(english_dictation.BOOKS)
    books[0]['lessons'][0]['id'] = DEFAULT_LESSON_ID
    monkeypatch.setattr(english_dictation, 'BOOKS', books)
    for book_id, text in ((GRADE4_BOOK_ID, 'Textbook content'), (BOOK_ID, 'Original custom content')):
        assert save(browser, entries(text), book_id=book_id).status_code == 200
    marked = mistakes(browser, [group([{'text': 'same', 'spoken_text': 'Textbook reading'}], book_id=GRADE4_BOOK_ID),
                                group([{'text': 'same', 'spoken_text': 'Original reading'}])])
    assert marked.json == {'added_count': 2, 'existing_count': 0}
    assert browser.client.get(word_url(book_id=GRADE4_BOOK_ID), headers=browser.headers).json['wordlist']['words'][0]['text'] == 'Textbook content'
    assert browser.client.get(word_url(), headers=browser.headers).json['wordlist']['words'][0]['text'] == 'Original custom content'
    textbook = read_mistakes(browser, book_id=GRADE4_BOOK_ID).json['items'][0]
    original = read_mistakes(browser).json['items'][0]
    assert textbook['words'][0]['spoken_text'] == 'Textbook reading' and textbook['lesson']['unit'] == 'Unit 1 · My school'
    assert original['words'][0]['spoken_text'] == 'Original reading' and original['lesson']['unit'] == '我的清单'


def test_every_english_route_uses_shared_authentication(browser):
    for method, path, body in (
        ('get', '/books', None), ('get', '/lessons?book_id=' + BOOK_ID, None),
        ('post', '/lessons', {'book_id': BOOK_ID, 'title': 'My list'}),
        ('get', '/wordlists?book_id=' + BOOK_ID, None),
        ('get', '/wordlists/' + BOOK_ID + '/' + DEFAULT_LESSON_ID, None),
        ('put', '/wordlists/' + BOOK_ID + '/' + DEFAULT_LESSON_ID, {'base_revision': 0, 'words': entries('Apple')}),
        ('get', '/mistakes?book_id=' + BOOK_ID, None),
        ('post', '/mistakes', {'items': [group(entries('Apple'))]}),
        ('delete', '/mistakes/' + BOOK_ID + '/' + DEFAULT_LESSON_ID, {'words': ['Apple']}),
        ('post', '/audio', {'text': 'Apple'}),
    ):
        response = getattr(browser.client, method)(PREFIX + path, json=body)
        assert response.status_code == 401
    assert browser.state.spoken == [] and not browser.data_dir.exists()


def test_full_length_sentence_and_hint_survive_save_reload_and_exact_deduplication(browser):
    sentence = 'I ' + 'a' * 484 + " won't forget."
    assert len(sentence) == 500
    hint = 'B' * 500
    response = save(browser, [{'text': sentence, 'spoken_text': hint}, *entries("It's bright, isn't it?", 'Apple', 'apple', 'APPLE', 'Apple', '  Hello   there. ', 'Hello there.', 'e\u0301', 'é')])
    assert response.status_code == 200 and response.json['duplicates_removed'] == 3
    saved = response.json['wordlist']
    assert saved['words'][0] == {'text': sentence, 'spoken_text': hint}
    assert [row['text'] for row in saved['words']] == [sentence, "It's bright, isn't it?", 'Apple', 'apple', 'APPLE', 'Hello there.', 'é']
    assert browser.client.get(word_url(), headers=browser.headers).json['wordlist'] == saved
    assert DictationStore(database(browser), max_text=500, max_spoken=500).get_wordlist(BOOK_ID, DEFAULT_LESSON_ID) == saved


def test_parent_prepares_the_students_default_and_custom_lists_but_other_students_are_isolated(browser):
    prepared = save(browser, entries('Apple', "I can't see the moon."), headers=browser.parent)
    assert prepared.status_code == 200 and prepared.json['wordlist']['updated_by'] == 'alice_parent'
    assert browser.client.get(word_url(), headers=browser.headers).json['wordlist'] == prepared.json['wordlist']
    custom_body = {'book_id': BOOK_ID, 'title': 'My sentences', 'number': 2}
    created = browser.client.post(PREFIX + '/lessons', headers=browser.parent, json=custom_body)
    assert created.status_code == 201 and created.json['lesson']['custom'] is True
    lesson_id = created.json['lesson']['id']
    duplicate = browser.client.post(PREFIX + '/lessons', headers=browser.headers, json=custom_body)
    assert duplicate.status_code == 200 and duplicate.json['duplicate'] and duplicate.json['lesson']['id'] == lesson_id
    assert save(browser, entries('We enjoy watching the stars.'), lesson_id=lesson_id).status_code == 200
    assert browser.client.get(word_url(lesson_id), headers=browser.parent).json['wordlist']['word_count'] == 1
    assert browser.client.get(word_url(lesson_id), headers=browser.bob).status_code == 404
    assert browser.client.get(word_url(), headers=browser.bob).json['wordlist']['words'] == []
    assert not database(browser, 'alice_parent').exists() and not database(browser, 'bob').exists()


@pytest.mark.parametrize('words', [
    None, {}, ['Apple'], [{'text': 'A' * 501}], [{'text': 'A', 'spoken_text': 'B' * 501}],
    [{'text': 'A\nB'}], [{'text': 'A\u200bB'}], [{'text': '!!!'}], [{'text': None}],
    [{'text': 'Apple', 'student': 'bob'}], entries(*map(str, range(401))), entries(*(['Apple'] * 501)),
])
def test_invalid_words_are_rejected_without_creating_student_storage(browser, words):
    assert save(browser, words).status_code == 400
    assert not browser.data_dir.exists()


def test_chinese_lengths_remain_forty_and_one_hundred_in_the_same_app(browser):
    path = '/api/subjects/chinese/dictation/wordlists/chinese-book/lesson-01'
    for payload in ([{'text': 'A' * 41}], [{'text': 'A', 'spoken_text': 'B' * 101}]):
        assert browser.client.put(path, headers=browser.headers, json={'base_revision': 0, 'words': payload}).status_code == 400
    assert save(browser, [{'text': 'A' * 500, 'spoken_text': 'B' * 500}]).status_code == 200
    assert not (browser.data_dir / 'alice/subjects/chinese').exists()


def test_english_preparation_does_not_touch_existing_chinese_learning_exam_or_reward_data(browser):
    sentinels = [browser.data_dir / 'alice' / name for name in (
        'learning.sqlite3', 'exam_preparation.sqlite3', 'gamification.json',
        'subjects/chinese/dictation.sqlite3', 'subjects/chinese/writing.sqlite3', 'subjects/math/practice.sqlite3')]
    for path in sentinels:
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'keep existing student data')
    assert save(browser, entries('Apple')).status_code == 200
    assert mistakes(browser, [group(entries('Apple'))]).status_code == 200
    assert audio(browser, headers=browser.parent).status_code == 200
    assert all(path.read_bytes() == b'keep existing student data' for path in sentinels)


def test_stale_revision_preserves_the_full_sentence(browser):
    original = save(browser, entries('A' * 500)).json['wordlist']
    rejected = save(browser, entries('Overwritten'), revision=0)
    assert rejected.status_code == 409 and rejected.json['code'] == 'conflict'
    assert browser.client.get(word_url(), headers=browser.headers).json['wordlist'] == original


def test_simultaneous_first_wordlist_saves_have_exactly_one_winner(browser):
    def submit(text):
        with browser.app.test_client() as client:
            response = client.put(word_url(), headers=browser.headers, json={'base_revision': 0, 'words': entries(text)})
            return response.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, ['A' * 500, 'B' * 500]))
    assert sorted(outcomes) == [200, 409]
    assert browser.client.get(word_url(), headers=browser.headers).json['wordlist']['revision'] == 1


def test_simultaneous_first_mistake_merges_keep_both_lists(browser):
    def submit(text):
        with browser.app.test_client() as client:
            response = client.post(PREFIX + '/mistakes', headers=browser.headers,
                                   json={'items': [group(entries('Shared', text))]})
            return response.status_code, response.json
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, ['A' * 500, 'B' * 500]))
    assert [code for code, _ in outcomes] == [200, 200]
    assert sum(result['added_count'] for _, result in outcomes) == 3
    assert read_mistakes(browser).json['word_count'] == 3


def test_mistakes_preserve_long_snapshots_and_list_boundaries_and_remove_only_exact_text(browser):
    long = 'A' * 500
    original = save(browser, entries(long, 'Apple', 'apple')).json['wordlist']
    custom = browser.client.post(PREFIX + '/lessons', headers=browser.headers, json={'book_id': BOOK_ID, 'title': 'Extra list'}).json['lesson']
    response = mistakes(browser, [group([{'text': long, 'spoken_text': 'B' * 500}, *entries('Apple', 'apple')]),
                                  group(entries(long), custom['id'])], headers=browser.parent)
    assert response.status_code == 200 and response.json == {'added_count': 4, 'existing_count': 0}
    before = read_mistakes(browser).json
    assert (before['count'], before['word_count']) == (2, 4)
    marked = next(row for row in before['items'] if row['lesson_id'] == DEFAULT_LESSON_ID)
    first = next(word for word in marked['words'] if word['text'] == long)
    assert first['spoken_text'] == 'B' * 500 and first['updated_by'] == 'alice_parent'
    assert mistakes(browser, [group([{'text': long, 'spoken_text': 'C' * 500}])]).json == {'added_count': 0, 'existing_count': 1}
    after = read_mistakes(browser).json
    changed = next(word for row in after['items'] if row['lesson_id'] == DEFAULT_LESSON_ID for word in row['words'] if word['text'] == long)
    assert changed['added_at'] == first['added_at'] and changed['spoken_text'] == 'C' * 500
    endpoint = PREFIX + '/mistakes/' + BOOK_ID + '/' + DEFAULT_LESSON_ID
    assert browser.client.delete(endpoint, headers=browser.parent, json={'words': [long[:-1]]}).json['removed_count'] == 0
    assert browser.client.delete(endpoint, headers=browser.parent, json={'words': [long, 'Apple']}).json['removed_count'] == 2
    remaining = read_mistakes(browser).json
    assert remaining['word_count'] == 2
    assert any(word['text'] == 'apple' for row in remaining['items'] for word in row['words'])
    assert any(word['text'] == long for row in remaining['items'] if row['lesson_id'] == custom['id'] for word in row['words'])
    assert browser.client.get(word_url(), headers=browser.headers).json['wordlist'] == original
    assert read_mistakes(browser, browser.bob).json['items'] == [] and not database(browser, 'bob').exists()


def test_an_invalid_source_rejects_a_whole_mistake_batch_before_writing(browser):
    for bad in ({**group(entries('Bad')), 'book_id': 'unknown'}, group(entries('Bad'), 'unknown'),
                group(entries('Bad'), 'custom_' + 'a' * 32)):
        assert mistakes(browser, [group(entries('Keep')), bad]).status_code == 404
    assert not browser.data_dir.exists()


def test_old_english_database_reads_stay_read_only_and_keep_five_hundred_character_sentences(browser):
    original = save(browser, entries('A' * 500)).json['wordlist']
    with sqlite3.connect(database(browser)) as connection:
        connection.execute('DROP TABLE dictation_mistakes'); connection.execute('DROP TABLE dictation_mistake_groups')
    before = database(browser).read_bytes()
    assert read_mistakes(browser).json == {'items': [], 'count': 0, 'word_count': 0}
    assert database(browser).read_bytes() == before
    assert browser.client.get(word_url(), headers=browser.headers).json['wordlist'] == original
    assert mistakes(browser, [group(entries('A' * 500))]).status_code == 200


@pytest.mark.parametrize('damage', ['empty-file', 'not-sqlite', 'missing-table', 'invalid-word', 'invalid-mistake'])
def test_damaged_english_storage_is_reported_and_not_repaired_or_overwritten(browser, damage):
    path = database(browser)
    if damage in ('empty-file', 'not-sqlite'):
        path.parent.mkdir(parents=True); path.write_bytes(b'' if damage == 'empty-file' else b'broken sqlite')
    else:
        assert save(browser, entries('A' * 500)).status_code == 200
        assert mistakes(browser, [group(entries('A' * 500))]).status_code == 200
        with sqlite3.connect(path) as connection:
            if damage == 'missing-table':
                connection.execute('DROP TABLE dictation_lessons')
            elif damage == 'invalid-word':
                connection.execute('UPDATE dictation_wordlists SET words_json=?', (json.dumps([{'text': 'A' * 501, 'spoken_text': 'A'}]),))
            else:
                connection.execute('UPDATE dictation_mistakes SET spoken_text=?', ('B' * 501,))
    before = path.read_bytes()
    if damage != 'invalid-mistake':
        assert browser.client.get(word_url(), headers=browser.headers).status_code == 503
        assert save(browser, entries('Do not overwrite'), revision=1).status_code == 503
    if damage != 'invalid-word':
        assert read_mistakes(browser).status_code == 503
        assert mistakes(browser, [group(entries('Do not overwrite'))]).status_code == 503
    assert path.read_bytes() == before


@pytest.mark.parametrize('payload', [None, [], {}, {'text': None}, {'text': True}, {'text': ''}, {'text': '   '},
                                     {'text': 'A' * 501}, {'text': 'A\nB'}, {'text': 'A\u200bB'}, {'text': '!!!'},
                                     {'text': 'Hello', 'student': 'bob'}])
def test_audio_rejects_invalid_input_before_rate_limiting_or_synthesis(browser, payload):
    response = browser.client.post(PREFIX + '/audio', headers=browser.headers, json=payload)
    assert response.status_code == 400 and 'no-store' in response.headers['Cache-Control']
    assert browser.state.rate_keys == [] and browser.state.spoken == [] and not browser.data_dir.exists()


def test_audio_preserves_a_full_five_hundred_character_sentence_and_allows_parents(browser):
    text = "I won't " + 'a' * 484 + ' forget.'
    assert len(text) == 500
    response = audio(browser, text, browser.parent)
    assert response.status_code == 200 and response.mimetype == 'audio/wav' and response.data == browser.state.wav
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['X-TTS-Cache'] == 'HIT' and response.headers['Server-Timing'] == 'piper;dur=12.2'
    assert browser.state.spoken == [text]
    assert browser.state.rate_keys == [('tts_audio:alice', 17)]
    assert not browser.data_dir.exists()


def test_audio_rate_limit_is_shared_by_parent_and_student_but_isolated_between_students(browser):
    assert audio(browser).status_code == 200
    browser.state.allowed = False
    blocked = audio(browser, headers=browser.parent)
    assert blocked.status_code == 429 and 'no-store' in blocked.headers['Cache-Control']
    assert len(browser.state.spoken) == 1
    browser.state.allowed = True
    assert audio(browser, headers=browser.bob).status_code == 200
    assert browser.state.rate_keys == [('tts_audio:alice', 17), ('tts_audio:alice', 17), ('tts_audio:bob', 17)]


@pytest.mark.parametrize('mode', ['not-ready', 'missing-ready', 'missing-synthesizer', 'ready-error', 'synthesis-error', 'empty-result', 'wrong-result'])
def test_audio_unavailable_or_failed_engine_returns_recoverable_json_without_learning_state(browser, mode, monkeypatch):
    def unavailable(*args):
        raise OSError('engine unavailable')
    if mode == 'not-ready':
        browser.state.ready = False
    elif mode == 'missing-ready':
        monkeypatch.setattr(browser.services, 'piper_runtime_ready', None)
    elif mode == 'missing-synthesizer':
        monkeypatch.setattr(browser.services, 'piper_synthesize_wav', None)
    elif mode == 'ready-error':
        monkeypatch.setattr(browser.services, 'piper_runtime_ready', unavailable)
    elif mode == 'synthesis-error':
        monkeypatch.setattr(browser.services, 'piper_synthesize_wav', unavailable)
    elif mode == 'empty-result':
        browser.state.wav = b''
    else:
        browser.state.wav = 'invalid WAV result'
    response = audio(browser, headers=browser.parent)
    assert response.status_code == 503 and response.is_json and response.json['error']
    assert 'no-store' in response.headers['Cache-Control'] and not browser.data_dir.exists()


def test_optional_audio_metadata_failure_does_not_discard_valid_audio(browser, monkeypatch):
    def invalid_metadata():
        raise ValueError('optional metadata unavailable')
    monkeypatch.setattr(browser.services, 'piper_last_result_metadata', invalid_metadata)
    response = audio(browser)
    assert response.status_code == 200 and response.data == browser.state.wav
    assert 'Server-Timing' not in response.headers


def retired_store(browser):
    return DictationStore(database(browser), max_text=500, max_spoken=500)


def retired_snapshot(number, kind):
    return {'id': f'unit-{number:02d}-{kind}', 'title': '单词与短语' if kind == 'words' else '句子',
            'number': None, 'custom': False, 'unit': f'Unit {number} · Historical unit'}


def seed_retired(browser, number=1, words=('Shared', 'school'), sentences=('Shared', 'A full sentence.')):
    old = retired_store(browser)
    for kind, texts in (('words', words), ('sentences', sentences)):
        entry = retired_snapshot(number, kind)
        old.save_wordlist(GRADE4_BOOK_ID, entry['id'], 0, entries(*texts), 'alice')
        old.merge_mistakes([{'book_id': GRADE4_BOOK_ID, 'lesson_id': entry['id'], 'lesson': entry,
                             'words': entries(*texts)}], 'alice')


def archived_rows(browser):
    with sqlite3.connect(database(browser)) as connection:
        return {table: connection.execute('SELECT * FROM ' + table + " WHERE book_id=? AND (lesson_id LIKE '%-words' OR lesson_id LIKE '%-sentences') ORDER BY lesson_id,rowid",
                                           (GRADE4_BOOK_ID,)).fetchall()
                for table in ('dictation_wordlists', 'dictation_mistake_groups', 'dictation_mistakes')}


def test_retired_lists_merge_once_with_latest_hints_earliest_mistakes_and_untouched_archives(browser):
    seed_retired(browser)
    old = retired_store(browser)
    old.save_wordlist(GRADE4_BOOK_ID, 'unit-01-words', 1, entries('Shared', 'school'), 'alice_parent')
    with sqlite3.connect(database(browser)) as connection:
        connection.execute("UPDATE dictation_wordlists SET updated_at='2026-11-14T09:00:00' WHERE lesson_id='unit-01-words'")
        connection.execute("UPDATE dictation_wordlists SET updated_at='2026-11-14T10:00:00',words_json=? WHERE lesson_id='unit-01-sentences'",
                           (json.dumps([{'text': 'Shared', 'spoken_text': 'New sentence hint'}, {'text': 'A full sentence.', 'spoken_text': 'A full sentence.'}]),))
        connection.execute("UPDATE dictation_mistakes SET added_at='2026-11-14T09:00:00',updated_at='2026-11-14T12:00:00',spoken_text='Latest marked hint',updated_by='alice_parent' WHERE lesson_id='unit-01-words' AND text='Shared'")
        connection.execute("UPDATE dictation_mistakes SET added_at='2026-11-14T08:00:00',updated_at='2026-11-14T11:00:00' WHERE lesson_id='unit-01-sentences' AND text='Shared'")
    archive = archived_rows(browser)
    legacy = save(browser, entries('Original personal list')).json['wordlist']
    merged = browser.client.get(word_url('unit-01', GRADE4_BOOK_ID), headers=browser.parent)
    assert merged.status_code == 200
    current = merged.json['wordlist']
    assert current['revision'] == 3 and [word['text'] for word in current['words']] == ['Shared', 'school', 'A full sentence.']
    assert current['words'][0]['spoken_text'] == 'New sentence hint'
    notebook = read_mistakes(browser, book_id=GRADE4_BOOK_ID).json
    assert notebook['count'] == 1 and notebook['word_count'] == 3 and notebook['items'][0]['lesson_id'] == 'unit-01'
    shared = next(word for word in notebook['items'][0]['words'] if word['text'] == 'Shared')
    assert (shared['added_at'], shared['updated_at'], shared['spoken_text'], shared['updated_by']) == (
        '2026-11-14T08:00:00', '2026-11-14T12:00:00', 'Latest marked hint', 'alice_parent')
    for _ in range(2):
        assert browser.client.get(word_url('unit-01', GRADE4_BOOK_ID), headers=browser.headers).json['wordlist'] == current
        assert read_mistakes(browser, book_id=GRADE4_BOOK_ID).json == notebook
    assert archived_rows(browser) == archive
    assert browser.client.get(word_url(), headers=browser.headers).json['wordlist'] == legacy
    summaries = browser.client.get(PREFIX + '/wordlists?book_id=' + GRADE4_BOOK_ID, headers=browser.headers).json['items']
    assert [item['lesson_id'] for item in summaries] == ['unit-01']
    for source in ('unit-01-words', 'unit-01-sentences'):
        assert browser.client.get(word_url(source, GRADE4_BOOK_ID), headers=browser.headers).status_code == 200
        rejected = save(browser, entries('Do not overwrite'), revision=1, book_id=GRADE4_BOOK_ID, lesson_id=source)
        assert rejected.status_code == 409 and rejected.json['code'] == 'catalog_updated'
    assert archived_rows(browser) == archive
    assert not database(browser, 'alice_parent').exists()


def test_old_mistake_sources_redirect_acknowledge_both_sources_and_do_not_resurrect_removed_words(browser):
    seed_retired(browser)
    browser.client.get(word_url('unit-01', GRADE4_BOOK_ID), headers=browser.headers)
    old_sources = [group(entries('late shared item'), 'unit-01-words', GRADE4_BOOK_ID),
                   group(entries('late shared item'), 'unit-01-sentences', GRADE4_BOOK_ID)]
    assert mistakes(browser, old_sources).json == {'added_count': 1, 'existing_count': 1}
    removed = browser.client.delete(PREFIX + '/mistakes/' + GRADE4_BOOK_ID + '/unit-01-sentences',
                                    headers=browser.parent, json={'words': ['late shared item', 'Shared']})
    assert removed.json == {'removed_count': 2}
    for _ in range(2):
        notebook = read_mistakes(browser, book_id=GRADE4_BOOK_ID).json
        assert [item['lesson_id'] for item in notebook['items']] == ['unit-01']
        assert {word['text'] for word in notebook['items'][0]['words']} == {'school', 'A full sentence.'}
    current = browser.client.get(word_url('unit-01', GRADE4_BOOK_ID), headers=browser.headers).json['wordlist']
    assert save(browser, [], revision=current['revision'], book_id=GRADE4_BOOK_ID, lesson_id='unit-01').status_code == 200
    assert browser.client.get(word_url('unit-01', GRADE4_BOOK_ID), headers=browser.headers).json['wordlist']['words'] == []


def test_migration_keeps_all_four_hundred_items_and_two_thousand_mistakes(browser):
    old = retired_store(browser)
    for kind in ('words', 'sentences'):
        entry = retired_snapshot(1, kind)
        old.save_wordlist(GRADE4_BOOK_ID, entry['id'], 0, entries(*(kind + str(index) for index in range(200))), 'alice')
        old.merge_mistakes([{'book_id': GRADE4_BOOK_ID, 'lesson_id': entry['id'], 'lesson': entry,
                             'words': entries(*(kind + str(index) for index in range(1000)))}], 'alice')
    response = browser.client.get(word_url('unit-01', GRADE4_BOOK_ID), headers=browser.headers)
    assert response.status_code == 200 and response.json['wordlist']['word_count'] == 400
    assert read_mistakes(browser, book_id=GRADE4_BOOK_ID).json['word_count'] == 2000
    current = response.json['wordlist']
    assert save(browser, current['words'], revision=current['revision'], lesson_id='unit-01', book_id=GRADE4_BOOK_ID).status_code == 200
    assert save(browser, [*current['words'], {'text': 'overflow'}], revision=current['revision'] + 1,
                lesson_id='unit-01', book_id=GRADE4_BOOK_ID).status_code == 400
    all_words = read_mistakes(browser, book_id=GRADE4_BOOK_ID).json['items'][0]['words']
    normalized = [{'text': word['text'], 'spoken_text': word['spoken_text']} for word in all_words]
    assert mistakes(browser, [group(normalized, 'unit-01', GRADE4_BOOK_ID)]).json == {'added_count': 0, 'existing_count': 2000}
    assert mistakes(browser, [group([*normalized, {'text': 'overflow'}], 'unit-01', GRADE4_BOOK_ID)]).status_code == 400
    removed = browser.client.delete(PREFIX + '/mistakes/' + GRADE4_BOOK_ID + '/unit-01', headers=browser.headers,
                                    json={'words': [word['text'] for word in all_words]})
    assert removed.json == {'removed_count': 2000}
    assert read_mistakes(browser, book_id=GRADE4_BOOK_ID).json['word_count'] == 0


def test_mistake_only_migration_creates_an_empty_marker_and_concurrent_readers_merge_once(browser):
    old = retired_store(browser); entry = retired_snapshot(1, 'words')
    old.merge_mistakes([{'book_id': GRADE4_BOOK_ID, 'lesson_id': entry['id'], 'lesson': entry, 'words': entries('Saved mistake')}], 'alice')
    def read(_):
        with browser.app.test_client() as client:
            response = client.get(word_url('unit-01', GRADE4_BOOK_ID), headers=browser.headers)
            return response.status_code, response.json
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(read, range(2)))
    assert responses[0] == responses[1] and responses[0][0] == 200
    assert responses[0][1]['wordlist']['revision'] == 1 and responses[0][1]['wordlist']['words'] == []
    assert read_mistakes(browser, book_id=GRADE4_BOOK_ID).json['word_count'] == 1


def test_invalid_mixed_request_does_not_commit_a_pending_migration(browser):
    seed_retired(browser); before = archived_rows(browser)
    rejected = mistakes(browser, [group(entries('Valid item'), 'unit-01', GRADE4_BOOK_ID),
                                  group(entries('Wrong book'), 'unit-01', BOOK_ID)])
    assert rejected.status_code == 404 and archived_rows(browser) == before
    with sqlite3.connect(database(browser)) as connection:
        assert not connection.execute("SELECT 1 FROM dictation_wordlists WHERE lesson_id='unit-01'").fetchone()


@pytest.mark.parametrize('damage', ['invalid-json', 'invalid-date', 'revision-overflow', 'partial-canonical-mistakes'])
def test_corrupt_or_partial_migration_preserves_every_archive_and_rejects_the_whole_merge(browser, damage):
    seed_retired(browser, 1); seed_retired(browser, 2)
    with sqlite3.connect(database(browser)) as connection:
        if damage == 'invalid-json':
            connection.execute("UPDATE dictation_wordlists SET words_json='broken' WHERE lesson_id='unit-02-words'")
        elif damage == 'invalid-date':
            connection.execute("UPDATE dictation_wordlists SET updated_at='broken' WHERE lesson_id='unit-02-words'")
        elif damage == 'revision-overflow':
            connection.execute("UPDATE dictation_wordlists SET revision=9007199254740991 WHERE lesson_id='unit-02-words'")
        else:
            connection.execute("INSERT INTO dictation_mistake_groups(book_id,lesson_id,lesson_json) VALUES(?,?,?)",
                               (GRADE4_BOOK_ID, 'unit-02', json.dumps({'id': 'unit-02', 'title': '默写内容', 'number': None, 'custom': False})))
            connection.execute("INSERT INTO dictation_mistakes VALUES(?,?,?,?,?,?,?)", (GRADE4_BOOK_ID, 'unit-02', 'Partial', 'Partial', '2026-11-14T09:00:00', '2026-11-14T09:00:00', 'alice'))
    before = archived_rows(browser)
    response = browser.client.get(word_url('unit-01', GRADE4_BOOK_ID), headers=browser.headers)
    assert response.status_code == 503 and archived_rows(browser) == before
    with sqlite3.connect(database(browser)) as connection:
        assert not connection.execute("SELECT 1 FROM dictation_wordlists WHERE lesson_id IN ('unit-01','unit-02')").fetchone()


def test_failure_after_first_unit_writes_rolls_back_all_units_and_mistakes(browser):
    seed_retired(browser, 1); seed_retired(browser, 2); before = archived_rows(browser)
    with sqlite3.connect(database(browser)) as connection:
        connection.execute("CREATE TRIGGER fail_second_unit BEFORE INSERT ON dictation_mistakes WHEN NEW.lesson_id='unit-02' BEGIN SELECT RAISE(ABORT,'simulated disk failure'); END")
    response = browser.client.get(word_url('unit-01', GRADE4_BOOK_ID), headers=browser.headers)
    assert response.status_code == 503 and archived_rows(browser) == before
    with sqlite3.connect(database(browser)) as connection:
        assert not connection.execute("SELECT 1 FROM dictation_wordlists WHERE lesson_id IN ('unit-01','unit-02')").fetchone()
        assert not connection.execute("SELECT 1 FROM dictation_mistake_groups WHERE lesson_id IN ('unit-01','unit-02')").fetchone()

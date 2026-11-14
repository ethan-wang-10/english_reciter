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

from english_dictation import BOOK_ID, DEFAULT_LESSON_ID, register_english_dictation_routes
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


def word_url(lesson_id=DEFAULT_LESSON_ID):
    return PREFIX + '/wordlists/' + BOOK_ID + '/' + lesson_id


def database(browser, name='alice'):
    return browser.data_dir / name / 'subjects/english/dictation.sqlite3'


def entries(*texts):
    return [{'text': value} for value in texts]


def save(browser, words, revision=0, headers=None, lesson_id=DEFAULT_LESSON_ID):
    return browser.client.put(word_url(lesson_id), headers=headers or browser.headers,
                              json={'base_revision': revision, 'words': words})


def group(words, lesson_id=DEFAULT_LESSON_ID):
    return {'book_id': BOOK_ID, 'lesson_id': lesson_id, 'words': words}


def mistakes(browser, items, headers=None):
    return browser.client.post(PREFIX + '/mistakes', headers=headers or browser.headers, json={'items': items})


def read_mistakes(browser, headers=None):
    return browser.client.get(PREFIX + '/mistakes?book_id=' + BOOK_ID, headers=headers or browser.headers)


def audio(browser, text='Hello, world!', headers=None):
    return browser.client.post(PREFIX + '/audio', headers=headers or browser.headers, json={'text': text})


def test_default_list_is_ready_without_creating_a_database_or_inventing_a_textbook(browser):
    books = browser.client.get(PREFIX + '/books', headers=browser.headers)
    assert books.status_code == 200 and books.json['count'] == 1
    book = books.json['items'][0]
    assert (book['id'], book['title']) == (BOOK_ID, '我的英语默写')
    assert book['lessons'] == [{'id': DEFAULT_LESSON_ID, 'title': '我的单词与句子', 'number': None, 'custom': False, 'unit': '我的清单'}]
    for path in ('/lessons?book_id=' + BOOK_ID, '/wordlists?book_id=' + BOOK_ID,
                 '/wordlists/' + BOOK_ID + '/' + DEFAULT_LESSON_ID, '/mistakes?book_id=' + BOOK_ID):
        response = browser.client.get(PREFIX + path, headers=browser.headers)
        assert response.status_code == 200 and 'no-store' in response.headers['Cache-Control']
    assert not browser.data_dir.exists()


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
    [{'text': 'Apple', 'student': 'bob'}], entries(*map(str, range(201))), entries(*(['Apple'] * 501)),
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

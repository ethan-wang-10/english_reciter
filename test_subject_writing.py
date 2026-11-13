"""Writing practice keeps each student's drafts, revisions and milestones intact."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from functools import wraps
import json
import logging
from pathlib import Path
from types import SimpleNamespace

from flask import Flask, g, jsonify, request
import pytest

from subject_writing import WritingError, WritingStore, initial_data, register_subject_writing_routes, validate_data


PREFIX = '/api/subjects/chinese/writing'
LESSON_ID = 'grade4-interesting-recess-game'


def lesson(version=1):
    return {
        'id': LESSON_ID, 'version': version, 'grade': 4,
        'title': '一次有趣的课间游戏' if version == 1 else '更新后的课间游戏',
        'description': '先搭提纲，再补内容。', 'goal': '写清最有趣的一刻。',
        'time_hint': '可以分两到三次。',
        'materials': ['下课铃响了', '同桌猜错了'],
        'stages': [{'id': f's{index}', 'title': f'步骤{index}', 'goal': '写清楚', 'question': '你准备写什么？',
                    'hints': [{'title': '方向', 'text': '想想实际发生的事。'}],
                    'checklist': [{'id': 'clear', 'label': '我写清楚了。'}]} for index in range(6)],
    }


def complete_data():
    data = initial_data()
    data.update(stage=5, structure_started=True, language_started=True, review_round='language')
    data['planning'].update(situation='我和同桌课间玩猜词。', main_idea='一次猜错让我们觉得有趣。')
    data['outline'][1].update(content='同桌把兔子猜成青蛙。', purpose='写出最有趣的一刻。', focus=True)
    data['paragraphs'] = {'p1': '下课后我们玩猜词。', 'p2': '我做动作，他猜错了。'}
    data['structure_paragraphs'] = {'p1': '下课后，我们玩猜词。', 'p2': '我做动作，他猜成青蛙了。'}
    data['final_paragraphs'] = {'p1': '下课后，我们玩猜词。', 'p2': '我竖起两根手指。“青蛙！”同桌说。'}
    data['revision_notes'] = {'structure': '把猜错的过程放在重点段。', 'language': '删掉重复评价。'}
    data['reflection']['learned'] = '动作和对话能让经过更清楚。'
    return data


@pytest.fixture
def browser(tmp_path):
    app = Flask(__name__)
    directory = tmp_path / 'content'
    directory.mkdir()
    path = directory / 'lesson-01-game.json'
    path.write_text(json.dumps(lesson(), ensure_ascii=False))
    app.config.update(TESTING=True, WRITING_LESSON_DIR=directory)
    accounts = {'alice-token': ('alice', False), 'bob-token': ('bob', False), 'parent-token': ('alice', True)}

    def token_required(function):
        @wraps(function)
        def authenticated(*args, **kwargs):
            token = request.headers.get('Authorization', '').removeprefix('Bearer ')
            if token not in accounts:
                return jsonify({'error': '需要认证'}), 401
            username, parent = accounts[token]
            g.is_parent = parent
            g.login_username = username + '_parent' if parent else username
            return function(username, *args, **kwargs)
        return authenticated

    def parent_forbidden(function):
        @wraps(function)
        def student_only(username, *args, **kwargs):
            if g.is_parent:
                return jsonify({'error': '家长只能查看'}), 403
            return function(username, *args, **kwargs)
        return student_only

    data_dir = tmp_path / 'data'
    services = SimpleNamespace(DATA_DIR=data_dir, token_required=token_required,
                               parent_forbidden=parent_forbidden, logger=logging.getLogger(__name__))
    register_subject_writing_routes(app, services)
    return SimpleNamespace(client=app.test_client(), app=app, data_dir=data_dir, services=services,
                           lesson_path=path, headers={'Authorization': 'Bearer alice-token'})


def start(browser, headers=None):
    response = browser.client.post(PREFIX + '/attempts', headers=headers or browser.headers, json={'lesson_id': LESSON_ID})
    assert response.status_code == 201, response.json
    return response.json['attempt']


def database(browser, username='alice'):
    return browser.data_dir / username / 'subjects/chinese/writing.sqlite3'


def test_empty_reads_are_private_and_do_not_create_learning_files(browser):
    for endpoint in ('/lessons', '/lessons/' + LESSON_ID, '/attempts'):
        response = browser.client.get(PREFIX + endpoint, headers=browser.headers)
        assert response.status_code == 200
        assert 'no-store' in response.headers['Cache-Control']
    assert browser.client.get(PREFIX + '/attempts', headers=browser.headers).json == {
        'items': [], 'count': 0, 'read_only': False,
    }
    fake = 'writing_' + 'a' * 32
    assert browser.client.get(PREFIX + '/attempts/' + fake, headers=browser.headers).status_code == 404
    assert not browser.data_dir.exists()
    assert WritingStore(database(browser)).records() == []
    assert not browser.data_dir.exists()


def test_lesson_and_draft_endpoints_require_shared_auth(browser):
    for method, endpoint, payload in (
        ('get', '/lessons', None), ('get', '/attempts', None),
        ('post', '/attempts', {'lesson_id': LESSON_ID}),
    ):
        response = getattr(browser.client, method)(PREFIX + endpoint, json=payload)
        assert response.status_code == 401
    assert not browser.data_dir.exists()


def test_start_creates_initial_snapshot_and_allows_another_draft(browser):
    first, second = start(browser), start(browser)
    assert first['id'] != second['id']
    assert first['id'].startswith('writing_')
    assert first['revision'] == 1 and first['status'] == 'in_progress'
    assert first['data'] == initial_data()
    assert len(first['data']['outline']) == 3
    assert all(not card['focus'] for card in first['data']['outline'])
    response = browser.client.get(PREFIX + '/attempts/' + first['id'], headers=browser.headers)
    assert response.json['attempt'] == first and response.json['read_only'] is False
    assert len(response.json['versions']) == 1
    assert response.json['versions'][0]['label'] == '开始练习'
    listed = browser.client.get(PREFIX + '/attempts', headers=browser.headers).json
    assert listed['count'] == 2
    assert all(item['title'] == lesson()['title'] and item['stage'] == 0 for item in listed['items'])
    assert WritingStore(database(browser)).records() == []


def test_save_reload_keeps_words_reordering_and_orphan_paragraphs(browser):
    attempt = start(browser)
    data = attempt['data']
    data['planning']['situation'] = '先保存我的话\n保留原来的换行。'
    data['outline'] = [data['outline'][1], data['outline'][0]]
    data['paragraphs'] = {'p2': '重点段', 'p3': '删掉提纲卡片后仍应保留的文字。'}
    data['stage'] = 3
    saved = browser.client.patch(PREFIX + '/attempts/' + attempt['id'], headers=browser.headers,
                                 json={'base_revision': 1, 'data': data})
    assert saved.status_code == 200 and saved.json['attempt']['revision'] == 2
    reloaded = WritingStore(database(browser)).get(attempt['id'])
    assert reloaded['attempt']['data'] == data
    assert len(reloaded['versions']) == 1, 'Autosaving must not create a milestone for every keystroke'


def test_student_and_parent_reads_are_scoped_to_the_effective_child(browser):
    attempt = start(browser)
    endpoint = PREFIX + '/attempts/' + attempt['id']
    parent = {'Authorization': 'Bearer parent-token'}
    other = {'Authorization': 'Bearer bob-token'}
    assert browser.client.get(endpoint, headers=parent).json['read_only'] is True
    assert browser.client.get(PREFIX + '/attempts', headers=parent).json['count'] == 1
    assert browser.client.get(endpoint, headers=other).status_code == 404
    assert browser.client.get(PREFIX + '/attempts', headers=other).json['items'] == []
    assert not (browser.data_dir / 'bob').exists()
    version = browser.client.get(endpoint, headers=parent).json['versions'][0]
    assert browser.client.get(endpoint + '/versions/' + version['id'], headers=parent).status_code == 200
    assert browser.client.get(endpoint + '/versions/' + version['id'], headers=other).status_code == 404


@pytest.mark.parametrize('method,suffix,payload', [
    ('post', '', {'lesson_id': LESSON_ID}),
    ('patch', '/attempt', {'base_revision': 1, 'data': initial_data()}),
    ('post', '/attempt/checkpoints', {'base_revision': 1, 'data': initial_data(), 'label': '提纲'}),
    ('post', '/attempt/complete', {'base_revision': 1, 'data': complete_data()}),
    ('post', '/attempt/restore', {'base_revision': 1, 'version_id': 'version_' + 'a' * 32}),
])
def test_parent_cannot_create_or_change_student_words(browser, method, suffix, payload):
    attempt = start(browser)
    suffix = suffix.replace('/attempt', '/' + attempt['id'])
    response = getattr(browser.client, method)(PREFIX + '/attempts' + suffix,
                                              headers={'Authorization': 'Bearer parent-token'}, json=payload)
    assert response.status_code == 403
    assert WritingStore(database(browser)).get(attempt['id'])['attempt'] == attempt


def test_conflict_does_not_overwrite_either_version(browser):
    attempt = start(browser)
    first, second = initial_data(), initial_data()
    first['planning']['materials'] = '第一页面的内容'
    second['planning']['materials'] = '第二页面的内容'
    endpoint = PREFIX + '/attempts/' + attempt['id']
    assert browser.client.patch(endpoint, headers=browser.headers, json={'base_revision': 1, 'data': first}).status_code == 200
    conflict = browser.client.patch(endpoint, headers=browser.headers, json={'base_revision': 1, 'data': second})
    assert conflict.status_code == 409
    assert conflict.json['code'] == 'conflict' and conflict.json['current_revision'] == 2
    assert WritingStore(database(browser)).get(attempt['id'])['attempt']['data'] == first
    assert second['planning']['materials'] == '第二页面的内容'


def test_two_workers_cannot_both_save_the_same_base_revision(tmp_path):
    path = tmp_path / 'writing.sqlite3'
    attempt = WritingStore(path).start(lesson())

    def save(text):
        data = initial_data()
        data['planning']['materials'] = text
        try:
            result = WritingStore(path).save(attempt['id'], 1, data)
            return result['attempt']['data']['planning']['materials']
        except WritingError as error:
            assert error.status == 409 and error.details['code'] == 'conflict'
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ['甲页面', '乙页面']))
    assert sum(item is not None for item in results) == 1
    persisted = WritingStore(path).get(attempt['id'])['attempt']
    assert persisted['revision'] == 2
    assert persisted['data']['planning']['materials'] == next(item for item in results if item is not None)


def test_checkpoint_restore_and_lesson_snapshots_are_immutable(browser):
    attempt = start(browser)
    endpoint = PREFIX + '/attempts/' + attempt['id']
    original_version = browser.client.get(endpoint, headers=browser.headers).json['versions'][0]
    data = initial_data()
    data['planning']['main_idea'] = '我自己的中心意思'
    checkpoint = browser.client.post(endpoint + '/checkpoints', headers=browser.headers,
                                     json={'base_revision': 1, 'data': data, 'label': '提纲定稿'})
    assert checkpoint.status_code == 200
    version = checkpoint.json['versions'][0]
    assert version['label'] == '提纲定稿' and version['revision'] == 2
    later = deepcopy(data)
    later['paragraphs']['p2'] = '还没保存成版本的正文也不能丢。'
    assert browser.client.patch(endpoint, headers=browser.headers, json={'base_revision': 2, 'data': later}).status_code == 200
    browser.lesson_path.write_text(json.dumps(lesson(2), ensure_ascii=False))
    restored = browser.client.post(endpoint + '/restore', headers=browser.headers,
                                   json={'base_revision': 3, 'version_id': original_version['id']})
    assert restored.status_code == 200
    assert restored.json['attempt']['data'] == initial_data()
    assert restored.json['attempt']['revision'] == 4
    assert restored.json['attempt']['lesson_snapshot']['title'] == lesson()['title']
    versions = restored.json['versions']
    assert len(versions) == 4
    before = next(item for item in versions if item['label'] == '恢复前的草稿')
    assert browser.client.get(endpoint + '/versions/' + before['id'], headers=browser.headers).json['version']['data'] == later
    assert browser.client.get(endpoint + '/versions/' + version['id'], headers=browser.headers).json['version']['data'] == data
    assert start(browser)['lesson_snapshot']['title'] == lesson(2)['title']


def test_versions_cannot_be_read_or_restored_through_another_attempt(browser):
    first, second = start(browser), start(browser)
    version = WritingStore(database(browser)).get(first['id'])['versions'][0]
    endpoint = PREFIX + '/attempts/' + second['id']
    assert browser.client.get(endpoint + '/versions/' + version['id'], headers=browser.headers).status_code == 404
    assert browser.client.post(endpoint + '/restore', headers=browser.headers,
                               json={'base_revision': 1, 'version_id': version['id']}).status_code == 404
    assert WritingStore(database(browser)).get(second['id'])['attempt']['revision'] == 1


@pytest.mark.parametrize('label', [None, '', '   ', 1, '字' * 81])
def test_checkpoint_requires_a_real_label_and_keeps_revision_on_failure(browser, label):
    attempt = start(browser)
    response = browser.client.post(PREFIX + '/attempts/' + attempt['id'] + '/checkpoints', headers=browser.headers,
                                   json={'base_revision': 1, 'data': initial_data(), 'label': label})
    assert response.status_code == 400
    result = WritingStore(database(browser)).get(attempt['id'])
    assert result['attempt'] == attempt
    assert len(result['versions']) == 1


def test_completion_checks_readiness_without_grading_and_preserves_completed_work(browser):
    attempt = start(browser)
    endpoint = PREFIX + '/attempts/' + attempt['id']
    incomplete = browser.client.post(endpoint + '/complete', headers=browser.headers,
                                     json={'base_revision': 1, 'data': initial_data()})
    assert incomplete.status_code == 400 and incomplete.json['code'] == 'incomplete'
    assert 'reflection.learned' in incomplete.json['missing']
    assert WritingStore(database(browser)).get(attempt['id'])['attempt']['revision'] == 1
    data = complete_data()
    completed = browser.client.post(endpoint + '/complete', headers=browser.headers,
                                    json={'base_revision': 1, 'data': data})
    assert completed.status_code == 200
    work = completed.json['attempt']
    assert work['status'] == 'completed' and work['completed_at']
    assert work['data'] == data
    assert 'score' not in work
    assert completed.json['versions'][0]['label'] == '完成稿'
    records = WritingStore(database(browser)).records()
    assert len(records) == 1 and records[0]['kind'] == 'writing'
    assert records[0]['id'] == attempt['id'] and records[0]['status'] == 'completed'
    assert not {'score', 'correct_count', 'total'} & set(records[0])
    for suffix, method, payload in (
        ('', 'patch', {'base_revision': 2, 'data': data}),
        ('/checkpoints', 'post', {'base_revision': 2, 'data': data, 'label': '覆盖旧稿'}),
        ('/complete', 'post', {'base_revision': 2, 'data': data}),
        ('/restore', 'post', {'base_revision': 2, 'version_id': completed.json['versions'][0]['id']}),
    ):
        response = getattr(browser.client, method)(endpoint + suffix, headers=browser.headers, json=payload)
        assert response.status_code == 409 and response.json['code'] == 'completed'
    assert WritingStore(database(browser)).get(attempt['id'])['attempt'] == work


def test_no_change_is_a_valid_reflection_and_not_every_card_needs_full_text(browser):
    attempt = start(browser)
    data = complete_data()
    data['revision_notes'] = {'structure': '', 'language': ''}
    data['no_change'] = {'structure': True, 'language': True}
    data['outline'] = [data['outline'][1]]
    assert browser.client.post(PREFIX + '/attempts/' + attempt['id'] + '/complete', headers=browser.headers,
                               json={'base_revision': 1, 'data': data}).status_code == 200


def test_orphaned_paragraphs_alone_do_not_satisfy_completion(browser):
    attempt = start(browser)
    data = complete_data()
    data['outline'] = [{'id': 'new', 'content': '新的安排', 'purpose': '', 'focus': True}]
    response = browser.client.post(PREFIX + '/attempts/' + attempt['id'] + '/complete', headers=browser.headers,
                                   json={'base_revision': 1, 'data': data})
    assert response.status_code == 400
    assert {'paragraphs', 'final_paragraphs'} <= set(response.json['missing'])


@pytest.mark.parametrize('mutation', [
    lambda data: data.update(stage=True),
    lambda data: data.update(stage=6),
    lambda data: data.update(mode='unknown'),
    lambda data: data.update(extra='not accepted'),
    lambda data: data['planning'].update(main_idea='字' * 2001),
    lambda data: data['outline'].append(deepcopy(data['outline'][0])),
    lambda data: data['outline'][0].update(focus='yes'),
    lambda data: data.update(outline=[{'id': 'p' + str(i), 'content': '', 'purpose': '', 'focus': False} for i in range(9)]),
    lambda data: data['paragraphs'].update(p1='字' * 6001),
    lambda data: data.update(paragraphs={'p' + str(i): '' for i in range(33)}),
    lambda data: data['checks'].update(structure=['same', 'same']),
    lambda data: data['no_change'].update(structure='no'),
    lambda data: data.update(review_round='style'),
    lambda data: data.update(structure_started=1),
])
def test_malformed_updates_never_replace_a_saved_draft(browser, mutation):
    attempt = start(browser)
    data = initial_data()
    mutation(data)
    response = browser.client.patch(PREFIX + '/attempts/' + attempt['id'], headers=browser.headers,
                                    json={'base_revision': 1, 'data': data})
    assert response.status_code == 400
    assert WritingStore(database(browser)).get(attempt['id'])['attempt'] == attempt


def test_total_size_limit_rejects_large_but_individually_valid_fields():
    data = initial_data()
    data['paragraphs'] = {'p' + str(i): '字' * 5000 for i in range(8)}
    with pytest.raises(WritingError) as failure:
        validate_data(data)
    assert failure.value.status == 400


@pytest.mark.parametrize('revision', [None, True, 0, -1, 1.5, '1'])
def test_invalid_revision_cannot_update(browser, revision):
    attempt = start(browser)
    response = browser.client.patch(PREFIX + '/attempts/' + attempt['id'], headers=browser.headers,
                                    json={'base_revision': revision, 'data': initial_data()})
    assert response.status_code == 400
    assert WritingStore(database(browser)).get(attempt['id'])['attempt'] == attempt


def test_unknown_resources_wrong_subjects_and_json_shapes_are_safe(browser):
    assert browser.client.get(PREFIX + '/lessons/not-a-lesson', headers=browser.headers).status_code == 404
    assert browser.client.post(PREFIX + '/attempts', headers=browser.headers, json={'lesson_id': 'not-a-lesson'}).status_code == 404
    assert browser.client.post(PREFIX + '/attempts', headers=browser.headers, json=[]).status_code == 400
    for subject in ('math', 'english'):
        assert browser.client.post('/api/subjects/' + subject + '/writing/attempts', headers=browser.headers,
                                   json={'lesson_id': LESSON_ID}).status_code == 404
    assert not browser.data_dir.exists()


def test_bad_lesson_does_not_create_a_student_database(browser):
    browser.lesson_path.write_text('{broken')
    assert browser.client.get(PREFIX + '/lessons', headers=browser.headers).status_code == 503
    assert browser.client.post(PREFIX + '/attempts', headers=browser.headers, json={'lesson_id': LESSON_ID}).status_code == 503
    assert not browser.data_dir.exists()


def test_corrupt_existing_database_is_reported_and_never_reinitialized(tmp_path):
    path = tmp_path / 'writing.sqlite3'
    original = b'preserve this damaged database exactly'
    path.write_bytes(original)
    with pytest.raises(WritingError) as error:
        WritingStore(path).list_attempts()
    assert error.value.status == 503
    assert path.read_bytes() == original


def test_writing_does_not_change_english_or_math_storage(browser):
    sentinels = [browser.data_dir / 'alice/learning.sqlite3', browser.data_dir / 'alice/subjects/math/practice.sqlite3']
    for path in sentinels:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'untouched existing student data')
    attempt = start(browser)
    WritingStore(database(browser)).save(attempt['id'], 1, complete_data(), complete=True)
    assert all(path.read_bytes() == b'untouched existing student data' for path in sentinels)


def test_completed_writing_joins_chinese_records_without_a_score_or_math_leak(browser):
    from subject_portal import register_subject_portal_routes
    content = browser.lesson_path.parent / 'subjects'
    for subject in ('chinese', 'math'):
        folder = content / subject
        folder.mkdir(parents=True)
        (folder / 'catalog.json').write_text(json.dumps({
            'schema_version': 1, 'subject': subject, 'papers': [], 'knowledge': [],
        }))
    browser.app.config['SUBJECT_CONTENT_DIR'] = content
    register_subject_portal_routes(browser.app, browser.services)
    attempt = start(browser)
    endpoint = '/api/subjects/chinese/records'
    assert browser.client.get(endpoint, headers=browser.headers).json['items'] == []
    completed = browser.client.post(PREFIX + '/attempts/' + attempt['id'] + '/complete', headers=browser.headers,
                                    json={'base_revision': 1, 'data': complete_data()})
    assert completed.status_code == 200
    records = browser.client.get(endpoint, headers=browser.headers).json
    assert records['count'] == 1
    record = records['items'][0]
    assert record['id'] == attempt['id'] and record['kind'] == 'writing'
    assert record['paper_title'] == '作文专项：' + lesson()['title']
    assert not {'score', 'correct_count', 'total'} & set(record)
    parent = browser.client.get(endpoint, headers={'Authorization': 'Bearer parent-token'})
    assert parent.json == records
    assert browser.client.get(endpoint, headers={'Authorization': 'Bearer bob-token'}).json['items'] == []
    assert browser.client.get('/api/subjects/math/records', headers=browser.headers).json['items'] == []
    assert not (browser.data_dir / 'alice/subjects/math').exists()

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

from subject_writing import WritingError, WritingStore, initial_data, initial_guidance, register_subject_writing_routes, validate_data, validate_lesson


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


def guided_lesson():
    return json.loads((Path(__file__).resolve().parent / 'subject_content/chinese/writing/lesson-01-game.json').read_text())


@pytest.fixture
def guided_browser(browser):
    browser.lesson_path.write_text(json.dumps(guided_lesson(), ensure_ascii=False))
    browser.headers = {**browser.headers, 'X-Writing-Flow': 'guided-v1'}
    return browser


def start_guided(browser):
    response = browser.client.post(PREFIX + '/attempts', headers=browser.headers,
                                   json={'lesson_id': LESSON_ID, 'flow_version': 1})
    assert response.status_code == 201, response.json
    return response.json['attempt']


def guided_complete_data():
    data = initial_data(guided_lesson())
    data.update(stage=5, structure_started=True, language_started=True, review_round='language')
    data['guidance'].update(step=8, confirmed=list(range(9)), theme='fun', structure='sequence',
                            sentence='我竖起两根手指，同桌却叫了一声青蛙。',
                            paragraph='我把手指竖在头顶，同桌猜成了青蛙，我们都笑了。', endingOkay=True)
    data['guidance']['slots'] = {'begin': ['start'], 'middle': ['frog'], 'end': ['joy']}
    data['paragraphs'] = {'p_full': '课间我们玩猜词。我竖起手指，同桌猜成青蛙，我们都笑了。铃响了，我还觉得好笑。'}
    data['structure_paragraphs'] = deepcopy(data['paragraphs'])
    data['final_paragraphs'] = deepcopy(data['paragraphs'])
    data['revision_notes']['structure'] = '补上猜错后的反应，让重点更清楚。'
    data['no_change']['language'] = True
    data['reflection']['learned'] = '先确定最想表达的感受，再用细节把意思写清楚。'
    return data


def test_published_lesson_has_nine_question_configuration_and_commented_original_examples():
    lesson = validate_lesson(guided_lesson())
    assert lesson['version'] == 2
    assert [stage['id'] for stage in lesson['stages']] == ['planning', 'outline', 'focus', 'draft', 'revise', 'reflect']
    config = lesson['guidance']
    assert config['version'] == 1 and [step['id'] for step in config['steps']] == list(range(9))
    assert {item['id'] for item in config['structures']} == {'total', 'sequence', 'final'}
    assert {item['id'] for item in config['examples']} == {'total', 'sequence'}
    assert all(len(item['paragraphs']) == 3 and all(part['text'] and part['hint'] for part in item['paragraphs']) for item in config['examples'])
    assert '自己的游戏' in config['transfer']['hint']


def test_new_guided_attempt_starts_empty_and_uses_one_canonical_full_draft(guided_browser):
    attempt = start_guided(guided_browser)
    assert attempt['lesson_snapshot']['version'] == 2 and 'guidance' in attempt['lesson_snapshot']
    assert attempt['data']['guidance'] == initial_guidance()
    assert attempt['data']['outline'] == [{'id': 'p_full', 'content': '', 'purpose': '整篇初稿（开头—重点—结尾）', 'focus': True}]
    assert attempt['data']['paragraphs'] == {}
    assert 'draft' not in attempt['data']['guidance']
    summaries = guided_browser.client.get(PREFIX + '/lessons', headers=guided_browser.headers).json['items']
    assert summaries[0]['flow_version'] == 1
    initial = WritingStore(database(guided_browser)).get(attempt['id'])['versions'][0]
    assert WritingStore(database(guided_browser)).get_version(attempt['id'], initial['id'])['data'] == attempt['data']


def test_already_open_classic_client_can_still_start_and_complete_the_current_lesson(guided_browser):
    guided_browser.headers = {'Authorization': 'Bearer alice-token'}
    attempt = start(guided_browser)
    assert attempt['lesson_snapshot']['version'] == 2 and 'guidance' not in attempt['lesson_snapshot']
    assert attempt['data'] == initial_data()
    complete = guided_browser.client.post(PREFIX + '/attempts/' + attempt['id'] + '/complete', headers=guided_browser.headers,
                                          json={'base_revision': 1, 'data': complete_data()})
    assert complete.status_code == 200 and 'guidance' not in complete.json['attempt']['data']


@pytest.mark.parametrize('flow', [None, True, False, 0, 2, '1', {}, []])
def test_invalid_flow_negotiation_is_rejected_before_creating_a_student_database(guided_browser, flow):
    response = guided_browser.client.post(PREFIX + '/attempts', headers=guided_browser.headers,
                                          json={'lesson_id': LESSON_ID, 'flow_version': flow})
    assert response.status_code == 400 and not guided_browser.data_dir.exists()


def test_guided_flow_is_not_silently_added_to_a_legacy_lesson(browser):
    response = browser.client.post(PREFIX + '/attempts', headers=browser.headers,
                                   json={'lesson_id': LESSON_ID, 'flow_version': 1})
    assert response.status_code == 400 and not browser.data_dir.exists()


@pytest.mark.parametrize('capability', [None, 'legacy', 'guided-v2'])
def test_guided_attempt_routes_require_the_capability_without_changing_saved_work(guided_browser, capability):
    # The explicit POST flow version itself declares creation capability.
    auth = {'Authorization': 'Bearer alice-token'}
    response = guided_browser.client.post(PREFIX + '/attempts', headers=auth,
                                          json={'lesson_id': LESSON_ID, 'flow_version': 1})
    assert response.status_code == 201
    attempt = response.json['attempt']; endpoint = PREFIX + '/attempts/' + attempt['id']
    store = WritingStore(database(guided_browser)); before = store.get(attempt['id'])
    version_id = before['versions'][0]['id']
    headers = {**auth, **({'X-Writing-Flow': capability} if capability is not None else {})}
    for method, suffix, payload in (
        ('get', '', None),
        ('patch', '', {'base_revision': 1, 'data': guided_complete_data()}),
        ('post', '/checkpoints', {'base_revision': 1, 'data': guided_complete_data(), 'label': '不能被旧页面覆盖'}),
        ('post', '/complete', {'base_revision': 1, 'data': guided_complete_data()}),
        ('get', '/versions/' + version_id, None),
        ('post', '/restore', {'base_revision': 1, 'version_id': version_id}),
    ):
        rejected = getattr(guided_browser.client, method)(endpoint + suffix, headers=headers, json=payload)
        assert rejected.status_code == 409 and rejected.json['code'] == 'upgrade_required'
        assert '刷新' in rejected.json['error'] and 'no-store' in rejected.headers['Cache-Control']
        assert store.get(attempt['id']) == before
    for path in ('/lessons', '/lessons/' + LESSON_ID, '/attempts'):
        assert guided_browser.client.get(PREFIX + path, headers=headers).status_code == 200


def test_capable_client_can_read_save_checkpoint_restore_and_complete_guided_work(guided_browser):
    attempt = start_guided(guided_browser); endpoint = PREFIX + '/attempts/' + attempt['id']
    headers = guided_browser.headers
    opened = guided_browser.client.get(endpoint, headers=headers)
    assert opened.status_code == 200 and opened.json['attempt'] == attempt
    initial_version = opened.json['versions'][0]['id']
    state = deepcopy(attempt['data']); state['guidance']['customTheme'] = '我自己的主旨'
    assert guided_browser.client.patch(endpoint, headers=headers, json={'base_revision': 1, 'data': state}).status_code == 200
    state['guidance']['sentence'] = '我写的一句话。'
    checkpoint = guided_browser.client.post(endpoint + '/checkpoints', headers=headers,
                                            json={'base_revision': 2, 'data': state, 'label': '自己的句子'})
    assert checkpoint.status_code == 200
    version_id = checkpoint.json['versions'][0]['id']
    version = guided_browser.client.get(endpoint + '/versions/' + version_id, headers=headers)
    assert version.status_code == 200 and version.json['version']['data'] == state
    restored = guided_browser.client.post(endpoint + '/restore', headers=headers,
                                          json={'base_revision': 3, 'version_id': initial_version})
    assert restored.status_code == 200 and restored.json['attempt']['data'] == attempt['data']
    completed = guided_browser.client.post(endpoint + '/complete', headers=headers,
                                           json={'base_revision': 4, 'data': guided_complete_data()})
    assert completed.status_code == 200 and completed.json['attempt']['status'] == 'completed'


def test_guided_capability_never_bypasses_shared_auth_parent_permissions_or_student_isolation(guided_browser):
    attempt = start_guided(guided_browser); endpoint = PREFIX + '/attempts/' + attempt['id']
    version_id = WritingStore(database(guided_browser)).get(attempt['id'])['versions'][0]['id']
    parent = {'Authorization': 'Bearer parent-token'}
    assert guided_browser.client.get(endpoint, headers=parent).json['code'] == 'upgrade_required'
    assert guided_browser.client.patch(endpoint, headers=parent, json={'base_revision': 1, 'data': guided_complete_data()}).status_code == 403
    for capability in (None, 'guided-v1'):
        other = {'Authorization': 'Bearer bob-token', **({'X-Writing-Flow': capability} if capability else {})}
        assert guided_browser.client.get(endpoint, headers=other).status_code == 404
        assert guided_browser.client.get(endpoint + '/versions/' + version_id, headers=other).status_code == 404
    assert guided_browser.client.get(endpoint, headers={'X-Writing-Flow': 'guided-v1'}).status_code == 401
    assert not (guided_browser.data_dir / 'bob').exists()


@pytest.mark.parametrize('capability', [None, 'classic-v0', 'guided-v2'])
def test_classic_attempt_routes_remain_compatible_with_every_capability_header(browser, capability):
    attempt = start(browser); endpoint = PREFIX + '/attempts/' + attempt['id']
    headers = {**browser.headers, **({'X-Writing-Flow': capability} if capability else {})}
    response = browser.client.get(endpoint, headers=headers)
    assert response.status_code == 200
    initial_version = response.json['versions'][0]['id']
    assert browser.client.patch(endpoint, headers=headers, json={'base_revision': 1, 'data': initial_data()}).status_code == 200
    saved = browser.client.post(endpoint + '/checkpoints', headers=headers,
                                json={'base_revision': 2, 'data': initial_data(), 'label': '旧版草稿'})
    assert saved.status_code == 200
    assert browser.client.get(endpoint + '/versions/' + saved.json['versions'][0]['id'], headers=headers).status_code == 200
    assert browser.client.post(endpoint + '/restore', headers=headers,
                               json={'base_revision': 3, 'version_id': initial_version}).status_code == 200
    assert browser.client.post(endpoint + '/complete', headers=headers,
                               json={'base_revision': 4, 'data': complete_data()}).status_code == 200


def test_existing_v1_attempts_and_snapshots_remain_classic_after_the_catalog_updates(browser):
    old = start(browser)
    initial_version = WritingStore(database(browser)).get(old['id'])['versions'][0]
    browser.lesson_path.write_text(json.dumps(guided_lesson(), ensure_ascii=False))
    assert WritingStore(database(browser)).get(old['id'])['attempt'] == old
    assert 'guidance' not in WritingStore(database(browser)).get_version(old['id'], initial_version['id'])['data']
    work = complete_data()
    saved = WritingStore(database(browser)).save(old['id'], 1, work, label='旧版重点段')
    assert saved['attempt']['data'] == work and saved['attempt']['lesson_snapshot']['version'] == 1
    restored = WritingStore(database(browser)).restore(old['id'], 2, initial_version['id'])
    assert restored['attempt']['data'] == initial_data()
    assert 'guidance' in start_guided(browser)['data']


def test_legacy_patch_and_checkpoint_omissions_preserve_existing_guidance_in_the_same_revision(guided_browser):
    attempt = start_guided(guided_browser)
    data = guided_complete_data()
    data['guidance']['review'] = [6, 7, 8]
    saved = WritingStore(database(guided_browser)).save(attempt['id'], 1, data)
    old_client = deepcopy(data); old_client.pop('guidance')
    old_client['paragraphs']['p_full'] += '\n这是旧客户端刚补充的内容。'
    result = WritingStore(database(guided_browser)).save(attempt['id'], 2, old_client, label='兼容保存')
    assert result['attempt']['revision'] == 3
    assert result['attempt']['data']['guidance'] == saved['attempt']['data']['guidance']
    assert result['attempt']['data']['paragraphs'] == old_client['paragraphs']
    version = result['versions'][0]
    assert WritingStore(database(guided_browser)).get_version(attempt['id'], version['id'])['data'] == result['attempt']['data']


@pytest.mark.parametrize('mutation', [
    lambda data: data.update(guidance=None),
    lambda data: data['guidance'].update(version=True),
    lambda data: data['guidance'].update(version=2),
    lambda data: data['guidance'].update(step=9),
    lambda data: data['guidance'].update(step=True),
    lambda data: data['guidance'].update(confirmed=[0, 0]),
    lambda data: data['guidance'].update(confirmed=[True]),
    lambda data: data['guidance'].update(review=[0]),
    lambda data: data['guidance'].update(theme='unknown'),
    lambda data: data['guidance'].update(structure='unknown'),
    lambda data: data['guidance']['slots'].update(middle=['frog', 'again']),
    lambda data: data['guidance']['slots'].update(begin=['missing']),
    lambda data: data['guidance']['notes'].update(extra='unknown'),
    lambda data: data['guidance'].update(details=['ears', 'ears']),
    lambda data: data['guidance'].update(details=['unknown']),
    lambda data: data['guidance'].update(customTheme='字' * 2001),
    lambda data: data['guidance'].update(ownDetail='字' * 2001),
    lambda data: data['guidance']['notes'].update(begin='字' * 2001),
    lambda data: data['guidance'].update(sentence='字' * 6001),
    lambda data: data['guidance'].update(paragraph='字' * 6001),
    lambda data: data['guidance'].update(endingOkay='yes'),
    lambda data: data['guidance'].update(draft='这里不能复制全文'),
    lambda data: data['guidance'].pop('notes'),
])
def test_malformed_guidance_never_replaces_any_saved_work(guided_browser, mutation):
    attempt = start_guided(guided_browser)
    data = deepcopy(attempt['data']); mutation(data)
    response = guided_browser.client.patch(PREFIX + '/attempts/' + attempt['id'], headers=guided_browser.headers,
                                           json={'base_revision': 1, 'data': data})
    assert response.status_code == 400
    assert WritingStore(database(guided_browser)).get(attempt['id'])['attempt'] == attempt


def test_valid_guidance_cannot_be_injected_to_convert_an_existing_classic_draft(browser):
    attempt = start(browser)
    data = deepcopy(attempt['data']); data['guidance'] = initial_guidance()
    with pytest.raises(WritingError) as caught:
        WritingStore(database(browser)).save(attempt['id'], 1, data)
    assert caught.value.status == 400
    assert WritingStore(database(browser)).get(attempt['id'])['attempt'] == attempt


def test_long_student_text_survives_saving_snapshots_and_restore_without_soft_limit_truncation(guided_browser):
    attempt = start_guided(guided_browser)
    data = deepcopy(attempt['data'])
    data['guidance'].update(customTheme='自己的主旨' * 60, ownDetail='自己的细节' * 60,
                            sentence='自己的句子' * 100, paragraph='自己的重点段' * 200)
    data['guidance']['notes'] = {part: '自己的提纲材料' * 30 for part in ('begin', 'middle', 'end')}
    data['paragraphs']['p_full'] = '保留我的完整初稿\n' * 240
    store = WritingStore(database(guided_browser))
    saved = store.save(attempt['id'], 1, data, label='长稿')
    version = saved['versions'][0]
    assert saved['attempt']['data'] == data
    assert store.get_version(attempt['id'], version['id'])['data'] == data
    later = deepcopy(data); later['guidance'].update(sentence='后来写的句子', review=[], confirmed=[])
    later['paragraphs']['p_full'] += '后来补写的正文。'
    store.save(attempt['id'], 2, later)
    restored = store.restore(attempt['id'], 3, version['id'])
    assert restored['attempt']['data'] == data
    before = next(row for row in restored['versions'] if row['label'] == '恢复前的草稿')
    assert store.get_version(attempt['id'], before['id'])['data'] == later


def test_guided_completion_accepts_personal_material_and_never_grades_against_the_example(guided_browser):
    attempt = start_guided(guided_browser)
    data = guided_complete_data()
    data['guidance'].update(theme='', customTheme='同伴的鼓励让我更有勇气。', structure='final', details=[],
                            sentence='我把脚迈进绳圈，同伴轻声为我数数。', paragraph='轮到我跳绳，我有点害怕，同伴鼓励我，我终于跳了过去。')
    data['guidance']['slots'] = {part: [] for part in ('begin', 'middle', 'end')}
    data['guidance']['notes'] = {'begin': '课间我来到操场', 'middle': '第一次跳进绳圈', 'end': '感谢同伴的鼓励'}
    data['paragraphs']['p_full'] = '课间我们跳绳。轮到我时，同伴为我加油。我终于跳过绳子，心里暖暖的。'
    data['structure_paragraphs'] = deepcopy(data['paragraphs']); data['final_paragraphs'] = deepcopy(data['paragraphs'])
    response = guided_browser.client.post(PREFIX + '/attempts/' + attempt['id'] + '/complete', headers=guided_browser.headers,
                                          json={'base_revision': 1, 'data': data})
    assert response.status_code == 200
    assert response.json['attempt']['status'] == 'completed' and response.json['attempt']['data'] == data
    assert 'score' not in response.json['attempt']


@pytest.mark.parametrize('mutation,missing', [
    (lambda data: data['guidance'].update(theme='', customTheme='  '), 'guidance.theme'),
    (lambda data: data['guidance'].update(structure=''), 'guidance.structure'),
    (lambda data: data['guidance']['slots'].update(begin=[]), 'guidance.slots.begin'),
    (lambda data: data['guidance']['slots'].update(middle=[]), 'guidance.slots.middle'),
    (lambda data: data['guidance']['slots'].update(end=[]), 'guidance.slots.end'),
    (lambda data: data['guidance'].update(sentence='  '), 'guidance.sentence'),
    (lambda data: data['guidance'].update(paragraph=''), 'guidance.paragraph'),
    (lambda data: data['paragraphs'].update(p_full=''), 'paragraphs.p_full'),
    (lambda data: data['final_paragraphs'].update(p_full=''), 'final_paragraphs.p_full'),
    (lambda data: data['guidance'].update(confirmed=list(range(8))), 'guidance.confirmed'),
    (lambda data: data['guidance'].update(review=[5]), 'guidance.review'),
    (lambda data: data['guidance'].update(endingOkay=False), 'guidance.endingOkay'),
])
def test_guided_completion_requires_real_work_and_all_nine_confirmations(guided_browser, mutation, missing):
    attempt = start_guided(guided_browser)
    data = guided_complete_data(); mutation(data)
    response = guided_browser.client.post(PREFIX + '/attempts/' + attempt['id'] + '/complete', headers=guided_browser.headers,
                                          json={'base_revision': 1, 'data': data})
    assert response.status_code == 400 and response.json['code'] == 'incomplete'
    assert missing in response.json['missing']
    assert WritingStore(database(guided_browser)).get(attempt['id'])['attempt'] == attempt


def test_navigating_to_the_last_question_does_not_confirm_previous_work(guided_browser):
    attempt = start_guided(guided_browser)
    data = deepcopy(attempt['data']); data['guidance']['step'] = 8
    saved = WritingStore(database(guided_browser)).save(attempt['id'], 1, data)
    assert saved['attempt']['data']['guidance']['confirmed'] == []
    assert saved['attempt']['data']['guidance']['review'] == []


def test_parent_reads_guided_state_and_versions_but_cannot_change_it(guided_browser):
    attempt = start_guided(guided_browser)
    endpoint = PREFIX + '/attempts/' + attempt['id']
    parent = {'Authorization': 'Bearer parent-token', 'X-Writing-Flow': 'guided-v1'}
    response = guided_browser.client.get(endpoint, headers=parent)
    assert response.json['read_only'] is True and response.json['attempt']['data']['guidance'] == initial_guidance()
    version = response.json['versions'][0]
    assert guided_browser.client.get(endpoint + '/versions/' + version['id'], headers=parent).json['version']['data'] == attempt['data']
    assert guided_browser.client.patch(endpoint, headers=parent, json={'base_revision': 1, 'data': guided_complete_data()}).status_code == 403
    assert guided_browser.client.post(PREFIX + '/attempts', headers=parent, json={'lesson_id': LESSON_ID, 'flow_version': 1}).status_code == 403
    assert WritingStore(database(guided_browser)).get(attempt['id'])['attempt'] == attempt


def test_guided_concurrent_saves_preserve_one_whole_winning_state(tmp_path):
    store = WritingStore(tmp_path / 'writing.sqlite3'); attempt = store.start(guided_lesson())
    def update(text):
        data = deepcopy(attempt['data'])
        data['guidance'].update(customTheme=text, sentence=text)
        data['paragraphs']['p_full'] = text
        try:
            return store.save(attempt['id'], 1, data)['attempt']['data']
        except WritingError as error:
            assert error.status == 409 and error.details['code'] == 'conflict'
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ['甲页面自己的文章', '乙页面自己的文章']))
    assert sum(item is not None for item in results) == 1
    assert store.get(attempt['id'])['attempt']['data'] == next(item for item in results if item is not None)


def test_failed_guided_checkpoint_and_restore_roll_back_state_and_version_together(guided_browser, monkeypatch):
    import sqlite3
    store = WritingStore(database(guided_browser)); attempt = start_guided(guided_browser)
    checkpoint = store.save(attempt['id'], 1, guided_complete_data(), label='待恢复的重点段')
    target_version = checkpoint['versions'][0]['id']
    data = deepcopy(checkpoint['attempt']['data']); data['guidance']['paragraph'] += '后来补写。'
    store.save(attempt['id'], 2, data)
    before = store.get(attempt['id'])
    original = store._snapshot
    def failed_checkpoint(*args, **kwargs):
        raise PermissionError('simulated disk failure')
    monkeypatch.setattr(store, '_snapshot', failed_checkpoint)
    with pytest.raises(WritingError) as failure:
        store.save(attempt['id'], 3, data, label='不能留下半个版本')
    assert failure.value.status == 503 and store.get(attempt['id']) == before
    calls = 0
    def failed_restore(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise sqlite3.OperationalError('simulated second snapshot failure')
        return original(*args, **kwargs)
    monkeypatch.setattr(store, '_snapshot', failed_restore)
    with pytest.raises(WritingError) as failure:
        store.restore(attempt['id'], 3, target_version)
    assert failure.value.status == 503 and store.get(attempt['id']) == before


def test_corrupted_guided_state_is_reported_and_cannot_be_overwritten(guided_browser):
    import sqlite3
    attempt = start_guided(guided_browser)
    damaged = deepcopy(attempt['data']); damaged['guidance']['slots']['middle'] = ['missing-choice']
    with sqlite3.connect(database(guided_browser)) as connection:
        connection.execute('UPDATE writing_attempts SET data_json=?', (json.dumps(damaged, ensure_ascii=False),))
    endpoint = PREFIX + '/attempts/' + attempt['id']
    assert guided_browser.client.get(endpoint, headers=guided_browser.headers).status_code == 503
    assert guided_browser.client.patch(endpoint, headers=guided_browser.headers,
                                       json={'base_revision': 1, 'data': guided_complete_data()}).status_code == 503
    with sqlite3.connect(database(guided_browser)) as connection:
        assert json.loads(connection.execute('SELECT data_json FROM writing_attempts').fetchone()[0]) == damaged


def test_corrupted_guided_version_cannot_replace_the_current_draft(guided_browser):
    import sqlite3
    store = WritingStore(database(guided_browser)); attempt = start_guided(guided_browser)
    before = store.get(attempt['id']); version = before['versions'][0]
    damaged = deepcopy(attempt['data']); damaged['guidance']['confirmed'] = [0, 0]
    with sqlite3.connect(database(guided_browser)) as connection:
        connection.execute('UPDATE writing_versions SET data_json=? WHERE id=?',
                           (json.dumps(damaged, ensure_ascii=False), version['id']))
    with pytest.raises(WritingError) as error:
        store.get_version(attempt['id'], version['id'])
    assert error.value.status == 503
    with pytest.raises(WritingError) as error:
        store.restore(attempt['id'], 1, version['id'])
    assert error.value.status == 503 and store.get(attempt['id']) == before


def test_guided_attempt_uses_its_frozen_choice_ids_and_examples_after_content_updates(guided_browser):
    attempt = start_guided(guided_browser)
    changed = guided_lesson(); changed['guidance']['themes'][0].update(id='another-theme', text='另一种方向')
    changed['guidance']['examples'][0]['paragraphs'][0]['text'] = '更新后的示例文字。'
    guided_browser.lesson_path.write_text(json.dumps(changed, ensure_ascii=False))
    data = deepcopy(attempt['data']); data['guidance']['theme'] = 'fun'
    saved = WritingStore(database(guided_browser)).save(attempt['id'], 1, data)
    assert saved['attempt']['lesson_snapshot'] == attempt['lesson_snapshot']
    assert saved['attempt']['data']['guidance']['theme'] == 'fun'
    new = start_guided(guided_browser)
    new_data = deepcopy(new['data']); new_data['guidance']['theme'] = 'another-theme'
    assert WritingStore(database(guided_browser)).save(new['id'], 1, new_data)['attempt']['data'] == new_data


@pytest.mark.parametrize('mutation', [
    lambda config: config.update(version=True),
    lambda config: config['steps'].pop(),
    lambda config: config['steps'][0].update(id=True),
    lambda config: config['themes'].append(deepcopy(config['themes'][0])),
    lambda config: config['structures'][0]['roles'].update(begin=['not teaching text']),
    lambda config: config['options']['begin'][0].update(onclick='not accepted'),
    lambda config: config['examples'][0]['paragraphs'][0].update(text={}),
    lambda config: config['examples'][0]['paragraphs'][0].update(html='not accepted'),
    lambda config: config['limits'].update(sentence=True),
    lambda config: config['limits'].update(draft=6001),
])
def test_invalid_guided_configuration_never_creates_student_storage(guided_browser, mutation):
    content = guided_lesson(); mutation(content['guidance'])
    guided_browser.lesson_path.write_text(json.dumps(content, ensure_ascii=False))
    assert guided_browser.client.get(PREFIX + '/lessons', headers=guided_browser.headers).status_code == 503
    assert guided_browser.client.post(PREFIX + '/attempts', headers=guided_browser.headers,
                                      json={'lesson_id': LESSON_ID, 'flow_version': 1}).status_code == 503
    assert not guided_browser.data_dir.exists()

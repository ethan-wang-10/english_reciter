"""Subject portals share account sessions while keeping learning content separate."""

from datetime import timedelta
from functools import wraps
import importlib.util
import json
import logging
from pathlib import Path
import re
from types import SimpleNamespace

from flask import Flask, g, jsonify, request
import pytest
from werkzeug.security import check_password_hash, generate_password_hash

import subject_portal as portal
import web_auth_routes


ROOT = Path(__file__).resolve().parent
HOSTS = ('english.itorange.online', 'chinese.itorange.online', 'math.itorange.online')
PRIVATE = {'password_hash', 'password', 'answer', 'answers', 'accepted_answers', 'questions', 'secret', 'token'}


def write_catalog(root, subject, *, papers=None, knowledge=None):
    folder = root / subject
    folder.mkdir(parents=True, exist_ok=True)
    payload = {'schema_version': 1, 'subject': subject, 'papers': papers or [], 'knowledge': knowledge or []}
    (folder / 'catalog.json').write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    return payload


def sample_paper(identifier='chinese-midterm'):
    return {
        'id': identifier, 'title': '四年级上册期中复习', 'grade': 4, 'term': 1,
        'exam_kind': 'midterm', 'description': '内置复习考卷。', 'question_count': 20,
        'time_minutes': 30, 'knowledge_id': 'chinese-points',
    }


def sample_knowledge(identifier='chinese-points'):
    return {
        'id': identifier, 'title': '阅读与表达', 'grade': 4, 'term': 1,
        'description': '学习重点。',
        'sections': [{'title': '阅读方法', 'items': ['先读问题，再回文中找依据。']}],
    }


def sample_record(identifier='record-alice', paper_id='chinese-midterm'):
    return {
        'id': identifier, 'paper_id': paper_id, 'paper_title': '语文练习',
        'score': 90, 'total': 20, 'correct_count': 18,
        'submitted_at': '2026-11-13T09:00:00', 'status': 'submitted',
    }


def write_records(data_dir, username, subject, records):
    folder = data_dir / username / 'subjects' / subject
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'records.json').write_text(json.dumps(records, ensure_ascii=False), encoding='utf-8')


@pytest.fixture
def subject_app(tmp_path):
    """Use real SQLite sessions without touching the application-wide session store."""
    spec = importlib.util.spec_from_file_location('isolated_subject_sessions', ROOT / 'auth_session_store.py')
    sessions = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sessions)
    data_dir = tmp_path / 'data'
    sessions.init_auth_session_store(data_dir)
    password = generate_password_hash('same-password')
    users = {
        'alice': {'password_hash': password, 'email': 'alice@example.test', 'nickname': '小海龟', 'enabled': True, 'plan': 'paid'},
        'bob': {'password_hash': password, 'email': 'bob@example.test', 'enabled': True, 'plan': 'free'},
        'alice_parent': {'password_hash': password, 'email': 'parent@example.test', 'enabled': True,
                         'role': 'parent', 'child_username': 'alice', 'plan': 'free'},
    }

    def get_user(username):
        row = users.get(username)
        return dict(row) if row else None

    def token_required(function):
        @wraps(function)
        def authenticated(*args, **kwargs):
            authorization = request.headers.get('Authorization', '')
            token = authorization[7:].strip() if authorization.startswith('Bearer ') else ''
            login = sessions.verify_session(token, sessions.SESSION_KIND_USER) if token else None
            if not login:
                return jsonify({'error': '需要认证'}), 401
            row = users.get(login)
            if not row or row.get('enabled') is False:
                return jsonify({'error': '账号已停用'}), 403
            child = row.get('child_username') if row.get('role') == 'parent' else None
            if child and (child not in users or users[child].get('enabled') is False):
                return jsonify({'error': '学生账号已停用'}), 403
            if row.get('role') == 'parent' and not child:
                return jsonify({'error': '家长账户配置错误'}), 403
            g.login_username = login
            g.user_row = row
            g.is_parent = row.get('role') == 'parent'
            g.effective_username = child or login
            g.effective_user_row = users[child or login]
            return function(child or login, *args, **kwargs)
        return authenticated

    def auth_payload(login):
        row = users[login]
        return {'login_username': login, 'is_parent': row.get('role') == 'parent',
                'child_username': row.get('child_username'), 'system_broadcast': None}

    def parent_forbidden(function):
        @wraps(function)
        def student_only(username, *args, **kwargs):
            if getattr(g, 'is_parent', False):
                return jsonify({'error': '家长账户仅可查看'}), 403
            return function(username, *args, **kwargs)
        return student_only

    def register(username, raw_password, email, invite):
        if invite != 'test-invite' or username in users:
            return False, '注册失败'
        users[username] = {'password_hash': generate_password_hash(raw_password), 'email': email,
                           'enabled': True, 'plan': 'free'}
        return True, None

    services = SimpleNamespace(
        DATA_DIR=data_dir, token_required=token_required, parent_forbidden=parent_forbidden, get_user=get_user,
        get_user_plan=lambda username: users.get(username, {}).get('plan', 'free'),
        is_parent_user_record=lambda row: row.get('role') == 'parent',
        request=request, jsonify=jsonify, g=g, logger=logging.getLogger('subject-test'),
        _rate_allow=lambda *_: True, _client_ip=lambda: '127.0.0.1',
        _RATE_MAX_LOGIN=20, _RATE_MAX_REGISTER=10,
        resolve_username=lambda username: username if username in users else None,
        verify_user=lambda username, raw: username in users and check_password_hash(users[username]['password_hash'], raw),
        is_valid_username=lambda value: isinstance(value, str) and value.replace('_', '').isalnum() and 3 <= len(value) <= 32,
        create_token=lambda login: sessions.create_session(sessions.SESSION_KIND_USER, login, timedelta(hours=1)),
        _auth_session_payload=auth_payload,
        _revoke_user_tokens=lambda login: sessions.revoke_principal(sessions.SESSION_KIND_USER, login),
        normalize_email=lambda value: (value or '').strip().lower(),
        register_user_with_invite=register,
        china_now_iso=lambda **_: '2026-11-13T09:00:00',
    )
    app = Flask(__name__, static_folder=str(ROOT / 'static'), template_folder=str(ROOT / 'templates'))
    app.config.update(TESTING=True, SUBJECT_CONTENT_DIR=tmp_path / 'content')
    for subject in ('chinese', 'math'):
        write_catalog(app.config['SUBJECT_CONTENT_DIR'], subject)
    web_auth_routes.register_routes(app, services)
    portal.register_subject_portal_routes(app, services)
    with app.test_client() as client:
        yield SimpleNamespace(app=app, client=client, users=users, services=services, sessions=sessions,
                              data_dir=data_dir, content_dir=app.config['SUBJECT_CONTENT_DIR'])
    sessions.close_connection()


def login(state, username='alice', host=HOSTS[0]):
    response = state.client.post('/api/auth/login', base_url='https://' + host,
                                 json={'username': username, 'password': 'same-password'})
    assert response.status_code == 200, response.get_json()
    return {'Authorization': 'Bearer ' + response.get_json()['access_token']}


def test_subject_hosts_are_exact_and_preview_pages_are_public(subject_app):
    state = subject_app
    assert portal.subject_for_host(state.app, 'chinese.itorange.online') == 'chinese'
    assert portal.subject_for_host(state.app, 'math.itorange.online:443') == 'math'
    assert portal.subject_for_host(state.app, 'english.itorange.online') is None
    assert portal.subject_for_host(state.app, 'chinese.itorange.online.attacker.test') is None
    for subject, name in [('chinese', '语文'), ('math', '数学')]:
        response = state.client.get('/subjects/' + subject)
        assert response.status_code == 200
        assert name in response.get_data(as_text=True)
        assert state.client.get('/subjects/' + subject + '/').status_code == 200
    assert state.client.get('/subjects/history').status_code == 404


@pytest.mark.parametrize('subject', ['chinese', 'math'])
@pytest.mark.parametrize('endpoint', ['bootstrap', 'papers', 'knowledge', 'records'])
def test_subject_data_requires_shared_user_auth(subject_app, subject, endpoint):
    state = subject_app
    url = f'/api/subjects/{subject}/{endpoint}'
    assert state.client.get(url).status_code == 401
    assert state.client.get(url, headers={'Authorization': 'Bearer fake'}).status_code == 401


def test_same_username_password_and_session_work_across_all_three_hosts(subject_app):
    state = subject_app
    for host in HOSTS:
        headers = login(state, host=host)
        for subject in ('chinese', 'math'):
            response = state.client.get(f'/api/subjects/{subject}/bootstrap', base_url='https://' + host, headers=headers)
            assert response.status_code == 200
            account = response.get_json()['account']
            assert account['username'] == 'alice'
            assert account['email'] == 'alice@example.test'
            assert not PRIVATE.intersection(account)


def test_registering_once_makes_the_account_available_to_both_subjects(subject_app):
    state = subject_app
    response = state.client.post('/api/auth/register', base_url='https://' + HOSTS[1], json={
        'username': 'new_student', 'password': 'same-password', 'email': 'new@example.test', 'invite_code': 'test-invite',
    })
    assert response.status_code == 201
    for subject, host in [('chinese', HOSTS[1]), ('math', HOSTS[2])]:
        headers = login(state, 'new_student', host)
        response = state.client.get(f'/api/subjects/{subject}/bootstrap', base_url='https://' + host, headers=headers)
        assert response.status_code == 200
        assert response.get_json()['account']['username'] == 'new_student'


@pytest.mark.parametrize('subject', ['chinese', 'math'])
def test_empty_subjects_are_real_empty_catalogs_and_do_not_create_english_data(subject_app, subject):
    state = subject_app
    headers = login(state)
    before = {str(path.relative_to(state.data_dir)) for path in state.data_dir.rglob('*')}
    response = state.client.get(f'/api/subjects/{subject}/bootstrap', headers=headers)
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['subject']['id'] == subject
    assert payload['summary'] == {'paper_count': 0, 'knowledge_count': 0, 'record_count': 0}
    assert payload['papers'] == payload['knowledge'] == payload['records'] == []
    for endpoint in ('papers', 'knowledge', 'records'):
        assert state.client.get(f'/api/subjects/{subject}/{endpoint}', headers=headers).get_json() == {'items': [], 'count': 0}
    after = {str(path.relative_to(state.data_dir)) for path in state.data_dir.rglob('*')}
    assert after == before
    assert not (state.data_dir / 'alice').exists()


def test_subject_catalogs_are_isolated_and_paper_metadata_does_not_expose_keys(subject_app):
    state = subject_app
    paper = {**sample_paper(), 'questions': [{'answer': 'private-answer'}], 'answers': {'q1': 'private-answer'}}
    write_catalog(state.content_dir, 'chinese', papers=[paper], knowledge=[sample_knowledge()])
    headers = login(state)
    response = state.client.get('/api/subjects/chinese/papers', headers=headers)
    assert response.status_code in (200, 503)
    if response.status_code == 200:
        public = response.get_json()['items'][0]
        assert public['id'] == paper['id']
        assert not PRIVATE.intersection(public)
        assert 'private-answer' not in response.get_data(as_text=True)
    else:
        assert 'private-answer' not in response.get_data(as_text=True)
    math = state.client.get('/api/subjects/math/bootstrap', headers=headers).get_json()
    assert math['papers'] == math['knowledge'] == []
    assert 'chinese-points' not in json.dumps(math)


def test_valid_catalog_content_populates_the_subject_bootstrap(subject_app):
    state = subject_app
    write_catalog(state.content_dir, 'chinese', papers=[sample_paper()], knowledge=[sample_knowledge()])
    payload = state.client.get('/api/subjects/chinese/bootstrap', headers=login(state)).get_json()
    assert payload['summary']['paper_count'] == payload['summary']['knowledge_count'] == 1
    assert payload['papers'][0]['id'] == 'chinese-midterm'
    assert payload['knowledge'][0]['sections'][0]['items'] == ['先读问题，再回文中找依据。']


def test_records_use_effective_student_and_never_other_students_or_subjects(subject_app):
    state = subject_app
    own = {**sample_record(), 'answers': {'q1': 'private-answer'}, 'private_note': 'do-not-expose'}
    write_records(state.data_dir, 'alice', 'chinese', [own])
    write_records(state.data_dir, 'alice', 'math', [sample_record('math-record', 'math-practice')])
    write_records(state.data_dir, 'bob', 'chinese', [sample_record('bob-record')])
    for principal in ('alice', 'alice_parent'):
        headers = login(state, principal)
        response = state.client.get('/api/subjects/chinese/records?username=bob', headers=headers)
        assert response.status_code == 200
        assert response.get_json() == {'count': 1, 'items': [sample_record()]}
        assert 'private-answer' not in response.get_data(as_text=True)
        bootstrap = state.client.get('/api/subjects/chinese/bootstrap', headers=headers).get_json()
        assert bootstrap['summary']['record_count'] == 1
        assert bootstrap['account']['username'] == principal
        assert bootstrap['account']['is_parent'] is (principal == 'alice_parent')
        assert bootstrap['account']['child_username'] == ('alice' if principal == 'alice_parent' else None)
        assert bootstrap['records'] == [sample_record()]


def test_disabled_accounts_children_and_revoked_sessions_stay_disabled(subject_app):
    state = subject_app
    headers = login(state)
    parent_headers = login(state, 'alice_parent')
    state.users['alice']['enabled'] = False
    for header in (headers, parent_headers):
        assert state.client.get('/api/subjects/chinese/bootstrap', headers=header).status_code == 403
    state.users['alice']['enabled'] = True
    state.sessions.revoke_principal(state.sessions.SESSION_KIND_USER, 'alice')
    assert state.client.get('/api/subjects/math/bootstrap', headers=headers).status_code == 401


@pytest.mark.parametrize('mutation', [
    lambda data: {**data, 'schema_version': 2},
    lambda data: {**data, 'subject': 'math'},
    lambda data: {**data, 'papers': 'invalid'},
    lambda data: {**data, 'papers': [{**sample_paper(), 'grade': 99}]},
    lambda data: {**data, 'papers': [{**sample_paper(), 'time_minutes': -1}]},
    lambda data: {**data, 'knowledge': [{**sample_knowledge(), 'sections': 'invalid'}]},
])
def test_invalid_catalog_fails_closed_without_exposing_paths(subject_app, mutation):
    state = subject_app
    content = mutation(write_catalog(state.content_dir, 'chinese'))
    (state.content_dir / 'chinese/catalog.json').write_text(json.dumps(content), encoding='utf-8')
    response = state.client.get('/api/subjects/chinese/bootstrap', headers=login(state))
    assert response.status_code == 503
    assert response.get_json()['error']
    assert str(state.content_dir) not in response.get_data(as_text=True)


def test_unknown_subject_and_malformed_catalog_do_not_fall_back_to_english(subject_app):
    state = subject_app
    headers = login(state)
    assert state.client.get('/api/subjects/english/bootstrap', headers=headers).status_code == 404
    assert state.client.get('/api/subjects/history/papers', headers=headers).status_code == 404
    (state.content_dir / 'math/catalog.json').write_text('{broken', encoding='utf-8')
    response = state.client.get('/api/subjects/math/bootstrap', headers=headers)
    assert response.status_code == 503
    assert response.get_json()['error']


@pytest.fixture
def integrated_web(subject_app, monkeypatch):
    """Exercise the actual entry point and token decorator using disposable state."""
    state = subject_app
    monkeypatch.setenv('ENGLISH_RECITER_DATA_DIR', str(state.data_dir / 'entry-point'))
    monkeypatch.setenv('GAOKAO_AUTO_BACKFILL_ENABLED', 'false')
    monkeypatch.setenv('PERF_MONITOR_ENABLED', 'false')
    import simple_web_app as web
    monkeypatch.setitem(web.app.config, 'TESTING', True)
    monkeypatch.setitem(web.app.config, 'SUBJECT_CONTENT_DIR', state.content_dir)
    monkeypatch.setattr(web, 'DATA_DIR', state.data_dir)
    monkeypatch.setattr(web, 'get_user', state.services.get_user)
    monkeypatch.setattr(web, 'resolve_username', state.services.resolve_username, raising=False)
    monkeypatch.setattr(web, 'create_token', state.services.create_token)
    monkeypatch.setattr(web, 'verify_token', lambda token: state.sessions.verify_session(token, state.sessions.SESSION_KIND_USER))
    monkeypatch.setattr(web, '_auth_session_payload', state.services._auth_session_payload)
    monkeypatch.setattr(web, '_revoke_user_tokens', state.services._revoke_user_tokens)
    monkeypatch.setattr(web, '_rate_allow', lambda *_: True)
    monkeypatch.setattr(web, 'user_reciter_session', lambda *_args, **_kwargs: pytest.fail('A subject portal must not initialize English learning'))
    with web.app.test_client() as client:
        yield web, client, state


def test_application_root_selects_chinese_math_and_preserves_english(integrated_web):
    _web, client, _state = integrated_web
    for host, title in zip(HOSTS, ('智能英语背诵系统', '语文学习空间', '数学学习空间'), strict=True):
        response = client.get('/', base_url='https://' + host)
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        if host == HOSTS[0]:
            assert f'<title>{title}</title>' in html
        else:
            config = re.search(r'<script[^>]+id="subject-site-config"[^>]*>(.*?)</script>', html, re.S)
            assert config
            site = json.loads(config.group(1))
            assert site['title'] == title
            assert site['id'] == ('chinese' if host == HOSTS[1] else 'math')
            assert 'no-store' in response.headers.get('Cache-Control', '')


def test_actual_shared_auth_routes_and_parent_decorator_work_on_subject_hosts(integrated_web):
    _web, client, state = integrated_web
    record = sample_record()
    write_records(state.data_dir, 'alice', 'chinese', [record])
    for host in HOSTS:
        response = client.post('/api/auth/login', base_url='https://' + host,
                               json={'username': 'alice_parent', 'password': 'same-password'})
        assert response.status_code == 200
        headers = {'Authorization': 'Bearer ' + response.get_json()['access_token']}
        response = client.get('/api/subjects/chinese/bootstrap', base_url='https://' + host, headers=headers)
        assert response.status_code == 200
        payload = response.get_json()
        assert payload['account']['username'] == 'alice_parent'
        assert payload['account']['child_username'] == 'alice'
        assert payload['records'] == [record]
        assert 'no-store' in response.headers.get('Cache-Control', '')
    state.users['alice']['enabled'] = False
    assert client.get('/api/subjects/chinese/bootstrap', base_url='https://' + HOSTS[1], headers=headers).status_code == 403

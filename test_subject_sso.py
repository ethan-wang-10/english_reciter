"""Real one-time session handoff across the three configured subject origins."""

import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from functools import wraps
import hashlib
import json
from pathlib import Path
import sqlite3

from flask import Flask, g, jsonify, request
import pytest

import auth_session_store as sessions
from subject_sso import CODE_KIND, register_subject_sso_routes


SITES = {
    'english': {'url': 'https://english.example.test/', 'name': '英语'},
    'chinese': {'url': 'https://chinese.example.test/', 'name': '语文'},
    'math': {'url': 'https://math.example.test/', 'name': '数学'},
}
VERIFIER = 'a' * 43
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip('=')


class Services:
    def __init__(self):
        self.users = {
            'alice': {'enabled': True}, 'bob': {'enabled': True},
            'alice_parent': {'enabled': True, 'role': 'parent', 'child_username': 'alice'},
        }

    def get_user(self, username):
        return self.users.get(username)

    @staticmethod
    def is_valid_username(value):
        return isinstance(value, str) and value.isidentifier()

    @staticmethod
    def _user_row_is_enabled(row):
        return isinstance(row, dict) and row.get('enabled', True) is not False

    @staticmethod
    def is_parent_user_record(row):
        return isinstance(row, dict) and row.get('role') == 'parent'

    @staticmethod
    def create_token(username):
        return sessions.create_session(sessions.SESSION_KIND_USER, username, timedelta(days=30))

    @staticmethod
    def verify_token(token):
        return sessions.verify_session(token, sessions.SESSION_KIND_USER)

    def _auth_session_payload(self, username):
        row = self.get_user(username)
        return {'login_username': username, 'is_parent': self.is_parent_user_record(row),
                'child_username': row.get('child_username')}

    def token_required(self, function):
        @wraps(function)
        def authenticated(*args, **kwargs):
            auth = request.headers.get('Authorization', '')
            username = self.verify_token(auth[7:]) if auth.startswith('Bearer ') else None
            if not username:
                return jsonify({'error': '需要认证'}), 401
            if not self._user_row_is_enabled(self.get_user(username)):
                return jsonify({'error': '账号已停用'}), 403
            row = self.get_user(username)
            g.login_username = username
            g.is_parent = self.is_parent_user_record(row)
            effective = row['child_username'] if g.is_parent else username
            return function(effective, *args, **kwargs)
        return authenticated


@pytest.fixture
def environment(tmp_path):
    sessions.init_auth_session_store(tmp_path)
    app = Flask(__name__)
    app.config.update(TESTING=True)
    services = Services()
    register_subject_sso_routes(app, services, lambda: SITES)
    yield app, services, tmp_path
    sessions.close_connection()


def ticket(environment, username='alice', target='chinese', origin=True):
    app, services, _path = environment
    token = services.create_token(username)
    headers = {'Authorization': 'Bearer ' + token}
    if origin:
        headers['Origin'] = SITES['english']['url'].rstrip('/')
    response = app.test_client().post('/api/auth/sso/ticket', base_url=SITES['english']['url'],
                                     headers=headers, json={'target': target, 'code_challenge': CHALLENGE})
    assert response.status_code == 201, response.get_json()
    return response.get_json()['code'], token


def exchange(environment, code, target='chinese', verifier=VERIFIER, origin=None):
    app, _services, _path = environment
    headers = {'Origin': origin or SITES[target]['url'].rstrip('/')}
    return app.test_client().post('/api/auth/sso/exchange', base_url=SITES[target]['url'],
                                  headers=headers, json={'code': code, 'target': target, 'code_verifier': verifier})


def test_public_sites_contain_only_configured_roots_and_no_account_secrets(environment):
    app, _services, _path = environment
    response = app.test_client().get('/api/auth/sso/sites', base_url=SITES['math']['url'])
    assert response.status_code == 200
    data = response.get_json()
    assert data['current_site'] == 'math'
    assert set(data['sites']) == {'english', 'chinese', 'math'}
    assert set(data['sites']['english']) == {'id', 'name', 'url'}
    assert response.headers['Cache-Control'] == 'no-store'


@pytest.mark.parametrize('target', ['chinese', 'math'])
def test_same_existing_account_gets_a_new_session_without_exposing_source_token(environment, target):
    code, source_token = ticket(environment, target=target)
    response = exchange(environment, code, target=target)
    assert response.status_code == 200
    data = response.get_json()
    assert data['username'] == 'alice'
    assert data['access_token'] != source_token
    assert environment[1].verify_token(data['access_token']) == 'alice'
    assert environment[1].verify_token(source_token) == 'alice'
    assert 'source_token' not in data and code not in response.get_data(as_text=True)
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['Referrer-Policy'] == 'no-referrer'


def test_authorization_code_is_hashed_at_rest_and_cannot_be_used_as_a_bearer_session(environment):
    code, _source_token = ticket(environment)
    with sqlite3.connect(environment[2] / 'sessions.sqlite3') as connection:
        stored = connection.execute('SELECT token FROM auth_sessions WHERE session_kind=?', (CODE_KIND,)).fetchone()[0]
    assert stored == hashlib.sha256(code.encode()).hexdigest()
    assert environment[1].verify_token(code) is None


def test_code_can_be_consumed_only_once(environment):
    code, _token = ticket(environment)
    assert exchange(environment, code).status_code == 200
    assert exchange(environment, code).status_code == 400


def test_concurrent_exchange_issues_exactly_one_new_session(environment):
    code, _token = ticket(environment)
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: exchange(environment, code).status_code, range(2)))
    assert sorted(statuses) == [200, 400]


def test_wrong_target_cannot_burn_or_redeem_another_sites_code(environment):
    code, _token = ticket(environment)
    assert exchange(environment, code, target='math').status_code == 400
    assert exchange(environment, code).status_code == 200


def test_wrong_pkce_verifier_does_not_burn_the_valid_code(environment):
    code, _token = ticket(environment)
    assert exchange(environment, code, verifier='b' * 43).status_code == 400
    assert exchange(environment, code).status_code == 200


def test_wrong_origin_cannot_issue_or_redeem_a_ticket(environment):
    app, services, _path = environment
    token = services.create_token('alice')
    response = app.test_client().post('/api/auth/sso/ticket', base_url=SITES['english']['url'],
        headers={'Authorization': 'Bearer ' + token, 'Origin': 'https://attacker.example'},
        json={'target': 'chinese', 'code_challenge': CHALLENGE})
    assert response.status_code == 403
    code, _token = ticket(environment)
    assert exchange(environment, code, origin='https://attacker.example').status_code == 403
    assert exchange(environment, code).status_code == 200


@pytest.mark.parametrize('target', ['https://attacker.example/', '//attacker.example', '../math', '', [], 'english'])
def test_ticket_targets_are_exact_allowlisted_site_identifiers(environment, target):
    app, services, _path = environment
    token = services.create_token('alice')
    response = app.test_client().post('/api/auth/sso/ticket', base_url=SITES['english']['url'],
        headers={'Authorization': 'Bearer ' + token}, json={'target': target, 'code_challenge': CHALLENGE})
    assert response.status_code == 400


@pytest.mark.parametrize('challenge', [None, '', 'plain-verifier', 'a' * 42, 'a' * 44, '=' * 43, [], {}])
def test_ticket_requires_canonical_s256_challenge(environment, challenge):
    app, services, _path = environment
    token = services.create_token('alice')
    response = app.test_client().post('/api/auth/sso/ticket', base_url=SITES['english']['url'],
        headers={'Authorization': 'Bearer ' + token}, json={'target': 'math', 'code_challenge': challenge})
    assert response.status_code == 400


def test_ticket_requires_an_existing_valid_account_session(environment):
    app, services, _path = environment
    response = app.test_client().post('/api/auth/sso/ticket', base_url=SITES['english']['url'],
        json={'target': 'math', 'code_challenge': CHALLENGE})
    assert response.status_code == 401
    token = services.create_token('alice')
    services.users['alice']['enabled'] = False
    response = app.test_client().post('/api/auth/sso/ticket', base_url=SITES['english']['url'],
        headers={'Authorization': 'Bearer ' + token}, json={'target': 'math', 'code_challenge': CHALLENGE})
    assert response.status_code == 403


def test_expired_code_is_rejected(environment, monkeypatch):
    now = sessions.time.time()
    monkeypatch.setattr(sessions.time, 'time', lambda: now)
    code, _token = ticket(environment)
    monkeypatch.setattr(sessions.time, 'time', lambda: now + 61)
    assert exchange(environment, code).status_code == 400


def test_logout_or_password_reset_revocation_invalidates_unredeemed_codes(environment):
    code, _token = ticket(environment)
    sessions.revoke_principal(sessions.SESSION_KIND_USER, 'alice')
    assert exchange(environment, code).status_code == 400


def test_account_disabled_after_issuing_code_cannot_sign_in(environment):
    code, _token = ticket(environment)
    environment[1].users['alice']['enabled'] = False
    assert exchange(environment, code).status_code == 403


def test_parent_keeps_own_login_and_child_view_identity(environment):
    code, _token = ticket(environment, username='alice_parent')
    response = exchange(environment, code)
    assert response.status_code == 200
    assert response.get_json()['username'] == 'alice_parent'
    assert response.get_json()['is_parent'] is True
    assert response.get_json()['child_username'] == 'alice'
    assert environment[1].verify_token(response.get_json()['access_token']) == 'alice_parent'


def test_disabled_or_invalid_parent_child_cannot_redeem_code(environment):
    code, _token = ticket(environment, username='alice_parent')
    environment[1].users['alice']['enabled'] = False
    assert exchange(environment, code).status_code == 403


def test_revocation_during_exchange_does_not_leave_a_new_session(environment, monkeypatch):
    code, _token = ticket(environment)
    services = environment[1]
    create = services.create_token
    def revoke_then_create(username):
        sessions.revoke_principal(sessions.SESSION_KIND_USER, username)
        return create(username)
    monkeypatch.setattr(services, 'create_token', revoke_then_create)
    assert exchange(environment, code).status_code == 401
    with sqlite3.connect(environment[2] / 'sessions.sqlite3') as connection:
        count = connection.execute('SELECT COUNT(*) FROM auth_sessions WHERE session_kind=?', (sessions.SESSION_KIND_USER,)).fetchone()[0]
    assert count == 0


def test_unknown_hosts_cannot_create_bridge_pages_or_site_configuration(environment):
    app, _services, _path = environment
    client = app.test_client()
    for path in ['/api/auth/sso/sites', '/auth/subject-start?from=english', '/auth/subject-callback']:
        assert client.get(path, base_url='https://attacker.example/').status_code == 404


def test_bridge_only_embeds_safe_config_and_uses_the_same_origin_script(environment):
    app, _services, _path = environment
    response = app.test_client().get('/auth/subject-start?from=english', base_url=SITES['chinese']['url'])
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'subject-sso-config' in html and 'subject-sso' in html
    assert 'source_token' not in html and 'access_token' not in html
    assert '"current_site": "chinese"' in html
    assert response.headers['X-Frame-Options'] == 'DENY'
    assert response.headers['Referrer-Policy'] == 'no-referrer'
    assert response.headers['Cache-Control'] == 'no-store'


@pytest.mark.parametrize('path', [
    '/auth/subject-start?from=https://attacker.example',
    '/auth/subject-start?from=chinese',
    '/auth/subject-authorize?target=math&state=short&code_challenge=' + CHALLENGE,
    '/auth/subject-authorize?target=https://attacker.example&state=' + 'a' * 43 + '&code_challenge=' + CHALLENGE,
])
def test_bridge_rejects_invalid_target_state_or_source_before_rendering(environment, path):
    app, _services, _path = environment
    assert app.test_client().get(path, base_url=SITES['chinese']['url']).status_code == 400


def test_request_scheme_behind_https_proxy_does_not_change_the_trusted_origin(environment):
    app, services, _path = environment
    token = services.create_token('alice')
    response = app.test_client().post('/api/auth/sso/ticket', base_url='http://english.example.test/',
        headers={'Authorization': 'Bearer ' + token, 'Origin': 'https://english.example.test'},
        json={'target': 'chinese', 'code_challenge': CHALLENGE})
    assert response.status_code == 201

"""Regression checks for deployment-facing request and account boundaries."""

import gzip
import sqlite3

import pytest

from test_simple_web_app import web


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setitem(web.app.config, 'TESTING', True)
    with web.app.test_client() as client:
        yield client


def test_forwarded_ip_is_ignored_until_proxy_is_explicitly_trusted(monkeypatch):
    monkeypatch.delenv('TRUSTED_PROXY_IPS', raising=False)
    with web.app.test_request_context(headers={'X-Forwarded-For': '198.51.100.1'},
                                     environ_base={'REMOTE_ADDR': '203.0.113.9'}):
        assert web._client_ip() == '203.0.113.9'
    monkeypatch.setenv('TRUSTED_PROXY_IPS', '127.0.0.1/32,10.0.0.0/8')
    with web.app.test_request_context(headers={'X-Forwarded-For': '192.0.2.99,198.51.100.7,10.0.0.2'},
                                     environ_base={'REMOTE_ADDR': '127.0.0.1'}):
        assert web._client_ip() == '198.51.100.7'
    with web.app.test_request_context(headers={'X-Forwarded-For': '198.51.100.1'},
                                     environ_base={'REMOTE_ADDR': '203.0.113.9'}):
        assert web._client_ip() == '203.0.113.9'


def test_invalid_forwarded_chain_falls_back_to_peer(monkeypatch):
    monkeypatch.setenv('TRUSTED_PROXY_IPS', '127.0.0.1/32')
    with web.app.test_request_context(headers={'X-Forwarded-For': 'not-an-ip'},
                                     environ_base={'REMOTE_ADDR': '127.0.0.1'}):
        assert web._client_ip() == '127.0.0.1'


def test_rate_buckets_expire_and_stay_bounded_without_evicting_active_limits(monkeypatch, tmp_path):
    clock = [100.0]
    monkeypatch.setattr(web, 'time', lambda: clock[0])
    monkeypatch.setattr(web, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(web, '_RATE_MAX_BUCKETS', 2)
    assert web._rate_allow('a', 1)
    assert not web._rate_allow('a', 1)
    assert web._rate_allow('b', 1)
    assert not web._rate_allow('c', 1)
    assert not web._rate_allow('a', 1)
    clock[0] += web._RATE_WINDOW_SEC
    assert web._rate_allow('c', 1)
    with sqlite3.connect(tmp_path / '_shared' / 'rate_limits.sqlite3') as connection:
        assert connection.execute('SELECT key FROM rate_buckets').fetchall() == [('c',)]


@pytest.mark.parametrize('configured', ['https://[invalid', 'https://example.com:bad',
                                        'https://user:pass@example.com', 'javascript:alert(1)',
                                        'https://example.com/?next=bad'])
def test_reset_origin_rejects_invalid_configuration(monkeypatch, configured):
    monkeypatch.setenv('PUBLIC_BASE_URL', configured)
    with web.app.test_request_context():
        with pytest.raises(RuntimeError):
            web._public_origin()


def test_reset_origin_never_uses_untrusted_host_in_production(monkeypatch):
    monkeypatch.setenv('FLASK_ENV', 'production')
    monkeypatch.delenv('PUBLIC_BASE_URL', raising=False)
    with web.app.test_request_context(base_url='https://attacker.example'):
        with pytest.raises(RuntimeError, match='PUBLIC_BASE_URL'):
            web._public_origin()
    monkeypatch.setenv('PUBLIC_BASE_URL', 'https://reciter.example/')
    with web.app.test_request_context(base_url='https://attacker.example'):
        assert web._public_origin() == 'https://reciter.example'


def test_missing_reset_origin_fails_before_creating_tokens(client, monkeypatch):
    monkeypatch.setenv('FLASK_ENV', 'production')
    monkeypatch.delenv('PUBLIC_BASE_URL', raising=False)
    monkeypatch.setattr(web, '_rate_allow', lambda *args: True)
    monkeypatch.setattr(web, '_smtp_config', lambda: {})
    monkeypatch.setattr(web, 'load_users', lambda: pytest.fail('must fail before looking up accounts'))
    response = client.post('/api/auth/forgot-password', json={'email': 'test@example.com'},
                           base_url='https://attacker.example')
    assert response.status_code == 503


def test_parent_creation_uses_unique_password_and_does_not_redisclose_it(client, monkeypatch):
    users = {'alice': {'enabled': True}, 'bobby': {'enabled': True}}
    monkeypatch.setattr(web, 'verify_admin_token', lambda token: True)
    monkeypatch.setattr(web, 'mutate_users', lambda fn, **kwargs: fn(users))
    headers = {'Authorization': 'Bearer admin-test'}
    first = client.patch('/api/admin/users/alice/parent', headers=headers, json={'enabled': True})
    second = client.patch('/api/admin/users/bobby/parent', headers=headers, json={'enabled': True})
    assert first.status_code == second.status_code == 200
    password = first.get_json()['default_password_hint']
    assert len(password) >= 12 and password != '123123'
    assert password != second.get_json()['default_password_hint']
    assert web.verify_password(password, users['alice_parent']['password_hash'])
    repeated = client.patch('/api/admin/users/alice/parent', headers=headers, json={'enabled': True})
    assert 'default_password_hint' not in repeated.get_json()


def test_gzip_negotiation_honors_q_zero_and_varies_plain_response(client):
    plain = client.get('/', headers={'Accept-Encoding': 'gzip;q=0'})
    assert plain.status_code == 200
    assert 'Content-Encoding' not in plain.headers
    assert 'Accept-Encoding' in plain.vary
    compressed = client.get('/', headers={'Accept-Encoding': 'gzip'})
    assert compressed.headers['Content-Encoding'] == 'gzip'
    assert gzip.decompress(compressed.data) == plain.data


def test_chat_stream_is_disabled_by_default(client, monkeypatch):
    monkeypatch.setattr(web, '_verify_chat_stream_auth', lambda: 'alice')
    monkeypatch.delenv('CHAT_SSE_ENABLED', raising=False)
    response = client.get('/api/chat/stream')
    assert response.status_code == 503


def test_corrupt_persistent_state_is_reported_without_disclosing_paths(monkeypatch):
    from storage_utils import PersistentStateLoadError

    with web.app.test_request_context():
        response, status = web._persistent_state_unavailable(PersistentStateLoadError('/private/state.json'))
        assert status == 503
        assert '/private' not in response.get_json()['error']

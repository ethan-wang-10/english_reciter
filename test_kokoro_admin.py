"""The shared administrator switch is hot-read, fail-closed, and preserves keys."""
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
import io
import json
import os
import wave

import pytest

import dictation_audio
from test_simple_web_app import web


@pytest.fixture
def state(tmp_path, monkeypatch):
    path = tmp_path / 'config.json'
    monkeypatch.setattr(web, '_APP_CONFIG_FILE', path)
    monkeypatch.setitem(web.app.config, 'TESTING', True)
    monkeypatch.setattr(web, 'verify_admin_token', lambda token: token == 'admin')
    monkeypatch.setattr(web, 'verify_token', lambda token: token if token in ('student', 'parent') else None)
    monkeypatch.setattr(web, 'get_user', lambda name: ({'enabled': True, 'role': 'parent', 'child_username': 'student'}
                                                     if name == 'parent' else {'enabled': True}) if name in ('student', 'parent') else None)
    monkeypatch.setattr(web, '_rate_allow', lambda *args: True)
    monkeypatch.setenv('DICTATION_KOKORO_URL', 'http://127.0.0.1:8877')
    monkeypatch.setenv('KOKORO_EXPERIMENT_ENABLED', '1')
    output = io.BytesIO()
    with wave.open(output, 'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(24000); stream.writeframes(b'\x10\x00' * 200)
    audio = output.getvalue()
    calls, piper = [], []
    ready = [True]
    def worker(endpoint, payload=None, **kwargs):
        calls.append((endpoint, payload))
        headers = Message(); headers['Content-Type'] = 'audio/wav'; headers['X-TTS-Cache'] = 'HIT'
        if endpoint == '/health':
            return json.dumps({'enabled': ready[0], 'ready': ready[0], 'model_path': '/private/not-public'}).encode(), headers
        if endpoint == '/prepare':
            count = len(payload['texts'])
            return json.dumps({'accepted': count, 'cached': 0, 'pending': 0, 'failed': 0, 'count': count}).encode(), headers
        return audio, headers
    monkeypatch.setattr(dictation_audio, '_worker', worker)
    monkeypatch.setattr(web, 'piper_runtime_ready', lambda: True)
    monkeypatch.setattr(web, 'piper_synthesize_wav', lambda text: piper.append(text) or audio)
    monkeypatch.setattr(web, 'piper_last_result_metadata', None)
    return {'path': path, 'calls': calls, 'piper': piper, 'ready': ready,
            'admin': {'Authorization': 'Bearer admin'}, 'student': {'Authorization': 'Bearer student'},
            'parent': {'Authorization': 'Bearer parent'}}


def test_admin_only_configuration_and_default_off_ignore_worker_environment(state):
    with web.app.test_client() as client:
        assert client.get('/api/admin/config').status_code == 401
        assert client.get('/api/admin/config', headers=state['student']).status_code == 401
        assert client.patch('/api/admin/config', headers=state['student'], json={'kokoro_enabled': True}).status_code == 401
        response = client.get('/api/admin/config', headers=state['admin'])
        assert response.status_code == 200
        assert response.json['kokoro_enabled'] is False and response.json['kokoro_service_ready'] is False
        assert response.json['kokoro_status'] == 'disabled'
        for subject in ('english', 'chinese'):
            assert client.get(f'/api/subjects/{subject}/dictation/audio/capabilities', headers=state['student']).json == {'kokoro': False, 'prepare': False}
        assert client.get('/api/tts/capabilities').json == {'piper': True, 'kokoro': False}
    assert state['calls'] == [] and not state['path'].exists()


@pytest.mark.parametrize('flag', [None, 0, 1, '', 'true', 'false', [], {}])
def test_admin_kokoro_flag_requires_a_real_boolean_and_never_overwrites_config(state, flag):
    original = {'deepseek_api_key': 'keep-secret', 'kokoro_enabled': False, 'unrelated': {'keep': True}}
    state['path'].write_text(json.dumps(original))
    before = state['path'].read_bytes()
    with web.app.test_client() as client:
        response = client.patch('/api/admin/config', headers=state['admin'], json={'kokoro_enabled': flag})
    assert response.status_code == 400 and state['path'].read_bytes() == before and state['calls'] == []


def test_admin_updates_preserve_existing_secrets_and_other_fields_and_read_status_safely(state):
    original = {'deepseek_api_key': 'keep-original-secret', 'article_ai_extract_enabled': True,
                'smtp_password': 'never-return', 'unrelated': {'nested': 7}}
    state['path'].write_text(json.dumps(original))
    with web.app.test_client() as client:
        response = client.patch('/api/admin/config', headers=state['admin'], json={'kokoro_enabled': True})
        assert response.status_code == 200 and response.json['kokoro_enabled'] is True and response.json['kokoro_service_ready'] is True
        assert response.json['kokoro_status'] == 'ready'
        assert json.loads(state['path'].read_text()) == {**original, 'kokoro_enabled': True}
        public = client.get('/api/admin/config', headers=state['admin'])
        assert public.json['deepseek_api_key_set'] and public.json['article_ai_extract_enabled']
        assert 'smtp_password' not in public.json and 'model_path' not in public.json
        assert '/private' not in public.get_data(as_text=True) and 'never-return' not in public.get_data(as_text=True)
        compatible = client.patch('/api/admin/config', headers=state['admin'], json={'article_ai_extract_enabled': False})
        assert compatible.status_code == 200
        assert json.loads(state['path'].read_text()) == {**original, 'article_ai_extract_enabled': False, 'kokoro_enabled': True}


def test_hot_config_changes_apply_to_both_subjects_without_restart_even_when_mtime_is_unchanged(state):
    state['path'].write_text(json.dumps({'kokoro_enabled': True}))
    original_mtime = state['path'].stat().st_mtime_ns
    with web.app.test_client() as client:
        for subject in ('english', 'chinese'):
            prefix = f'/api/subjects/{subject}/dictation/audio'
            assert client.get(prefix + '/capabilities', headers=state['parent']).json == {'kokoro': True, 'prepare': True}
            response = client.post(prefix, headers=state['parent'], json={'text': 'Hello' if subject == 'english' else '观潮'})
            assert response.status_code == 200 and response.headers['X-TTS-Engine'] == 'kokoro'
            assert client.post(prefix + '/prepare', headers=state['parent'], json={'texts': ['Hello' if subject == 'english' else '观潮']}).json['enabled']
        state['path'].write_text(json.dumps({'kokoro_enabled': False}))
        os.utime(state['path'], ns=(original_mtime, original_mtime))
        state['calls'].clear()
        for subject in ('english', 'chinese'):
            prefix = f'/api/subjects/{subject}/dictation/audio'
            assert client.get(prefix + '/capabilities', headers=state['parent']).json == {'kokoro': False, 'prepare': False}
            assert client.post(prefix + '/prepare', headers=state['parent'], json={'texts': ['Hello']}).json['enabled'] is False
        assert client.post('/api/subjects/english/dictation/audio', headers=state['parent'], json={'text': 'Hello'}).headers['X-TTS-Engine'] == 'piper'
        assert client.post('/api/subjects/chinese/dictation/audio', headers=state['parent'], json={'text': '观潮'}).status_code == 503
    assert state['calls'] == []


def test_persisted_disable_survives_worker_reset_failure_and_no_health_probe_runs(state, monkeypatch):
    state['path'].write_text(json.dumps({'kokoro_enabled': True, 'deepseek_api_key': 'keep'}))
    reset_states = []
    def fail_reset():
        reset_states.append(web.kokoro_enabled())
        raise OSError('worker is already down')
    monkeypatch.setattr(dictation_audio, 'reset_worker', fail_reset)
    with web.app.test_client() as client:
        response = client.patch('/api/admin/config', headers=state['admin'], json={'kokoro_enabled': False})
        assert response.status_code == 200 and response.json['kokoro_status'] == 'disabled'
        assert client.get('/api/admin/config', headers=state['admin']).json['kokoro_enabled'] is False
    assert reset_states == [False] and state['calls'] == []
    assert json.loads(state['path'].read_text()) == {'kokoro_enabled': False, 'deepseek_api_key': 'keep'}


@pytest.mark.parametrize('damaged', ['broken', '[]', '{"kokoro_enabled":"true"}', '{"kokoro_enabled":true,"kokoro_enabled":false}'])
def test_bad_configuration_fails_closed_and_admin_cannot_overwrite_original_file(state, damaged):
    state['path'].write_text(damaged)
    with web.app.test_client() as client:
        response = client.get('/api/admin/config', headers=state['admin'])
        assert response.status_code == 503 and response.json['kokoro_status'] == 'config_error'
        assert client.patch('/api/admin/config', headers=state['admin'], json={'kokoro_enabled': False}).status_code == 503
        assert client.patch('/api/admin/config', headers=state['admin'], json={'article_ai_extract_enabled': True}).status_code == 503
        assert client.get('/api/subjects/english/dictation/audio/capabilities', headers=state['student']).json['kokoro'] is False
        assert client.post('/api/words/speak-audio', headers=state['student'], json={'text': 'Hello'}).headers['X-TTS-Engine'] == 'piper'
    assert state['path'].read_text() == damaged and state['calls'] == []


def test_worker_unavailable_uses_existing_main_english_fallback_and_keeps_parent_policy(state, monkeypatch):
    state['path'].write_text(json.dumps({'kokoro_enabled': True}))
    with web.app.test_client() as client:
        response = client.post('/api/words/speak-audio', headers=state['student'], json={'text': "Don't forget."})
        assert response.status_code == 200 and response.headers['X-TTS-Engine'] == 'kokoro'
        assert client.post('/api/words/speak-audio', headers=state['parent'], json={'text': 'No'}).status_code == 403
        monkeypatch.setattr(dictation_audio, '_worker', lambda *args, **kwargs: None)
        response = client.post('/api/words/speak-audio', headers=state['student'], json={'text': 'A' * 501})
        assert response.status_code == 200 and response.headers['X-TTS-Engine'] == 'piper'
        assert state['piper'][-1] == 'A' * 500
        status = client.get('/api/admin/config', headers=state['admin'])
        assert status.json['kokoro_enabled'] is True and status.json['kokoro_status'] == 'unavailable'
        assert client.get('/api/tts/capabilities').json == {'piper': True, 'kokoro': False}


def test_concurrent_admin_changes_do_not_drop_unrelated_configuration(state):
    state['path'].write_text(json.dumps({'deepseek_api_key': 'keep', 'unrelated': 9}))
    def update(body):
        with web.app.test_client() as client:
            return client.patch('/api/admin/config', headers=state['admin'], json=body).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(update, [{'kokoro_enabled': True}, {'article_ai_extract_enabled': True}])) == [200, 200]
    assert json.loads(state['path'].read_text()) == {'deepseek_api_key': 'keep', 'unrelated': 9,
                                                   'kokoro_enabled': True, 'article_ai_extract_enabled': True}


def test_turning_off_while_worker_responds_discards_late_audio_and_preparation(state, monkeypatch):
    original = dictation_audio._worker
    def off_during_response(*args, **kwargs):
        response = original(*args, **kwargs)
        state['path'].write_text(json.dumps({'kokoro_enabled': False}))
        return response
    monkeypatch.setattr(dictation_audio, '_worker', off_during_response)
    with web.app.test_client() as client:
        for subject in ('english', 'chinese'):
            state['path'].write_text(json.dumps({'kokoro_enabled': True}))
            response = client.post(f'/api/subjects/{subject}/dictation/audio', headers=state['parent'], json={'text': 'Hello'})
            assert response.status_code == (200 if subject == 'english' else 503)
            if subject == 'english':
                assert response.headers['X-TTS-Engine'] == 'piper'
            state['path'].write_text(json.dumps({'kokoro_enabled': True}))
            response = client.post(f'/api/subjects/{subject}/dictation/audio/prepare', headers=state['parent'], json={'texts': ['Hello']})
            assert response.json['enabled'] is False and response.json['accepted'] == 0

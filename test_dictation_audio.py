"""Optional model gateway keeps authentication, full text and old audio fallbacks."""
from email.message import Message
from functools import wraps
import io
import json
from types import SimpleNamespace
import wave

from flask import Flask, g, jsonify, request
import pytest

import dictation_audio as audio
from english_dictation import register_english_dictation_routes


def wav():
    output = io.BytesIO()
    with wave.open(output, 'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(24000)
        stream.writeframes(b'\x10\x00' * 240)
    return output.getvalue()


def headers(kind='application/json', cache='MISS'):
    result = Message(); result['Content-Type'] = kind; result['X-TTS-Cache'] = cache
    return result


@pytest.fixture
def browser(tmp_path, monkeypatch):
    monkeypatch.delenv('DICTATION_KOKORO_URL', raising=False)
    state = SimpleNamespace(rate=[], allowed=True, piper=[], wav=wav(), kokoro_enabled=False)
    def authenticated(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            token = request.headers.get('Authorization')
            if token not in ('Bearer student', 'Bearer parent'):
                return jsonify({'error': '需要登录'}), 401
            g.login_username = 'parent' if token.endswith('parent') else 'student'
            return function('student', *args, **kwargs)
        return wrapped
    def rate(key, limit):
        state.rate.append((key, limit)); return state.allowed
    def piper(text):
        state.piper.append(text); return state.wav
    services = SimpleNamespace(token_required=authenticated, DATA_DIR=tmp_path/'users',
        _rate_allow=rate, _RATE_MAX_TTS_AUDIO=90, piper_runtime_ready=lambda: True,
        piper_synthesize_wav=piper, kokoro_enabled=lambda: state.kokoro_enabled)
    app = Flask(__name__); app.config['TESTING'] = True
    audio.register_dictation_audio_routes(app, services)
    register_english_dictation_routes(app, services)
    return SimpleNamespace(client=app.test_client(), state=state, services=services,
        headers={'Authorization': 'Bearer student'}, parent={'Authorization': 'Bearer parent'})


def test_disabled_gateway_never_contacts_a_worker_and_existing_english_audio_still_works(browser, monkeypatch):
    monkeypatch.setattr(audio.urllib.request, 'build_opener', lambda *_: pytest.fail('Disabled worker must not be contacted'))
    for subject in ('english', 'chinese'):
        response = browser.client.get(f'/api/subjects/{subject}/dictation/audio/capabilities', headers=browser.headers)
        assert response.json == {'kokoro': False, 'prepare': False}
        assert 'no-store' in response.headers['Cache-Control']
        prepared = browser.client.post(f'/api/subjects/{subject}/dictation/audio/prepare', headers=browser.parent, json={'texts': ['Hello' if subject == 'english' else '潮水']})
        assert prepared.status_code == 200 and prepared.json['enabled'] is False
    response = browser.client.post('/api/subjects/english/dictation/audio', headers=browser.parent, json={'text': 'Hello, world!'})
    assert response.status_code == 200 and response.data == browser.state.wav
    assert response.headers['X-TTS-Engine'] == 'piper' and browser.state.piper == ['Hello, world!']
    assert browser.client.post('/api/subjects/chinese/dictation/audio', headers=browser.headers, json={'text': '潮水'}).status_code == 503
    assert not browser.services.DATA_DIR.exists()


@pytest.mark.parametrize('origin', ['https://127.0.0.1:8877', 'http://localhost:8877', 'http://0.0.0.0:8877',
    'http://example.com:8877', 'http://127.0.0.1:65536', 'http://user@127.0.0.1:8877', 'http://127.0.0.1:8877/path'])
def test_gateway_cannot_forward_private_dictation_to_external_or_mistyped_origins(monkeypatch, origin):
    monkeypatch.setenv('DICTATION_KOKORO_URL', origin)
    monkeypatch.setattr(audio.urllib.request, 'build_opener', lambda *_: pytest.fail('Invalid worker must not be contacted'))
    assert audio.kokoro_audio('Private sentence.', 'en') is None


def test_both_languages_require_shared_authentication_for_capabilities_preparation_and_playback(browser):
    for subject in ('english', 'chinese'):
        prefix = f'/api/subjects/{subject}/dictation/audio'
        assert browser.client.get(prefix+'/capabilities').status_code == 401
        assert browser.client.post(prefix+'/prepare', json={'texts':['word']}).status_code == 401
        assert browser.client.post(prefix, json={'text':'word'}).status_code == 401


def test_ready_worker_preserves_full_corrected_text_and_parent_identity_and_skips_piper(browser, monkeypatch):
    browser.state.kokoro_enabled = True
    calls = []
    def worker(path, payload=None, **kwargs):
        calls.append((path,payload))
        if path == '/health':return json.dumps({'enabled':True,'ready':True}).encode(),headers()
        return browser.state.wav,headers('audio/wav','HIT')
    monkeypatch.setattr(audio,'_worker',worker)
    for subject,text,language in (('english','A'*500,'en'),('chinese','单薄的薄','zh')):
        prefix=f'/api/subjects/{subject}/dictation/audio'
        assert browser.client.get(prefix+'/capabilities',headers=browser.headers).json == {'kokoro':True,'prepare':True}
        response=browser.client.post(prefix,headers=browser.parent,json={'text':text})
        assert response.status_code==200 and response.data==browser.state.wav
        assert response.headers['X-TTS-Engine']=='kokoro' and response.headers['X-TTS-Cache']=='HIT'
        assert calls[-1]==('/audio',{'text':text,'language':language})
    assert browser.state.piper==[] and browser.state.rate==[('tts_audio:student',90)]*2
    assert not browser.services.DATA_DIR.exists()


@pytest.mark.parametrize('result', [None, (b'not a wav',headers('audio/wav')),
    (b'RIFF'+b'\0'*100,headers('audio/wav')), (wav()[:-8],headers('audio/wav')), (wav(),headers('application/json'))])
def test_unavailable_or_incomplete_kokoro_audio_falls_back_without_losing_the_sentence(browser,monkeypatch,result):
    browser.state.kokoro_enabled = True
    monkeypatch.setattr(audio,'_worker',lambda *_args,**_kwargs:result)
    text="Don't run, please."
    response=browser.client.post('/api/subjects/english/dictation/audio',headers=browser.headers,json={'text':text})
    assert response.status_code==200 and response.headers['X-TTS-Engine']=='piper'
    assert browser.state.piper==[text]
    chinese=browser.client.post('/api/subjects/chinese/dictation/audio',headers=browser.headers,json={'text':'潮水'})
    assert chinese.status_code==503 and chinese.json['error']


def test_prepare_is_bounded_normalizes_exact_text_and_returns_only_counts(browser,monkeypatch):
    browser.state.kokoro_enabled = True
    monkeypatch.setenv('DICTATION_KOKORO_URL','http://127.0.0.1:8877')
    calls=[]
    def worker(path,payload=None,**kwargs):
        calls.append((path,payload));return json.dumps({'accepted':2,'cached':0,'pending':2,'failed':0,'count':2,'private':'never expose'}).encode(),headers()
    monkeypatch.setattr(audio,'_worker',worker)
    prefix='/api/subjects/english/dictation/audio/prepare'
    response=browser.client.post(prefix,headers=browser.parent,json={'texts':[' Hello   there. ','Hello there.',"Don't run, please."]})
    assert response.status_code==200 and response.json=={'enabled':True,'accepted':2,'cached':0,'pending':2,'failed':0,'count':2}
    assert calls==[('/prepare',{'language':'en','texts':['Hello there.',"Don't run, please."]})]
    assert browser.state.rate==[('tts_prepare:student',10)] and browser.state.piper==[]
    for payload in ({'texts':['A'*501]}, {'texts':['a']*401}, {'texts':['word'],'voice':'private'}, {'texts':['a\nb']}):
        assert browser.client.post(prefix,headers=browser.headers,json=payload).status_code==400
    assert len(calls)==1
    assert browser.client.post('/api/subjects/chinese/dictation/audio/prepare',headers=browser.headers,json={'texts':['词']*201}).status_code==400


def test_preparation_worker_failure_does_not_claim_success_or_change_saved_lists(browser,monkeypatch):
    browser.state.kokoro_enabled = True
    monkeypatch.setenv('DICTATION_KOKORO_URL','http://127.0.0.1:8877')
    monkeypatch.setattr(audio,'_worker',lambda *_args,**_kwargs:None)
    response=browser.client.post('/api/subjects/english/dictation/audio/prepare',headers=browser.headers,json={'texts':['apple']})
    assert response.status_code==200 and response.json['enabled'] is False and response.json['failed']==1
    assert not browser.services.DATA_DIR.exists()
    browser.state.allowed=False
    assert browser.client.post('/api/subjects/chinese/dictation/audio',headers=browser.headers,json={'text':'词'}).status_code==429
    assert browser.client.post('/api/subjects/english/dictation/audio/prepare',headers=browser.headers,json={'texts':['word']}).status_code==429


def test_interrupted_local_http_response_uses_existing_english_fallback(browser,monkeypatch):
    from http.client import IncompleteRead
    monkeypatch.setenv('DICTATION_KOKORO_URL','http://127.0.0.1:8877')
    class Interrupted:
        def open(self,*args,**kwargs):
            raise IncompleteRead(b'partial local response',100)
    monkeypatch.setattr(audio.urllib.request,'build_opener',lambda *_:Interrupted())
    response=browser.client.post('/api/subjects/english/dictation/audio',headers=browser.headers,json={'text':'A complete sentence.'})
    assert response.status_code==200 and response.headers['X-TTS-Engine']=='piper'
    assert browser.state.piper==['A complete sentence.']

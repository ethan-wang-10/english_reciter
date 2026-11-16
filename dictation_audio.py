"""Optional loopback Kokoro gateway; no model is loaded by the web process."""
from __future__ import annotations

import io
from http.client import HTTPException
import json
import os
import re
import urllib.error
import urllib.request
import wave

from flask import Blueprint, Response, current_app, has_app_context, jsonify, request

from subject_dictation import DictationError, _word

MAX_WAV_BYTES = 25_000_000
LANGUAGES = {'english': ('en', 500, 400), 'chinese': ('zh', 100, 200)}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _origin():
    value = os.getenv('DICTATION_KOKORO_URL', '').strip().rstrip('/')
    # Explicit opt-in to a private worker. Never forward text to an external
    # host, a proxy, or a redirect, even if a deployment value is mistyped.
    return value if re.fullmatch(r'http://127\.0\.0\.1:[1-9][0-9]{0,4}', value) and int(value.rsplit(':', 1)[1]) <= 65535 else ''


def _worker(path, payload=None, *, timeout=1.5, maximum=100_000):
    origin = _origin()
    if not origin:
        return None
    headers = {'Accept': 'audio/wav' if path == '/audio' else 'application/json'}
    body = None
    if payload is not None:
        headers['Content-Type'] = 'application/json'
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    message = urllib.request.Request(origin + path, data=body, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(message, timeout=timeout) as response:
            if response.status != 200:
                return None
            content = response.read(maximum + 1)
            if len(content) > maximum:
                return None
            return content, response.headers
    except (OSError, ValueError, HTTPException, urllib.error.URLError):
        return None


def enabled():
    """Read the shared administrator switch for every request, never an env flag."""
    if not has_app_context():
        return False
    reader = current_app.config.get('KOKORO_ENABLED_READER')
    try:
        return callable(reader) and reader() is True
    except Exception:
        return False


def reset_worker():
    """Best effort after persisting off; a failed reset never re-enables speech."""
    return _worker('/reset', {}, timeout=1.0) is not None


def capabilities():
    if not enabled():
        return {'kokoro': False, 'prepare': False}
    result = _worker('/health', timeout=.6)
    if result and enabled():
        try:
            health = json.loads(result[0])
            ready = isinstance(health, dict) and health.get('enabled') is True and health.get('ready') is True
            return {'kokoro': ready, 'prepare': ready}
        except (ValueError, TypeError, RecursionError):
            pass
    return {'kokoro': False, 'prepare': False}


def kokoro_audio(text, language):
    """Return a complete validated WAV or None, preserving the caller's fallback."""
    if not enabled():
        return None
    result = _worker('/audio', {'text': text, 'language': language}, timeout=15, maximum=MAX_WAV_BYTES)
    if not result or not enabled():
        return None
    content, headers = result
    if headers.get_content_type() not in ('audio/wav', 'audio/x-wav'):
        return None
    try:
        with wave.open(io.BytesIO(content), 'rb') as audio:
            frames = audio.getnframes()
            if (audio.getcomptype() != 'NONE' or audio.getnchannels() not in (1, 2)
                    or audio.getsampwidth() != 2 or not 8000 <= audio.getframerate() <= 48000 or frames <= 0):
                return None
            if len(audio.readframes(frames)) != frames * audio.getnchannels() * audio.getsampwidth():
                return None
    except (ValueError, EOFError, wave.Error):
        return None
    return content, headers.get('X-TTS-Cache') == 'HIT'


def audio_response(content, hit=False):
    response = Response(content, mimetype='audio/wav')
    response.cache_control.no_store = True
    response.headers['X-TTS-Engine'] = 'kokoro'
    response.headers['X-TTS-Cache'] = 'HIT' if hit else 'MISS'
    return response


def register_dictation_audio_routes(app, services):
    routes = Blueprint('dictation_audio', __name__)
    app.config['KOKORO_ENABLED_READER'] = lambda: getattr(services, 'kokoro_enabled', lambda: False)()

    @routes.errorhandler(DictationError)
    def invalid(error):
        return jsonify({'error': str(error), **error.details}), error.status

    @routes.after_request
    def private(response):
        response.cache_control.no_store = True
        return response

    def settings(subject):
        if subject not in LANGUAGES:
            raise DictationError('学科不存在。', 404)
        return LANGUAGES[subject]

    @routes.get('/api/subjects/<subject>/dictation/audio/capabilities')
    @services.token_required
    def available(username, subject):
        settings(subject)
        return jsonify(capabilities())

    @routes.post('/api/subjects/<subject>/dictation/audio/prepare')
    @services.token_required
    def prepare(username, subject):
        language, limit, count = settings(subject)
        if request.content_length is not None and request.content_length > 2_500_000:
            raise DictationError('本次朗读内容过多，请分清单准备。')
        body = request.get_json(silent=True)
        if (not isinstance(body, dict) or set(body) != {'texts'} or not isinstance(body['texts'], list)
                or len(body['texts']) > count):
            raise DictationError('请提交有效的朗读清单。')
        texts = list(dict.fromkeys(_word({'text': text}, max_text=limit, max_spoken=limit)['text'] for text in body['texts']))
        if not enabled() or not _origin():
            return jsonify({'enabled': False, 'accepted': 0, 'cached': 0, 'pending': 0, 'failed': 0, 'count': len(texts)})
        if not services._rate_allow(f'tts_prepare:{username}', 10):
            raise DictationError('朗读内容正在准备中，请稍后再试。', 429)
        result = _worker('/prepare', {'language': language, 'texts': texts})
        if not enabled():
            return jsonify({'enabled': False, 'accepted': 0, 'cached': 0, 'pending': 0, 'failed': len(texts), 'count': len(texts)})
        try:
            value = json.loads(result[0]) if result else None
            fields = ('accepted', 'cached', 'pending', 'failed', 'count')
            if (not isinstance(value, dict) or any(type(value.get(key)) is not int or not 0 <= value[key] <= 10000 for key in fields)
                    or value['count'] != len(texts)):
                raise ValueError('Invalid preparation receipt')
            return jsonify({'enabled': True, **{key: value[key] for key in fields}})
        except (ValueError, TypeError, RecursionError):
            return jsonify({'enabled': False, 'accepted': 0, 'cached': 0, 'pending': 0, 'failed': len(texts), 'count': len(texts)})

    @routes.post('/api/subjects/chinese/dictation/audio')
    @services.token_required
    def chinese_audio(username):
        if request.content_length is not None and request.content_length > 8000:
            raise DictationError('朗读内容过长。')
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) != {'text'}:
            raise DictationError('请提交有效的朗读内容。')
        text = _word({'text': body['text']}, max_text=100, max_spoken=100)['text']
        if not services._rate_allow(f'tts_audio:{username}', services._RATE_MAX_TTS_AUDIO):
            raise DictationError('朗读请求过于频繁，请稍后再试。', 429)
        result = kokoro_audio(text, 'zh')
        if not result:
            raise DictationError('服务器普通话朗读暂时不可用，请使用设备普通话语音。', 503)
        return audio_response(*result)

    app.register_blueprint(routes)

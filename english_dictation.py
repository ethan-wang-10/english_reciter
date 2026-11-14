"""Paper-and-pencil English dictation, separate from graded learning and exams."""

from __future__ import annotations

from copy import deepcopy
import math

from flask import Blueprint, Response, g, jsonify, request

from subject_dictation import DictationError, DictationStore, _id, _word, validate_mistake_items


BOOK_ID = 'english-custom-dictation'
DEFAULT_LESSON_ID = 'default-list'
MAX_TEXT = 500
MAX_REQUEST_BYTES = 3_500_000
MAX_MISTAKE_REQUEST_BYTES = 13_000_000
MAX_AUDIO_REQUEST_BYTES = 8_000
DEFAULT_BOOK = {
    'id': BOOK_ID, 'title': '我的英语默写', 'edition': '自建清单',
    'description': '按自己的清单准备单词和句子，听完一条，写好后再继续。',
    'catalog_note': '每行一条，可以输入单词或完整句子。',
    'lessons': [{'id': DEFAULT_LESSON_ID, 'title': '我的单词与句子', 'number': None,
                 'custom': False, 'unit': '我的清单'}],
}


def register_english_dictation_routes(app, services):
    routes = Blueprint('english_dictation', __name__, url_prefix='/api/subjects/english/dictation')

    @routes.errorhandler(DictationError)
    def invalid(error):
        return jsonify({'error': str(error), **error.details}), error.status

    @routes.after_request
    def private(response):
        response.cache_control.no_store = True
        return response

    def book(book_id):
        if _id(book_id) != BOOK_ID:
            raise DictationError('默写清单不存在，请重新选择。', 404)
        return deepcopy(DEFAULT_BOOK)

    def store(username):
        return DictationStore(services.DATA_DIR / username / 'subjects/english/dictation.sqlite3',
                              max_text=MAX_TEXT, max_spoken=MAX_TEXT)

    def merged_lessons(username):
        items = [*deepcopy(DEFAULT_BOOK['lessons']), *store(username).list_custom_lessons(BOOK_ID)]
        return sorted(items, key=lambda item: (item['number'] is None, item['number'] or 10000, item['custom'], item['id']))

    def lesson(username, book_id, lesson_id):
        book(book_id); _id(lesson_id)
        result = next((entry for entry in merged_lessons(username) if entry['id'] == lesson_id), None)
        if result is None:
            raise DictationError('默写清单不存在，请重新选择。', 404)
        return result

    def body(required, optional=(), *, maximum=MAX_REQUEST_BYTES):
        if request.content_length is not None and request.content_length > maximum:
            raise DictationError('本次输入内容过长，请分清单录入。')
        value = request.get_json(silent=True)
        if (not isinstance(value, dict) or not set(required) <= set(value)
                or set(value) - set(required) - set(optional)):
            raise DictationError('请提交有效的默写内容。')
        return value

    @routes.get('/books')
    @services.token_required
    def list_books(username):
        return jsonify({'items': [deepcopy(DEFAULT_BOOK)], 'count': 1})

    @routes.get('/lessons')
    @services.token_required
    def list_lessons(username):
        book(request.args.get('book_id'))
        items = merged_lessons(username)
        return jsonify({'items': items, 'count': len(items)})

    @routes.post('/lessons')
    @services.token_required
    def add_lesson(username):
        data = body(('book_id', 'title'), ('number',))
        book(data['book_id'])
        item, created = store(username).create_lesson(BOOK_ID, data['title'], data.get('number'), getattr(g, 'login_username', username))
        return jsonify({'lesson': item, 'duplicate': not created}), 201 if created else 200

    @routes.get('/wordlists')
    @services.token_required
    def list_wordlists(username):
        book(request.args.get('book_id'))
        items = store(username).list_wordlists(BOOK_ID)
        return jsonify({'items': items, 'count': len(items)})

    @routes.get('/wordlists/<book_id>/<lesson_id>')
    @services.token_required
    def get_wordlist(username, book_id, lesson_id):
        entry = lesson(username, book_id, lesson_id)
        return jsonify({'wordlist': store(username).get_wordlist(book_id, lesson_id), 'lesson': entry})

    @routes.put('/wordlists/<book_id>/<lesson_id>')
    @services.token_required
    def save_wordlist(username, book_id, lesson_id):
        lesson(username, book_id, lesson_id)
        data = body(('base_revision', 'words'))
        return jsonify(store(username).save_wordlist(book_id, lesson_id, data['base_revision'], data['words'], getattr(g, 'login_username', username)))

    @routes.get('/mistakes')
    @services.token_required
    def list_mistakes(username):
        book(request.args.get('book_id'))
        current_lessons = {entry['id']: entry for entry in merged_lessons(username)}
        items = store(username).list_mistakes(BOOK_ID)
        for item in items:
            item['lesson'] = current_lessons.get(item['lesson_id'], item['lesson'])
        items.sort(key=lambda item: (item['lesson']['number'] is None, item['lesson']['number'] or 10000,
                                     item['lesson']['custom'], item['lesson_id']))
        return jsonify({'items': items, 'count': len(items), 'word_count': sum(item['word_count'] for item in items)})

    @routes.post('/mistakes')
    @services.token_required
    def add_mistakes(username):
        data = body(('items',), maximum=MAX_MISTAKE_REQUEST_BYTES)
        items = validate_mistake_items(data['items'], max_text=MAX_TEXT, max_spoken=MAX_TEXT)
        current_lessons = None
        for item in items:
            book(item['book_id'])
            if current_lessons is None:
                current_lessons = {entry['id']: entry for entry in merged_lessons(username)}
            entry = current_lessons.get(item['lesson_id'])
            if entry is None:
                raise DictationError('默写清单不存在，请重新选择。', 404)
            item['lesson'] = entry
        return jsonify(store(username).merge_mistakes(items, getattr(g, 'login_username', username)))

    @routes.delete('/mistakes/<book_id>/<lesson_id>')
    @services.token_required
    def remove_mistakes(username, book_id, lesson_id):
        book(book_id); _id(lesson_id)
        current_lessons = {entry['id'] for entry in merged_lessons(username)}
        if (lesson_id not in current_lessons
                and not any(group['lesson_id'] == lesson_id for group in store(username).list_mistakes(book_id))):
            raise DictationError('默写清单不存在，请重新选择。', 404)
        data = body(('words',))
        return jsonify(store(username).remove_mistakes(book_id, lesson_id, data['words']))

    @routes.post('/audio')
    @services.token_required
    def audio(username):
        data = body(('text',), maximum=MAX_AUDIO_REQUEST_BYTES)
        # Validate the whole string before invoking Piper. The older sanitizer
        # slices input, which would silently remove the end of a long sentence.
        text = _word({'text': data['text']}, max_text=MAX_TEXT, max_spoken=MAX_TEXT)['text']
        if not services._rate_allow(f'tts_audio:{username}', services._RATE_MAX_TTS_AUDIO):
            raise DictationError('朗读请求过于频繁，请稍后再试。', 429)
        ready = getattr(services, 'piper_runtime_ready', None)
        synthesize = getattr(services, 'piper_synthesize_wav', None)
        try:
            if ready is None or synthesize is None or not ready():
                raise DictationError('英语语音暂时不可用，请使用设备英语语音或稍后重试。', 503)
            wav = synthesize(text)
            if not isinstance(wav, (bytes, bytearray)) or not wav:
                raise DictationError('英语语音暂时未能生成，请使用设备英语语音或重试。', 503)
        except DictationError:
            raise
        except Exception:
            raise DictationError('英语语音暂时不可用，请使用设备英语语音或稍后重试。', 503) from None
        response = Response(bytes(wav), mimetype='audio/wav')
        metadata = getattr(services, 'piper_last_result_metadata', None)
        if callable(metadata):
            try:
                details = metadata()
                response.headers['X-TTS-Cache'] = 'HIT' if details.get('cache_hit') else 'MISS'
                duration = float(details.get('duration_ms') or 0)
                if math.isfinite(duration) and duration >= 0:
                    response.headers['Server-Timing'] = f'piper;dur={duration:.1f}'
            except Exception:
                pass  # Optional timing metadata must not discard a valid WAV.
        return response

    app.register_blueprint(routes)

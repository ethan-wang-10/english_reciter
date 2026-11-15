"""Paper-and-pencil English dictation, separate from graded learning and exams."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import math

from flask import Blueprint, Response, g, jsonify, request

from subject_dictation import DictationError, DictationStore, MAX_REVISION, _encode, _id, _text, _word, validate_mistake_items


BOOK_ID = 'english-custom-dictation'
DEFAULT_LESSON_ID = 'default-list'
GRADE4_BOOK_ID = 'shanghai-english-grade4-term1-2026'
MAX_TEXT = 500
MAX_WORDS = 400
MAX_MISTAKES = 2000
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
GRADE4_UNITS = (
    'My school', 'My classmates', 'Animals and their homes', 'Our birthday', 'Visiting places',
    "It's autumn!", 'My healthy breakfast', 'Be honest', 'What time is it?', 'Weather',
)
GRADE4_BOOK = {
    'id': GRADE4_BOOK_ID, 'title': '英语四年级上册', 'grade': 4, 'term': 1,
    'edition': '上海五四学制 · 2026年7月第一版',
    'description': '按课本单元整理单词、短语和句子，听完一条，写好后再继续。',
    'catalog_note': '每个 Unit 一份默写清单，单词、短语和句子都按一行一条录入。',
    'lessons': [
        {'id': f'unit-{number:02d}', 'title': '默写内容', 'number': None,
         'custom': False, 'unit': f'Unit {number} · {unit}'}
        for number, unit in enumerate(GRADE4_UNITS, 1)
    ],
}
BOOKS = (GRADE4_BOOK, DEFAULT_BOOK)
LEGACY_UNIT_IDS = {f'unit-{number:02d}-{kind}': f'unit-{number:02d}'
                   for number in range(1, 11) for kind in ('words', 'sentences')}


def canonical_lesson_id(book_id, lesson_id):
    return LEGACY_UNIT_IDS.get(lesson_id, lesson_id) if book_id == GRADE4_BOOK_ID else lesson_id


def _moment(value):
    if _text(value, 100) != value:
        raise ValueError('Invalid migration timestamp')
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone(timedelta(hours=8)))
    return timestamp.astimezone(timezone.utc)


class EnglishDictationStore(DictationStore):
    """Merge the two retired Unit lists once, retaining their original rows."""

    def __init__(self, path):
        super().__init__(path, max_text=MAX_TEXT, max_spoken=MAX_TEXT,
                         max_words=MAX_WORDS, max_mistakes=MAX_MISTAKES)

    @staticmethod
    def _needs_merge(connection):
        has_mistakes = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='dictation_mistakes'").fetchone()
        for number in range(1, 11):
            target = f'unit-{number:02d}'
            if connection.execute('SELECT 1 FROM dictation_wordlists WHERE book_id=? AND lesson_id=?', (GRADE4_BOOK_ID, target)).fetchone():
                continue
            aliases = (target + '-words', target + '-sentences')
            for table in ('dictation_wordlists', *(['dictation_mistake_groups', 'dictation_mistakes'] if has_mistakes else [])):
                if connection.execute('SELECT 1 FROM ' + table + ' WHERE book_id=? AND lesson_id IN (?,?) LIMIT 1', (GRADE4_BOOK_ID, *aliases)).fetchone():
                    return True
        return False

    def _merge_units(self, connection):
        if not self._needs_merge(connection):
            return
        try:
            old_store = DictationStore(self.path, max_text=MAX_TEXT, max_spoken=MAX_TEXT)
            raw = {row['lesson_id']: row for row in connection.execute('SELECT * FROM dictation_wordlists WHERE book_id=?', (GRADE4_BOOK_ID,))}
            notebooks = {group['lesson_id']: group for group in self._mistake_groups(connection, GRADE4_BOOK_ID)}
            plans = []
            for entry in GRADE4_BOOK['lessons']:
                target = entry['id']; aliases = (target + '-words', target + '-sentences')
                if target in raw:
                    self._public_wordlist(raw[target])
                    continue
                source_lists = [old_store._public_wordlist(raw[source]) for source in aliases if source in raw]
                source_groups = [notebooks[source] for source in aliases if source in notebooks]
                if not source_lists and not source_groups:
                    continue
                if target in notebooks:
                    raise ValueError('Partial Unit migration has no canonical wordlist marker')
                merged, word_times, marked, metadata = {}, {}, {}, []
                for source in source_lists:
                    moment = _moment(source['updated_at'])
                    if _text(source['updated_by'], 64) != source['updated_by']:
                        raise ValueError('Invalid source wordlist actor')
                    metadata.append((moment, source['updated_at'], source['updated_by']))
                    for word in source['words']:
                        if word['text'] not in merged or moment > word_times[word['text']]:
                            merged[word['text']] = deepcopy(word)
                            word_times[word['text']] = moment
                for source in source_groups:
                    if source['word_count'] > 1000:
                        raise ValueError('Invalid retired mistake group size')
                    for word in source['words']:
                        added, updated = _moment(word['added_at']), _moment(word['updated_at'])
                        metadata.append((updated, word['updated_at'], word['updated_by']))
                        previous = marked.get(word['text'])
                        if previous is None:
                            marked[word['text']] = deepcopy(word)
                        else:
                            first_added = word['added_at'] if added < _moment(previous['added_at']) else previous['added_at']
                            chosen = deepcopy(word) if updated > _moment(previous['updated_at']) else deepcopy(previous)
                            chosen['added_at'] = first_added
                            marked[word['text']] = chosen
                revision = max((source['revision'] for source in source_lists), default=0) + 1
                if revision > MAX_REVISION or len(merged) > self.max_words or len(marked) > self.max_mistakes:
                    raise ValueError('Merged Unit exceeds safe storage limits')
                _, updated_at, actor = max(metadata, key=lambda item: item[0])
                plans.append((entry, list(merged.values()), list(marked.values()), revision, updated_at, actor))
            # Validate all ten Units first; every write belongs to this one transaction.
            for entry, words, mistakes, revision, updated_at, actor in plans:
                connection.execute('INSERT INTO dictation_wordlists(book_id,lesson_id,words_json,revision,updated_at,updated_by) VALUES(?,?,?,?,?,?)',
                                   (GRADE4_BOOK_ID, entry['id'], _encode(words), revision, updated_at, actor))
                if mistakes:
                    connection.execute('INSERT INTO dictation_mistake_groups(book_id,lesson_id,lesson_json) VALUES(?,?,?)',
                                       (GRADE4_BOOK_ID, entry['id'], _encode(entry)))
                    connection.executemany('INSERT INTO dictation_mistakes(book_id,lesson_id,text,spoken_text,added_at,updated_at,updated_by) VALUES(?,?,?,?,?,?,?)',
                                           [(GRADE4_BOOK_ID, entry['id'], word['text'], word['spoken_text'], word['added_at'], word['updated_at'], word['updated_by']) for word in mistakes])
        except (ValueError, TypeError, KeyError) as error:
            raise DictationError('原有 Unit 清单暂时无法安全合并，全部原稿已保留，请稍后重试或联系管理员。', 503) from error

    @contextmanager
    def _connection(self, *, write=False):
        if not write and self.path.is_file():
            with super()._connection() as connection:
                needed = self._needs_merge(connection)
            if needed:
                with super()._connection(write=True) as connection:
                    self._merge_units(connection)
        with super()._connection(write=write) as connection:
            if write:
                self._merge_units(connection)
            yield connection

    def list_wordlists(self, book_id):
        _id(book_id)
        if not self.path.is_file():
            return []
        with self._connection() as connection:
            rows = connection.execute('SELECT * FROM dictation_wordlists WHERE book_id=? ORDER BY lesson_id', (book_id,)).fetchall()
            return [self._public_wordlist(row) for row in rows
                    if book_id != GRADE4_BOOK_ID or row['lesson_id'] not in LEGACY_UNIT_IDS]

    def list_mistakes(self, book_id):
        return [group for group in super().list_mistakes(book_id)
                if book_id != GRADE4_BOOK_ID or group['lesson_id'] not in LEGACY_UNIT_IDS]


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
        _id(book_id)
        selected = next((item for item in BOOKS if item['id'] == book_id), None)
        if selected is None:
            raise DictationError('默写清单不存在，请重新选择。', 404)
        return deepcopy(selected)

    def store(username, *, metadata_only=False):
        path = services.DATA_DIR / username / 'subjects/english/dictation.sqlite3'
        if metadata_only:
            return DictationStore(path, max_text=MAX_TEXT, max_spoken=MAX_TEXT,
                                  max_words=MAX_WORDS, max_mistakes=MAX_MISTAKES)
        return EnglishDictationStore(path)

    def merged_lessons(username, selected):
        return [*deepcopy(selected['lessons']), *store(username, metadata_only=True).list_custom_lessons(selected['id'])]

    def lesson(username, book_id, lesson_id):
        selected = book(book_id); _id(lesson_id)
        target = canonical_lesson_id(book_id, lesson_id)
        result = next((entry for entry in merged_lessons(username, selected) if entry['id'] == target), None)
        if result is None:
            raise DictationError('默写清单不存在，请重新选择。', 404)
        if target != lesson_id:
            result = {**result, 'id': lesson_id, 'title': '单词与短语' if lesson_id.endswith('-words') else '句子'}
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
        return jsonify({'items': deepcopy(BOOKS), 'count': len(BOOKS)})

    @routes.get('/lessons')
    @services.token_required
    def list_lessons(username):
        selected = book(request.args.get('book_id'))
        items = merged_lessons(username, selected)
        return jsonify({'items': items, 'count': len(items)})

    @routes.post('/lessons')
    @services.token_required
    def add_lesson(username):
        data = body(('book_id', 'title'), ('number',))
        selected = book(data['book_id'])
        item, created = store(username).create_lesson(selected['id'], data['title'], data.get('number'), getattr(g, 'login_username', username))
        return jsonify({'lesson': item, 'duplicate': not created}), 201 if created else 200

    @routes.get('/wordlists')
    @services.token_required
    def list_wordlists(username):
        selected = book(request.args.get('book_id'))
        order = {entry['id']: index for index, entry in enumerate(merged_lessons(username, selected))}
        items = store(username).list_wordlists(selected['id'])
        items.sort(key=lambda item: (order.get(item['lesson_id'], len(order)), item['lesson_id']))
        return jsonify({'items': items, 'count': len(items)})

    @routes.get('/wordlists/<book_id>/<lesson_id>')
    @services.token_required
    def get_wordlist(username, book_id, lesson_id):
        entry = lesson(username, book_id, lesson_id)
        return jsonify({'wordlist': store(username).get_wordlist(book_id, lesson_id), 'lesson': entry})

    @routes.put('/wordlists/<book_id>/<lesson_id>')
    @services.token_required
    def save_wordlist(username, book_id, lesson_id):
        if canonical_lesson_id(book_id, lesson_id) != lesson_id:
            raise DictationError('这个 Unit 已合并为一份清单，请先复制保留当前草稿，再刷新页面继续。',
                                 409, code='catalog_updated')
        lesson(username, book_id, lesson_id)
        data = body(('base_revision', 'words'))
        return jsonify(store(username).save_wordlist(book_id, lesson_id, data['base_revision'], data['words'], getattr(g, 'login_username', username)))

    @routes.get('/mistakes')
    @services.token_required
    def list_mistakes(username):
        selected = book(request.args.get('book_id'))
        current_lessons = {entry['id']: entry for entry in merged_lessons(username, selected)}
        order = {lesson_id: index for index, lesson_id in enumerate(current_lessons)}
        items = store(username).list_mistakes(selected['id'])
        for item in items:
            item['lesson'] = current_lessons.get(item['lesson_id'], item['lesson'])
        items.sort(key=lambda item: (order.get(item['lesson_id'], len(order)), item['lesson_id']))
        return jsonify({'items': items, 'count': len(items), 'word_count': sum(item['word_count'] for item in items)})

    @routes.post('/mistakes')
    @services.token_required
    def add_mistakes(username):
        data = body(('items',), maximum=MAX_MISTAKE_REQUEST_BYTES)
        items = validate_mistake_items(data['items'], max_text=MAX_TEXT, max_spoken=MAX_TEXT, max_mistakes=MAX_MISTAKES)
        submitted = sum(len(item['words']) for item in items)
        for item in items:
            item['lesson_id'] = canonical_lesson_id(item['book_id'], item['lesson_id'])
        items = validate_mistake_items(items, max_text=MAX_TEXT, max_spoken=MAX_TEXT, max_mistakes=MAX_MISTAKES)
        collapsed_aliases = submitted - sum(len(item['words']) for item in items)
        lessons_by_book = {}
        for item in items:
            selected = book(item['book_id'])
            if selected['id'] not in lessons_by_book:
                lessons_by_book[selected['id']] = {entry['id']: entry for entry in merged_lessons(username, selected)}
            entry = lessons_by_book[selected['id']].get(item['lesson_id'])
            if entry is None:
                raise DictationError('默写清单不存在，请重新选择。', 404)
            item['lesson'] = entry
        result = store(username).merge_mistakes(items, getattr(g, 'login_username', username))
        result['existing_count'] += collapsed_aliases
        return jsonify(result)

    @routes.delete('/mistakes/<book_id>/<lesson_id>')
    @services.token_required
    def remove_mistakes(username, book_id, lesson_id):
        selected = book(book_id); _id(lesson_id)
        lesson_id = canonical_lesson_id(book_id, lesson_id)
        current_lessons = {entry['id'] for entry in merged_lessons(username, selected)}
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
        from dictation_audio import audio_response, kokoro_audio
        experimental = kokoro_audio(text, 'en')
        if experimental:
            return audio_response(*experimental)
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
        response.headers['X-TTS-Engine'] = 'piper'
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

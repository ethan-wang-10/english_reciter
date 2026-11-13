"""Per-student Chinese dictation wordlists and family-maintained lesson folders.

Playback is a paper-writing aid, not an automatically graded exam. Both students
and their linked parents can prepare wordlists; account isolation is supplied by
the existing authentication decorator's effective student identity.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import re
import secrets
import sqlite3
import unicodedata
from urllib.parse import urlsplit

from flask import Blueprint, current_app, g, jsonify, request

from app_time import china_now_iso
from storage_utils import interprocess_file_lock


ROOT = Path(__file__).resolve().parent
IDENTIFIER = re.compile(r'[a-z0-9][a-z0-9_-]{0,95}')
MAX_REVISION = 9_007_199_254_740_991
MAX_REQUEST_BYTES = 256_000


class DictationError(ValueError):
    def __init__(self, message, status=400, **details):
        super().__init__(message)
        self.status = status
        self.details = details


def _id(value, message='教材或课文不存在。'):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise DictationError(message, 404)
    return value


def _text(value, maximum, *, empty=False):
    if not isinstance(value, str):
        raise DictationError('请填写有效的文字。')
    if any(unicodedata.category(character) in ('Cc', 'Cf', 'Cs') for character in value):
        raise DictationError('文字中不能包含控制字符或不可见字符。')
    result = ' '.join(unicodedata.normalize('NFC', value).split())
    if len(result) > maximum or not result and not empty:
        raise DictationError(f'文字不能为空，且不能超过{maximum}个字符。')
    return result


def _revision(value):
    if type(value) is not int or not 0 <= value <= MAX_REVISION:
        raise DictationError('词表版本无效，请重新打开课文后重试。')
    return value


def validate_words(value):
    """Normalize exact spellings while preserving first-entry order and aliases."""
    if not isinstance(value, list) or len(value) > 500:
        raise DictationError('请提交词语列表，一次最多输入500项。')
    result, seen, duplicates = [], set(), 0
    for item in value:
        if not isinstance(item, dict) or set(item) not in ({'text'}, {'text', 'spoken_text'}):
            raise DictationError('每个词语只接受文字和可选的朗读提示。')
        text = _text(item['text'], 40)
        if not any(unicodedata.category(character).startswith(('L', 'N')) for character in text):
            raise DictationError('词语中至少需要一个汉字、字母或数字。')
        spoken = _text(item.get('spoken_text', ''), 100, empty=True) or text
        if text in seen:
            duplicates += 1
            continue
        seen.add(text)
        result.append({'text': text, 'spoken_text': spoken})
    if len(result) > 200:
        raise DictationError('每篇课文最多保存200个不同的词语。')
    return result, duplicates


def _number(value):
    if value is not None and (type(value) is not int or not 1 <= value <= 999):
        raise DictationError('课文序号须为1到999的整数，暂不填写也可以。')
    return value


def _encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def load_books(path):
    """Load factual catalog metadata; wordlists never come from this shared file."""
    try:
        path = Path(path)
        if path.stat().st_size > 2_000_000:
            raise ValueError('Catalog is too large')
        raw = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(raw, dict) or type(raw.get('schema_version')) is not int
                or raw['schema_version'] != 1 or not isinstance(raw.get('books'), list)
                or len(raw['books']) > 100):
            raise ValueError('Invalid catalog root')
        books, seen = [], set()
        for item in raw['books']:
            if not isinstance(item, dict):
                raise ValueError('Invalid book')
            book_id = _id(item.get('id'))
            if book_id in seen:
                raise ValueError('Duplicate book id')
            seen.add(book_id)
            if type(item.get('grade')) is not int or not 1 <= item['grade'] <= 12:
                raise ValueError('Invalid grade')
            if type(item.get('term')) is not int or item['term'] not in (1, 2):
                raise ValueError('Invalid term')
            public = {'id': book_id, 'title': _text(item.get('title'), 200),
                      'grade': item['grade'], 'term': item['term'],
                      'edition': _text(item.get('edition'), 500), 'lessons': []}
            for name in ('description', 'source_note', 'catalog_note'):
                if name in item:
                    public[name] = _text(item[name], 2000, empty=True)
            lessons = item.get('lessons')
            if not isinstance(lessons, list) or len(lessons) > 500:
                raise ValueError('Invalid lessons')
            lesson_ids = set()
            for lesson in lessons:
                if not isinstance(lesson, dict):
                    raise ValueError('Invalid lesson')
                lesson_id = _id(lesson.get('id'))
                if lesson_id in lesson_ids or lesson_id.startswith('custom_'):
                    raise ValueError('Duplicate or reserved lesson id')
                lesson_ids.add(lesson_id)
                entry = {'id': lesson_id, 'title': _text(lesson.get('title'), 80),
                         'number': _number(lesson.get('number')), 'custom': False}
                if 'unit' in lesson:
                    entry['unit'] = _text(lesson['unit'], 100)
                public['lessons'].append(entry)
            if 'references' in item:
                if not isinstance(item['references'], list) or len(item['references']) > 30:
                    raise ValueError('Invalid catalog references')
                public['references'] = []
                for reference in item['references']:
                    if not isinstance(reference, dict):
                        raise ValueError('Invalid reference')
                    url = _text(reference.get('url'), 2000)
                    parsed = urlsplit(url)
                    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
                        raise ValueError('Invalid reference URL')
                    public['references'].append({'label': _text(reference.get('label'), 200), 'url': url})
            books.append(public)
        return books
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise DictationError('课本目录暂时无法读取，已保存的词表会保留，请稍后重试。', 503) from error


SCHEMA = '''
CREATE TABLE IF NOT EXISTS dictation_lessons (
    id TEXT PRIMARY KEY, book_id TEXT NOT NULL, title TEXT NOT NULL,
    number INTEGER, created_at TEXT NOT NULL, created_by TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS dictation_lessons_book ON dictation_lessons(book_id, number);
CREATE TABLE IF NOT EXISTS dictation_wordlists (
    book_id TEXT NOT NULL, lesson_id TEXT NOT NULL, words_json TEXT NOT NULL,
    revision INTEGER NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT NOT NULL,
    PRIMARY KEY(book_id, lesson_id)
);
'''


class DictationStore:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def _connection(self, *, write=False):
        connection = None
        if not write and not self.path.is_file():
            raise DictationError('词表不存在。', 404)
        try:
            if write:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                # The short schema lock also protects simultaneous first saves.
                with interprocess_file_lock(self.path.with_suffix('.schema.lock')):
                    connection = sqlite3.connect(self.path, timeout=20)
                    connection.execute('PRAGMA journal_mode=WAL')
                    connection.executescript(SCHEMA)
                connection.execute('BEGIN IMMEDIATE')
            else:
                connection = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=20)
            connection.row_factory = sqlite3.Row
            yield connection
            if write:
                connection.commit()
        except (OSError, sqlite3.Error) as error:
            if connection is not None and write:
                connection.rollback()
            raise DictationError('词表暂时无法保存或读取，原有内容已保留，请重试。', 503) from error
        except BaseException:
            if connection is not None and write:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _empty(book_id, lesson_id):
        return {'book_id': book_id, 'lesson_id': lesson_id, 'words': [], 'word_count': 0,
                'revision': 0, 'updated_at': None, 'updated_by': None}

    @staticmethod
    def _public_wordlist(row):
        try:
            raw = json.loads(row['words_json'])
            words, duplicates = validate_words(raw)
            if (duplicates or words != raw or type(row['revision']) is not int
                    or not 1 <= row['revision'] <= MAX_REVISION):
                raise ValueError('Invalid saved wordlist')
        except (ValueError, TypeError) as error:
            raise DictationError('已保存的词表暂时无法读取，原有内容已保留，请联系管理员。', 503) from error
        return {**{key: row[key] for key in ('book_id', 'lesson_id', 'revision', 'updated_at', 'updated_by')},
                'words': words, 'word_count': len(words)}

    @staticmethod
    def _public_lesson(row):
        try:
            if (not re.fullmatch(r'custom_[a-f0-9]{32}', row['id'])
                    or _id(row['book_id']) != row['book_id']
                    or _text(row['title'], 80) != row['title']
                    or _number(row['number']) != row['number']):
                raise ValueError('Invalid saved lesson')
        except (ValueError, TypeError) as error:
            raise DictationError('已保存的课文目录暂时无法读取，原有内容已保留。', 503) from error
        return {**{key: row[key] for key in ('id', 'book_id', 'number', 'title', 'created_at', 'created_by')}, 'custom': True}

    def list_wordlists(self, book_id):
        _id(book_id)
        if not self.path.is_file():
            return []
        with self._connection() as connection:
            rows = connection.execute('SELECT * FROM dictation_wordlists WHERE book_id=? ORDER BY lesson_id', (book_id,)).fetchall()
            return [self._public_wordlist(row) for row in rows]

    def get_wordlist(self, book_id, lesson_id):
        _id(book_id); _id(lesson_id)
        if not self.path.is_file():
            return self._empty(book_id, lesson_id)
        with self._connection() as connection:
            row = connection.execute('SELECT * FROM dictation_wordlists WHERE book_id=? AND lesson_id=?', (book_id, lesson_id)).fetchone()
            return self._public_wordlist(row) if row is not None else self._empty(book_id, lesson_id)

    def save_wordlist(self, book_id, lesson_id, base_revision, words, updated_by):
        _id(book_id); _id(lesson_id)
        _revision(base_revision)
        normalized, duplicates = validate_words(words)
        actor = _text(updated_by, 64)
        if not self.path.is_file() and base_revision != 0:
            raise DictationError('词表已有更新，请先保留本机内容再载入最新词表。', 409, code='conflict', current_revision=0)
        with self._connection(write=True) as connection:
            row = connection.execute('SELECT * FROM dictation_wordlists WHERE book_id=? AND lesson_id=?', (book_id, lesson_id)).fetchone()
            # Refuse to replace unreadable persisted content, including when a
            # still-open browser happens to carry the same revision number.
            current = self._public_wordlist(row)['revision'] if row is not None else 0
            if current != base_revision:
                raise DictationError('词表已有更新，请先保留本机内容再载入最新词表。', 409,
                                     code='conflict', current_revision=current)
            if current == MAX_REVISION:
                raise DictationError('词表版本已达到上限，原有内容已保留，请联系管理员。', 503)
            revision = current + 1
            connection.execute('INSERT INTO dictation_wordlists(book_id,lesson_id,words_json,revision,updated_at,updated_by) '
                               'VALUES(?,?,?,?,?,?) ON CONFLICT(book_id,lesson_id) DO UPDATE SET '
                               'words_json=excluded.words_json,revision=excluded.revision,updated_at=excluded.updated_at,updated_by=excluded.updated_by',
                               (book_id, lesson_id, _encode(normalized), revision, china_now_iso(timespec='microseconds'), actor))
            saved = connection.execute('SELECT * FROM dictation_wordlists WHERE book_id=? AND lesson_id=?', (book_id, lesson_id)).fetchone()
            return {'wordlist': self._public_wordlist(saved), 'duplicates_removed': duplicates}

    def list_custom_lessons(self, book_id):
        _id(book_id)
        if not self.path.is_file():
            return []
        with self._connection() as connection:
            rows = connection.execute('SELECT * FROM dictation_lessons WHERE book_id=? ORDER BY number IS NULL,number,created_at,id', (book_id,)).fetchall()
            return [self._public_lesson(row) for row in rows]

    def create_lesson(self, book_id, title, number, created_by):
        _id(book_id)
        title, number, actor = _text(title, 80), _number(number), _text(created_by, 64)
        with self._connection(write=True) as connection:
            existing = connection.execute('SELECT * FROM dictation_lessons WHERE book_id=? AND title=? AND number IS ?',
                                          (book_id, title, number)).fetchone()
            if existing is not None:
                return self._public_lesson(existing), False
            identifier = 'custom_' + secrets.token_hex(16)
            connection.execute('INSERT INTO dictation_lessons(id,book_id,title,number,created_at,created_by) VALUES(?,?,?,?,?,?)',
                               (identifier, book_id, title, number, china_now_iso(timespec='microseconds'), actor))
            row = connection.execute('SELECT * FROM dictation_lessons WHERE id=?', (identifier,)).fetchone()
            return self._public_lesson(row), True


def register_subject_dictation_routes(app, services):
    routes = Blueprint('subject_dictation', __name__, url_prefix='/api/subjects/chinese/dictation')

    @routes.errorhandler(DictationError)
    def invalid(error):
        return jsonify({'error': str(error), **error.details}), error.status

    @routes.after_request
    def private(response):
        response.cache_control.no_store = True
        return response

    def books():
        path = current_app.config.get('DICTATION_CATALOG_FILE', ROOT / 'subject_content/chinese/dictation/books.json')
        return load_books(path)

    def book(book_id):
        _id(book_id)
        result = next((item for item in books() if item['id'] == book_id), None)
        if result is None:
            raise DictationError('课本不存在，请重新选择。', 404)
        return result

    def store(username):
        return DictationStore(services.DATA_DIR / username / 'subjects/chinese/dictation.sqlite3')

    def merged_lessons(username, selected):
        items = [*deepcopy(selected['lessons']), *store(username).list_custom_lessons(selected['id'])]
        return sorted(items, key=lambda item: (item['number'] is None, item['number'] or 10000, item['custom'], item['id']))

    def lesson(username, selected, lesson_id):
        _id(lesson_id)
        found = next((item for item in merged_lessons(username, selected) if item['id'] == lesson_id), None)
        if found is None:
            raise DictationError('课文不存在，请重新选择。', 404)
        return found

    def body(required, optional=()):
        if request.content_length is not None and request.content_length > MAX_REQUEST_BYTES:
            raise DictationError('本次输入内容过长，请分课录入。')
        value = request.get_json(silent=True)
        if not isinstance(value, dict) or not set(required) <= set(value) or set(value) - set(required) - set(optional):
            raise DictationError('请提交有效的词表内容。')
        return value

    @routes.get('/books')
    @services.token_required
    def list_books(username):
        items = books()
        return jsonify({'items': items, 'count': len(items)})

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
        items = store(username).list_wordlists(selected['id'])
        return jsonify({'items': items, 'count': len(items)})

    @routes.get('/wordlists/<book_id>/<lesson_id>')
    @services.token_required
    def get_wordlist(username, book_id, lesson_id):
        selected = book(book_id)
        entry = lesson(username, selected, lesson_id)
        return jsonify({'wordlist': store(username).get_wordlist(book_id, lesson_id), 'lesson': entry})

    @routes.put('/wordlists/<book_id>/<lesson_id>')
    @services.token_required
    def save_wordlist(username, book_id, lesson_id):
        selected = book(book_id)
        lesson(username, selected, lesson_id)
        data = body(('base_revision', 'words'))
        return jsonify(store(username).save_wordlist(book_id, lesson_id, data['base_revision'], data['words'], getattr(g, 'login_username', username)))

    app.register_blueprint(routes)

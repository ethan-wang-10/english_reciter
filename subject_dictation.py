"""Per-student Chinese dictation wordlists and family-maintained lesson folders.

Playback is a paper-writing aid, not an automatically graded exam. Both students
and their linked parents can prepare wordlists; account isolation is supplied by
the existing authentication decorator's effective student identity.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
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
MAX_MISTAKE_REQUEST_BYTES = 1_500_000
MAX_MISTAKES_PER_LESSON = 1000


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


def _word(value):
    if not isinstance(value, dict) or set(value) not in ({'text'}, {'text', 'spoken_text'}):
        raise DictationError('每个词语只接受文字和可选的朗读提示。')
    text = _text(value['text'], 40)
    if not any(unicodedata.category(character).startswith(('L', 'N')) for character in text):
        raise DictationError('词语中至少需要一个汉字、字母或数字。')
    return {'text': text, 'spoken_text': _text(value.get('spoken_text', ''), 100, empty=True) or text}


def validate_words(value):
    """Normalize exact spellings while preserving first-entry order and aliases."""
    if not isinstance(value, list) or len(value) > 500:
        raise DictationError('请提交词语列表，一次最多输入500项。')
    result, seen, duplicates = [], set(), 0
    for item in value:
        word = _word(item)
        if word['text'] in seen:
            duplicates += 1
            continue
        seen.add(word['text'])
        result.append(word)
    if len(result) > 200:
        raise DictationError('每篇课文最多保存200个不同的词语。')
    return result, duplicates


def validate_mistake_items(value):
    """Bound one atomic merge while retaining lesson boundaries and exact text."""
    if not isinstance(value, list) or len(value) > 100:
        raise DictationError('一次最多加入100组课文错词。')
    groups, total = {}, 0
    for item in value:
        if not isinstance(item, dict) or set(item) != {'book_id', 'lesson_id', 'words'}:
            raise DictationError('每组错词只接受课本、课文和词语列表。')
        book_id, lesson_id = _id(item['book_id']), _id(item['lesson_id'])
        words = item['words']
        if not isinstance(words, list) or len(words) > 1000:
            raise DictationError('每组一次最多加入1000个错词。')
        total += len(words)
        if total > 2000:
            raise DictationError('一次最多加入2000个错词，请分次加入。')
        group = groups.setdefault((book_id, lesson_id), {})
        for entry in words:
            word = _word(entry)
            # The most recently supplied hint is the one just checked on paper.
            group[word['text']] = word
        if len(group) > 1000:
            raise DictationError('同一课文最多保存1000个不同的错词。')
    return [{'book_id': book_id, 'lesson_id': lesson_id, 'words': list(words.values())}
            for (book_id, lesson_id), words in groups.items()]


def validate_mistake_deletions(value):
    if not isinstance(value, list) or len(value) > 1000:
        raise DictationError('一次最多移除1000个错词。')
    return list(dict.fromkeys(_word({'text': text})['text'] for text in value))


def _lesson_snapshot(value, book_id, lesson_id):
    allowed = {'id', 'title', 'number', 'custom', 'unit', 'book_id', 'created_at', 'created_by'}
    if (not isinstance(value, dict) or set(value) - allowed
            or not {'id', 'title', 'number', 'custom'} <= set(value)
            or value['id'] != lesson_id or type(value['custom']) is not bool):
        raise ValueError('Invalid mistake lesson snapshot')
    _id(value['id']); _number(value['number'])
    if _text(value['title'], 80) != value['title']:
        raise ValueError('Invalid mistake lesson title')
    if value['custom'] != bool(re.fullmatch(r'custom_[a-f0-9]{32}', lesson_id)):
        raise ValueError('Invalid mistake lesson identity')
    for key, maximum in (('unit', 100), ('created_at', 100), ('created_by', 64)):
        if key in value and _text(value[key], maximum) != value[key]:
            raise ValueError('Invalid mistake lesson metadata')
    if 'book_id' in value and value['book_id'] != book_id:
        raise ValueError('Invalid mistake lesson book')
    return deepcopy(value)


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
CREATE TABLE IF NOT EXISTS dictation_mistake_groups (
    book_id TEXT NOT NULL, lesson_id TEXT NOT NULL, lesson_json TEXT NOT NULL,
    PRIMARY KEY(book_id, lesson_id)
);
CREATE TABLE IF NOT EXISTS dictation_mistakes (
    book_id TEXT NOT NULL, lesson_id TEXT NOT NULL, text TEXT NOT NULL,
    spoken_text TEXT NOT NULL, added_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL, PRIMARY KEY(book_id, lesson_id, text)
);
'''

TABLE_COLUMNS = {
    'dictation_lessons': {'id', 'book_id', 'title', 'number', 'created_at', 'created_by'},
    'dictation_wordlists': {'book_id', 'lesson_id', 'words_json', 'revision', 'updated_at', 'updated_by'},
    'dictation_mistake_groups': {'book_id', 'lesson_id', 'lesson_json'},
    'dictation_mistakes': {'book_id', 'lesson_id', 'text', 'spoken_text', 'added_at', 'updated_at', 'updated_by'},
}


def _schema(connection):
    """Recognize an intact old schema without repairing a partially lost one."""
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    required = {'dictation_lessons', 'dictation_wordlists'}
    optional = {'dictation_mistake_groups', 'dictation_mistakes'}
    if not required <= tables or tables & optional not in (set(), optional):
        raise DictationError('已保存的默写数据结构异常，原有内容已保留，请联系管理员。', 503)
    for name in required | (tables & optional):
        columns = {row[1] for row in connection.execute('PRAGMA table_info(' + name + ')')}
        if not TABLE_COLUMNS[name] <= columns:
            raise DictationError('已保存的默写数据结构异常，原有内容已保留，请联系管理员。', 503)
    return optional <= tables


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
                    existed = self.path.is_file()
                    connection = sqlite3.connect(self.path, timeout=20)
                    if existed:
                        _schema(connection)
                    connection.execute('PRAGMA journal_mode=WAL')
                    # Readers see either the complete legacy schema or both new
                    # tables. A failed migration leaves neither table behind.
                    connection.executescript('BEGIN IMMEDIATE;\n' + SCHEMA + '\nCOMMIT;')
                connection.execute('BEGIN IMMEDIATE')
            else:
                # Another worker may already have created the SQLite file while
                # its first schema transaction is still uncommitted. Wait only
                # for initialization; business reads do not hold this lock.
                with interprocess_file_lock(self.path.with_suffix('.schema.lock')):
                    connection = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=20)
                    _schema(connection)
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

    @staticmethod
    def _mistake_groups(connection, book_id):
        if not _schema(connection):
            return []
        try:
            groups = {}
            rows = connection.execute('SELECT * FROM dictation_mistake_groups WHERE book_id=? ORDER BY lesson_id', (book_id,)).fetchall()
            for row in rows:
                _id(row['book_id']); _id(row['lesson_id'])
                snapshot = _lesson_snapshot(json.loads(row['lesson_json']), row['book_id'], row['lesson_id'])
                groups[row['lesson_id']] = {'book_id': row['book_id'], 'lesson_id': row['lesson_id'],
                                            'lesson': snapshot, 'words': [], 'word_count': 0}
            rows = connection.execute('SELECT * FROM dictation_mistakes WHERE book_id=? ORDER BY lesson_id,added_at,text', (book_id,)).fetchall()
            for row in rows:
                word = _word({'text': row['text'], 'spoken_text': row['spoken_text']})
                if (row['lesson_id'] not in groups or word['text'] != row['text']
                        or word['spoken_text'] != row['spoken_text']
                        or _text(row['updated_by'], 64) != row['updated_by']):
                    raise ValueError('Invalid mistake word snapshot')
                for key in ('added_at', 'updated_at'):
                    if _text(row[key], 100) != row[key]:
                        raise ValueError('Invalid mistake timestamp')
                    datetime.fromisoformat(row[key])
                group = groups[row['lesson_id']]
                group['words'].append({**word, **{key: row[key] for key in ('added_at', 'updated_at', 'updated_by')}})
                group['word_count'] += 1
            for group in groups.values():
                if (not 1 <= group['word_count'] <= MAX_MISTAKES_PER_LESSON
                        or len({word['text'] for word in group['words']}) != group['word_count']):
                    raise ValueError('Invalid mistake group size')
            return list(groups.values())
        except (ValueError, TypeError, KeyError) as error:
            raise DictationError('已保存的错词本暂时无法读取，原有内容已保留，请联系管理员。', 503) from error

    def list_mistakes(self, book_id):
        _id(book_id)
        if not self.path.is_file():
            return []
        with self._connection() as connection:
            return self._mistake_groups(connection, book_id)

    def merge_mistakes(self, items, updated_by):
        """Merge resolved lesson snapshots atomically; never alter source wordlists."""
        if not isinstance(items, list) or any(not isinstance(item, dict) or set(item) != {
                'book_id', 'lesson_id', 'lesson', 'words'} for item in items):
            raise DictationError('请提交有效的课文错词。')
        normalized = validate_mistake_items([{key: item[key] for key in ('book_id', 'lesson_id', 'words')} for item in items])
        snapshots = {}
        for item in items:
            try:
                snapshots[item['book_id'], item['lesson_id']] = _lesson_snapshot(item['lesson'], item['book_id'], item['lesson_id'])
            except (ValueError, TypeError, KeyError) as error:
                raise DictationError('课文信息无效，请重新打开课文后重试。') from error
        normalized = [item for item in normalized if item['words']]
        actor = _text(updated_by, 64)
        if not normalized:
            return {'added_count': 0, 'existing_count': 0}
        with self._connection(write=True) as connection:
            saved = {}
            for book_id in {item['book_id'] for item in normalized}:
                saved.update({(book_id, group['lesson_id']): group for group in self._mistake_groups(connection, book_id)})
            # Check every group's resulting size before inserting any one group.
            for item in normalized:
                group = saved.get((item['book_id'], item['lesson_id']))
                old_words = {word['text'] for word in group['words']} if group else set()
                if len(old_words | {word['text'] for word in item['words']}) > MAX_MISTAKES_PER_LESSON:
                    raise DictationError('这篇课文的错词已达到1000个，请先移除掌握的词语再加入。')
            now, added, existing = china_now_iso(timespec='microseconds'), 0, 0
            for item in normalized:
                key = item['book_id'], item['lesson_id']
                group = saved.get(key)
                old_words = {word['text'] for word in group['words']} if group else set()
                if group is None:
                    connection.execute('INSERT INTO dictation_mistake_groups(book_id,lesson_id,lesson_json) VALUES(?,?,?)',
                                       (*key, _encode(snapshots[key])))
                for word in item['words']:
                    if word['text'] in old_words:
                        existing += 1
                    else:
                        added += 1
                    connection.execute('INSERT INTO dictation_mistakes(book_id,lesson_id,text,spoken_text,added_at,updated_at,updated_by) '
                                       'VALUES(?,?,?,?,?,?,?) ON CONFLICT(book_id,lesson_id,text) DO UPDATE SET '
                                       'spoken_text=excluded.spoken_text,updated_at=excluded.updated_at,updated_by=excluded.updated_by',
                                       (*key, word['text'], word['spoken_text'], now, now, actor))
            return {'added_count': added, 'existing_count': existing}

    def remove_mistakes(self, book_id, lesson_id, words):
        _id(book_id); _id(lesson_id)
        texts = validate_mistake_deletions(words)
        if not texts or not self.path.is_file():
            return {'removed_count': 0}
        # A delete against an intact legacy database is also a read-only no-op.
        if not any(group['lesson_id'] == lesson_id for group in self.list_mistakes(book_id)):
            return {'removed_count': 0}
        with self._connection(write=True) as connection:
            groups = {group['lesson_id']: group for group in self._mistake_groups(connection, book_id)}
            group = groups.get(lesson_id)
            selected = set(texts)
            count = sum(word['text'] in selected for word in group['words']) if group else 0
            connection.executemany('DELETE FROM dictation_mistakes WHERE book_id=? AND lesson_id=? AND text=?',
                                   [(book_id, lesson_id, text) for text in texts])
            if not connection.execute('SELECT 1 FROM dictation_mistakes WHERE book_id=? AND lesson_id=? LIMIT 1', (book_id, lesson_id)).fetchone():
                connection.execute('DELETE FROM dictation_mistake_groups WHERE book_id=? AND lesson_id=?', (book_id, lesson_id))
            return {'removed_count': count}


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

    def body(required, optional=(), *, maximum=MAX_REQUEST_BYTES):
        if request.content_length is not None and request.content_length > maximum:
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

    @routes.get('/mistakes')
    @services.token_required
    def list_mistakes(username):
        selected = book(request.args.get('book_id'))
        current_lessons = {entry['id']: entry for entry in merged_lessons(username, selected)}
        items = store(username).list_mistakes(selected['id'])
        for item in items:
            item['lesson'] = current_lessons.get(item['lesson_id'], item['lesson'])
        items.sort(key=lambda item: (item['lesson']['number'] is None, item['lesson']['number'] or 10000,
                                     item['lesson']['custom'], item['lesson_id']))
        return jsonify({'items': items, 'count': len(items), 'word_count': sum(item['word_count'] for item in items)})

    @routes.post('/mistakes')
    @services.token_required
    def add_mistakes(username):
        data = body(('items',), maximum=MAX_MISTAKE_REQUEST_BYTES)
        items = validate_mistake_items(data['items'])
        selected_books, lessons_by_book = {}, {}
        for item in items:
            book_id = item['book_id']
            if book_id not in selected_books:
                selected_books[book_id] = book(book_id)
                lessons_by_book[book_id] = {entry['id']: entry for entry in merged_lessons(username, selected_books[book_id])}
            entry = lessons_by_book[book_id].get(item['lesson_id'])
            if entry is None:
                raise DictationError('课文不存在，请重新选择。', 404)
            item['lesson'] = entry
        return jsonify(store(username).merge_mistakes(items, getattr(g, 'login_username', username)))

    @routes.delete('/mistakes/<book_id>/<lesson_id>')
    @services.token_required
    def remove_mistakes(username, book_id, lesson_id):
        selected = book(book_id)
        _id(lesson_id)
        current_lessons = {entry['id'] for entry in merged_lessons(username, selected)}
        if (lesson_id not in current_lessons
                and not any(group['lesson_id'] == lesson_id for group in store(username).list_mistakes(book_id))):
            raise DictationError('课文不存在，请重新选择。', 404)
        data = body(('words',))
        return jsonify(store(username).remove_mistakes(book_id, lesson_id, data['words']))

    app.register_blueprint(routes)

"""Guided writing drafts and immutable milestones, scoped to each student."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import re
import secrets
import sqlite3

from flask import Blueprint, current_app, g, jsonify, request

from app_time import china_now_iso


ROOT = Path(__file__).resolve().parent
LESSON_ID = re.compile(r'[a-z0-9][a-z0-9_-]{0,95}')
ATTEMPT_ID = re.compile(r'writing_[a-f0-9]{32}')
VERSION_ID = re.compile(r'version_[a-f0-9]{32}')
PARAGRAPH_ID = re.compile(r'[A-Za-z0-9_-]{1,64}')
MAX_DATA_BYTES = 100_000
DATA_FIELDS = frozenset((
    'stage', 'mode', 'planning', 'outline', 'paragraphs', 'structure_paragraphs',
    'final_paragraphs', 'structure_started', 'language_started', 'review_round',
    'checks', 'revision_notes', 'reflection', 'no_change',
))


class WritingError(ValueError):
    def __init__(self, message, status=400, **details):
        super().__init__(message)
        self.status = status
        self.details = details


def _object(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise WritingError('作文草稿格式无效，请刷新页面后重试。')
    return value


def _string(value, maximum=2000):
    if not isinstance(value, str) or len(value) > maximum:
        raise WritingError('文字格式无效或超过长度限制。')
    return value


def _valid_id(value, pattern, message='练习不存在。'):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise WritingError(message, 404)
    return value


def initial_data():
    return {
        'stage': 0, 'mode': 'material',
        'planning': {'situation': '', 'main_idea': '', 'materials': '', 'ending': ''},
        'outline': [{'id': f'p{index}', 'content': '', 'purpose': '', 'focus': False} for index in range(1, 4)],
        'paragraphs': {}, 'structure_paragraphs': {}, 'final_paragraphs': {},
        'structure_started': False, 'language_started': False, 'review_round': 'structure',
        'checks': {'structure': [], 'language': []},
        'revision_notes': {'structure': '', 'language': ''},
        'reflection': {'learned': '', 'next_time': ''},
        'no_change': {'structure': False, 'language': False},
    }


def validate_data(value):
    """Validate storage shape and bounds; do not judge a student's writing."""
    _object(value, DATA_FIELDS)
    if type(value['stage']) is not int or not 0 <= value['stage'] <= 5:
        raise WritingError('练习步骤无效。')
    if value['mode'] not in ('material', 'personal') or value['review_round'] not in ('structure', 'language'):
        raise WritingError('练习方式无效。')
    for key in ('structure_started', 'language_started'):
        if type(value[key]) is not bool:
            raise WritingError('修改状态格式无效。')
    for key, fields in (
        ('planning', ('situation', 'main_idea', 'materials', 'ending')),
        ('revision_notes', ('structure', 'language')),
        ('reflection', ('learned', 'next_time')),
    ):
        _object(value[key], fields)
        for text in value[key].values():
            _string(text)
    outline = value['outline']
    if not isinstance(outline, list) or len(outline) > 8:
        raise WritingError('提纲最多可安排8段。')
    seen = set()
    for card in outline:
        _object(card, ('id', 'content', 'purpose', 'focus'))
        identifier = card['id']
        if not isinstance(identifier, str) or not PARAGRAPH_ID.fullmatch(identifier) or identifier in seen:
            raise WritingError('提纲段落编号无效或重复。')
        seen.add(identifier)
        _string(card['content'])
        _string(card['purpose'])
        if type(card['focus']) is not bool:
            raise WritingError('重点段标记格式无效。')
    for key in ('paragraphs', 'structure_paragraphs', 'final_paragraphs'):
        paragraphs = value[key]
        if not isinstance(paragraphs, dict) or len(paragraphs) > 32:
            raise WritingError('正文段落格式无效。')
        for identifier, text in paragraphs.items():
            if not isinstance(identifier, str) or not PARAGRAPH_ID.fullmatch(identifier):
                raise WritingError('正文段落编号无效。')
            _string(text, 6000)
    _object(value['checks'], ('structure', 'language'))
    for keys in value['checks'].values():
        if (not isinstance(keys, list) or len(keys) > 32
                or any(not isinstance(key, str) or not key.strip() or len(key) > 80 for key in keys)
                or len(set(keys)) != len(keys)):
            raise WritingError('自查项目格式无效。')
    _object(value['no_change'], ('structure', 'language'))
    if any(type(flag) is not bool for flag in value['no_change'].values()):
        raise WritingError('修改确认格式无效。')
    if len(_encode(value).encode('utf-8')) > MAX_DATA_BYTES:
        raise WritingError('这份草稿内容过长，请缩短后再保存。')
    return deepcopy(value)


def _encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True)


def _decode(text):
    try:
        return json.loads(text)
    except (ValueError, TypeError) as error:
        raise WritingError('作文记录暂时无法读取，原稿已保留。', 503) from error


def _revision(value):
    if type(value) is not int or not 1 <= value <= 9_007_199_254_740_991:
        raise WritingError('草稿版本无效，请重新进入练习。')
    return value


def validate_lesson(lesson):
    try:
        if (not isinstance(lesson, dict) or not isinstance(lesson.get('id'), str)
                or not LESSON_ID.fullmatch(lesson['id']) or type(lesson.get('version')) is not int
                or lesson['version'] < 1 or not isinstance(lesson.get('title'), str)
                or not lesson['title'].strip() or len(lesson['title']) > 200
                or type(lesson.get('grade')) is not int or not 1 <= lesson['grade'] <= 12
                or not isinstance(lesson.get('stages'), list) or len(lesson['stages']) != 6):
            raise ValueError('Invalid lesson metadata')
        ids = set()
        for stage in lesson['stages']:
            if not isinstance(stage, dict) or not isinstance(stage.get('id'), str) or stage['id'] in ids:
                raise ValueError('Invalid stage')
            ids.add(stage['id'])
            for field in ('title', 'goal', 'question'):
                if not isinstance(stage.get(field), str) or not stage[field].strip():
                    raise ValueError('Missing stage guidance')
            if not isinstance(stage.get('hints'), list) or not isinstance(stage.get('checklist'), list):
                raise ValueError('Invalid stage guidance')
        if len(_encode(lesson).encode('utf-8')) > 200_000:
            raise ValueError('Lesson too large')
    except (ValueError, TypeError) as error:
        raise WritingError('作文练习内容暂不可用，请稍后再试。', 503) from error
    return deepcopy(lesson)


def load_lessons(directory):
    lessons, seen = [], set()
    try:
        for path in sorted(Path(directory).glob('*.json')):
            lesson = validate_lesson(json.loads(path.read_text(encoding='utf-8')))
            if lesson['id'] in seen:
                raise WritingError('作文练习编号重复，请稍后再试。', 503)
            seen.add(lesson['id'])
            lessons.append(lesson)
    except (OSError, ValueError, TypeError) as error:
        if isinstance(error, WritingError):
            raise
        raise WritingError('作文练习内容暂不可用，请稍后再试。', 503) from error
    return lessons


def lesson_summary(lesson):
    return {key: deepcopy(lesson[key]) for key in ('id', 'version', 'title', 'grade', 'description', 'goal', 'time_hint') if key in lesson}


def _completion_missing(data):
    missing = []
    for field in ('situation', 'main_idea'):
        if not data['planning'][field].strip():
            missing.append('planning.' + field)
    if not any(card['content'].strip() and card['focus'] for card in data['outline']):
        missing.append('outline.focus')
    active = {card['id'] for card in data['outline']}
    for field in ('paragraphs', 'final_paragraphs'):
        if not any(text.strip() for key, text in data[field].items() if key in active):
            missing.append(field)
    for field in ('structure', 'language'):
        if not data['revision_notes'][field].strip() and not data['no_change'][field]:
            missing.append('revision_notes.' + field)
    if not data['reflection']['learned'].strip():
        missing.append('reflection.learned')
    return missing


SCHEMA = '''
CREATE TABLE IF NOT EXISTS writing_attempts (
    id TEXT PRIMARY KEY, lesson_id TEXT NOT NULL, lesson_json TEXT NOT NULL,
    data_json TEXT NOT NULL, revision INTEGER NOT NULL, status TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT
);
CREATE TABLE IF NOT EXISTS writing_versions (
    id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL REFERENCES writing_attempts(id),
    label TEXT NOT NULL, data_json TEXT NOT NULL, revision INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS writing_updated ON writing_attempts(updated_at DESC);
CREATE INDEX IF NOT EXISTS writing_versions_attempt ON writing_versions(attempt_id, revision DESC);
'''


class WritingStore:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def _connection(self, *, write=False, create=False):
        connection = None
        if not self.path.is_file() and not create:
            raise WritingError('练习不存在。', 404)
        try:
            if create:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            if write:
                connection = sqlite3.connect(self.path, timeout=20)
                connection.execute('PRAGMA journal_mode=WAL')
            else:
                connection = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=20)
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA foreign_keys=ON')
            if create:
                connection.executescript(SCHEMA)
            if write:
                connection.execute('BEGIN IMMEDIATE')
            yield connection
            if write:
                connection.commit()
        except sqlite3.Error as error:
            if connection is not None and write:
                connection.rollback()
            raise WritingError('作文记录暂时无法保存或读取，原稿已保留，请重试。', 503) from error
        except BaseException:
            if connection is not None and write:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()

    def _row(self, connection, attempt_id):
        _valid_id(attempt_id, ATTEMPT_ID)
        row = connection.execute('SELECT * FROM writing_attempts WHERE id=?', (attempt_id,)).fetchone()
        if row is None:
            raise WritingError('练习不存在。', 404)
        return row

    def _public(self, row):
        return {**{key: row[key] for key in ('id', 'lesson_id', 'revision', 'status', 'created_at', 'updated_at', 'completed_at')},
                'lesson_snapshot': _decode(row['lesson_json']), 'data': _decode(row['data_json'])}

    def _versions(self, connection, attempt_id):
        return [dict(row) for row in connection.execute(
            'SELECT id,label,created_at,revision FROM writing_versions WHERE attempt_id=? ORDER BY revision DESC,rowid DESC',
            (attempt_id,),
        ).fetchall()]

    def _snapshot(self, connection, attempt_id, data, revision, label, now):
        version_id = 'version_' + secrets.token_hex(16)
        connection.execute('INSERT INTO writing_versions(id,attempt_id,label,data_json,revision,created_at) VALUES(?,?,?,?,?,?)',
                           (version_id, attempt_id, label, _encode(data), revision, now))
        return version_id

    def _check_write(self, row, base_revision):
        if row['status'] != 'in_progress':
            raise WritingError('这次练习已完成，可以另开一次练习。', 409, code='completed', current_revision=row['revision'])
        if _revision(base_revision) != row['revision']:
            raise WritingError('另一页面已有更新。本机这份内容仍可保留，请先查看最新稿。', 409,
                               code='conflict', current_revision=row['revision'])

    def start(self, lesson):
        lesson = validate_lesson(lesson)
        data = initial_data()
        now, attempt_id = china_now_iso(timespec='microseconds'), 'writing_' + secrets.token_hex(16)
        with self._connection(write=True, create=True) as connection:
            connection.execute('INSERT INTO writing_attempts(id,lesson_id,lesson_json,data_json,revision,status,created_at,updated_at) '
                               'VALUES(?,?,?,?,?,?,?,?)',
                               (attempt_id, lesson['id'], _encode(lesson), _encode(data), 1, 'in_progress', now, now))
            self._snapshot(connection, attempt_id, data, 1, '开始练习', now)
            return self._public(self._row(connection, attempt_id))

    def list_attempts(self):
        if not self.path.is_file():
            return []
        with self._connection() as connection:
            rows = connection.execute('SELECT * FROM writing_attempts ORDER BY updated_at DESC,id DESC LIMIT 200').fetchall()
            result = []
            for row in rows:
                lesson, data = _decode(row['lesson_json']), _decode(row['data_json'])
                result.append({**{key: row[key] for key in ('id', 'lesson_id', 'revision', 'status', 'created_at', 'updated_at', 'completed_at')},
                               'title': lesson['title'], 'lesson_title': lesson['title'], 'stage': data['stage']})
            return result

    def get(self, attempt_id):
        _valid_id(attempt_id, ATTEMPT_ID)
        with self._connection() as connection:
            row = self._row(connection, attempt_id)
            return {'attempt': self._public(row), 'versions': self._versions(connection, attempt_id)}

    def save(self, attempt_id, base_revision, data, *, label=None, complete=False):
        _valid_id(attempt_id, ATTEMPT_ID)
        with self._connection(write=True) as connection:
            row = self._row(connection, attempt_id)
            self._check_write(row, base_revision)
            data = validate_data(data)
            if label is not None and (not isinstance(label, str) or not label.strip() or len(label) > 80):
                raise WritingError('保存版本的名称无效。')
            if complete:
                missing = _completion_missing(data)
                if missing:
                    raise WritingError('还有几项内容没有准备好，请检查后再完成。', 400, code='incomplete', missing=missing)
            now, revision = china_now_iso(timespec='microseconds'), row['revision'] + 1
            connection.execute('UPDATE writing_attempts SET data_json=?,revision=?,status=?,updated_at=?,completed_at=? WHERE id=?',
                               (_encode(data), revision, 'completed' if complete else 'in_progress', now,
                                now if complete else None, attempt_id))
            if complete or label is not None:
                self._snapshot(connection, attempt_id, data, revision, '完成稿' if complete else label.strip(), now)
            result = {'attempt': self._public(self._row(connection, attempt_id))}
            if complete or label is not None:
                result['versions'] = self._versions(connection, attempt_id)
            return result

    def get_version(self, attempt_id, version_id):
        _valid_id(attempt_id, ATTEMPT_ID)
        _valid_id(version_id, VERSION_ID, '版本不存在。')
        with self._connection() as connection:
            self._row(connection, attempt_id)
            row = connection.execute('SELECT * FROM writing_versions WHERE id=? AND attempt_id=?', (version_id, attempt_id)).fetchone()
            if row is None:
                raise WritingError('版本不存在。', 404)
            return {**{key: row[key] for key in ('id', 'attempt_id', 'label', 'revision', 'created_at')},
                    'data': _decode(row['data_json'])}

    def restore(self, attempt_id, base_revision, version_id):
        _valid_id(attempt_id, ATTEMPT_ID)
        _valid_id(version_id, VERSION_ID, '版本不存在。')
        with self._connection(write=True) as connection:
            row = self._row(connection, attempt_id)
            self._check_write(row, base_revision)
            version = connection.execute('SELECT * FROM writing_versions WHERE id=? AND attempt_id=?', (version_id, attempt_id)).fetchone()
            if version is None:
                raise WritingError('版本不存在。', 404)
            now, revision = china_now_iso(timespec='microseconds'), row['revision'] + 1
            previous, restored = _decode(row['data_json']), validate_data(_decode(version['data_json']))
            self._snapshot(connection, attempt_id, previous, row['revision'], '恢复前的草稿', now)
            connection.execute('UPDATE writing_attempts SET data_json=?,revision=?,updated_at=? WHERE id=?',
                               (_encode(restored), revision, now, attempt_id))
            self._snapshot(connection, attempt_id, restored, revision, ('恢复版本：' + version['label'])[:80], now)
            return {'attempt': self._public(self._row(connection, attempt_id)), 'versions': self._versions(connection, attempt_id)}

    def records(self):
        if not self.path.is_file():
            return []
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM writing_attempts WHERE status='completed' ORDER BY completed_at DESC,id DESC LIMIT 200").fetchall()
            return [{'id': row['id'], 'kind': 'writing', 'paper_id': row['lesson_id'],
                     'paper_title': '作文专项：' + _decode(row['lesson_json'])['title'],
                     'submitted_at': row['completed_at'], 'status': 'completed'} for row in rows]


def register_subject_writing_routes(app, services):
    routes = Blueprint('subject_writing', __name__, url_prefix='/api/subjects/chinese/writing')

    @routes.errorhandler(WritingError)
    def invalid(error):
        return jsonify({'error': str(error), **error.details}), error.status

    @routes.after_request
    def private(response):
        response.cache_control.no_store = True
        return response

    def lessons():
        directory = current_app.config.get('WRITING_LESSON_DIR', ROOT / 'subject_content/chinese/writing')
        return load_lessons(Path(directory))

    def store(username):
        return WritingStore(services.DATA_DIR / username / 'subjects/chinese/writing.sqlite3')

    def body(fields):
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or set(data) != set(fields):
            raise WritingError('请提交有效的作文练习内容。')
        return data

    @routes.get('/lessons')
    @services.token_required
    def list_lessons(username):
        items = [lesson_summary(lesson) for lesson in lessons()]
        return jsonify({'items': items, 'count': len(items)})

    @routes.get('/lessons/<lesson_id>')
    @services.token_required
    def get_lesson(username, lesson_id):
        _valid_id(lesson_id, LESSON_ID, '练习题目不存在。')
        lesson = next((item for item in lessons() if item['id'] == lesson_id), None)
        if lesson is None:
            raise WritingError('练习题目不存在。', 404)
        return jsonify({'lesson': lesson})

    @routes.get('/attempts')
    @services.token_required
    def list_attempts(username):
        items = store(username).list_attempts()
        return jsonify({'items': items, 'count': len(items), 'read_only': bool(getattr(g, 'is_parent', False))})

    @routes.post('/attempts')
    @services.token_required
    @services.parent_forbidden
    def start(username):
        data = body(('lesson_id',))
        _valid_id(data['lesson_id'], LESSON_ID, '练习题目不存在。')
        lesson = next((item for item in lessons() if item['id'] == data['lesson_id']), None)
        if lesson is None:
            raise WritingError('练习题目不存在。', 404)
        return jsonify({'attempt': store(username).start(lesson), 'read_only': False}), 201

    @routes.get('/attempts/<attempt_id>')
    @services.token_required
    def get(username, attempt_id):
        return jsonify({**store(username).get(attempt_id), 'read_only': bool(getattr(g, 'is_parent', False))})

    @routes.patch('/attempts/<attempt_id>')
    @services.token_required
    @services.parent_forbidden
    def save(username, attempt_id):
        data = body(('base_revision', 'data'))
        return jsonify(store(username).save(attempt_id, data['base_revision'], data['data']))

    @routes.post('/attempts/<attempt_id>/checkpoints')
    @services.token_required
    @services.parent_forbidden
    def checkpoint(username, attempt_id):
        data = body(('base_revision', 'data', 'label'))
        if not isinstance(data['label'], str) or not data['label'].strip() or len(data['label']) > 80:
            raise WritingError('保存版本的名称无效。')
        return jsonify(store(username).save(attempt_id, data['base_revision'], data['data'], label=data['label']))

    @routes.post('/attempts/<attempt_id>/complete')
    @services.token_required
    @services.parent_forbidden
    def complete(username, attempt_id):
        data = body(('base_revision', 'data'))
        return jsonify(store(username).save(attempt_id, data['base_revision'], data['data'], complete=True))

    @routes.get('/attempts/<attempt_id>/versions/<version_id>')
    @services.token_required
    def get_version(username, attempt_id, version_id):
        return jsonify({'version': store(username).get_version(attempt_id, version_id)})

    @routes.post('/attempts/<attempt_id>/restore')
    @services.token_required
    @services.parent_forbidden
    def restore(username, attempt_id):
        data = body(('base_revision', 'version_id'))
        return jsonify(store(username).restore(attempt_id, data['base_revision'], data['version_id']))

    app.register_blueprint(routes)

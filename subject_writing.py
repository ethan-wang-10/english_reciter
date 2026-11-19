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
GUIDANCE_FIELDS = frozenset((
    'version', 'step', 'confirmed', 'review', 'theme', 'customTheme', 'structure',
    'slots', 'notes', 'details', 'ownDetail', 'sentence', 'paragraph', 'endingOkay',
))
GUIDANCE_PARTS = ('begin', 'middle', 'end')
COACHING_PARTS = ('source', 'activity', 'moment', 'feeling')
COACHING_COMPARISON_IDS = {'action', 'dialogue', 'reaction'}
GUIDANCE_IDS = {
    'theme': {'fun', 'mixup', 'together'},
    'structure': {'total', 'sequence', 'final'},
    'begin': {'theme', 'start', 'dialogue'},
    'middle': {'frog', 'again', 'laugh'},
    'end': {'bell', 'joy', 'echo'},
    'details': {'ears', 'frog', 'reaction'},
}


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


def initial_guidance():
    return {
        'version': 1, 'step': 0, 'confirmed': [], 'review': [], 'theme': '',
        'customTheme': '', 'structure': '',
        'slots': {part: [] for part in GUIDANCE_PARTS},
        'notes': {part: '' for part in GUIDANCE_PARTS},
        'details': [], 'ownDetail': '', 'sentence': '', 'paragraph': '', 'endingOkay': False,
    }


def initial_coaching():
    return {
        'version': 1,
        'material': {'step': 0, 'source': '', 'activity': '', 'moment': '', 'feeling': ''},
        'comparison': {'choices': [], 'reason': ''},
        'evidence': {part: {'before': '', 'after': '', 'reason': ''} for part in ('structure', 'language')},
    }


def initial_data(lesson=None):
    result = {
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
    if lesson and 'guidance' in lesson:
        result['guidance'] = initial_guidance()
        result['outline'] = [{'id': 'p_full', 'content': '', 'purpose': '整篇初稿（开头—重点—结尾）', 'focus': True}]
    if lesson and 'coaching' in lesson:
        result['coaching'] = initial_coaching()
    return result


def _selected(value, allowed, *, maximum=None):
    if (not isinstance(value, list) or len(value) > (len(allowed) if maximum is None else maximum)
            or any(not isinstance(item, str) or item not in allowed for item in value)
            or len(set(value)) != len(value)):
        raise WritingError('写作引导中的选项无效或重复，请保留本机内容并重新进入练习。')


def validate_guidance(value, configuration=None):
    _object(value, GUIDANCE_FIELDS)
    if type(value['version']) is not int or value['version'] != 1:
        raise WritingError('写作引导版本无效。')
    if type(value['step']) is not int or not 0 <= value['step'] <= 8:
        raise WritingError('写作引导步骤无效。')
    for field in ('confirmed', 'review'):
        steps = value[field]
        if (not isinstance(steps, list) or len(steps) > 9
                or any(type(step) is not int or not 0 <= step <= 8 for step in steps)
                or len(set(steps)) != len(steps)):
            raise WritingError('写作引导确认状态无效。')
    if not set(value['review']) <= set(value['confirmed']):
        raise WritingError('待复核步骤需要对应已确认的内容。')
    allowed = GUIDANCE_IDS
    if configuration is not None:
        allowed = {
            'theme': {item['id'] for item in configuration['themes']},
            'structure': {item['id'] for item in configuration['structures']},
            'details': {item['id'] for item in configuration['details']},
            **{part: {item['id'] for item in configuration['options'][part]} for part in GUIDANCE_PARTS},
        }
    for field in ('theme', 'structure'):
        if not isinstance(value[field], str) or value[field] not in {'', *allowed[field]}:
            raise WritingError('写作引导中的主旨或结构选项不存在。')
    _object(value['slots'], GUIDANCE_PARTS)
    _object(value['notes'], GUIDANCE_PARTS)
    for part in GUIDANCE_PARTS:
        _selected(value['slots'][part], allowed[part], maximum=1 if part == 'middle' else None)
        _string(value['notes'][part], 2000)
    _selected(value['details'], allowed['details'])
    for field in ('customTheme', 'ownDetail', 'sentence', 'paragraph'):
        # Configuration limits are teaching suggestions. Persisted text is never
        # sliced to those limits or silently normalized by a later client.
        _string(value[field], 6000 if field in ('sentence', 'paragraph') else 2000)
    if type(value['endingOkay']) is not bool:
        raise WritingError('结尾回看确认格式无效。')
    return deepcopy(value)


def validate_coaching(value, configuration=None):
    _object(value, ('version', 'material', 'comparison', 'evidence'))
    if type(value['version']) is not int or value['version'] != 1:
        raise WritingError('写作辅助版本无效。')
    material = _object(value['material'], ('step', *COACHING_PARTS))
    if type(material['step']) is not int or not 0 <= material['step'] <= 4:
        raise WritingError('素材回想步骤无效。')
    if not isinstance(material['source'], str) or material['source'] not in ('', 'personal', 'scenario'):
        raise WritingError('请选择自己的经历或给定情境。')
    for field in ('activity', 'moment', 'feeling'):
        _string(material[field])
    comparison = _object(value['comparison'], ('choices', 'reason'))
    allowed = ({item['id'] for item in configuration['comparison']['options']}
               if configuration is not None else COACHING_COMPARISON_IDS)
    _selected(comparison['choices'], allowed)
    _string(comparison['reason'])
    _object(value['evidence'], ('structure', 'language'))
    for evidence in value['evidence'].values():
        _object(evidence, ('before', 'after', 'reason'))
        _string(evidence['before'], 6000)
        _string(evidence['after'], 6000)
        _string(evidence['reason'])
    return deepcopy(value)


def validate_data(value, lesson=None):
    """Validate storage shape and bounds; do not judge a student's writing."""
    if not isinstance(value, dict) or set(value) not in (
            DATA_FIELDS, DATA_FIELDS | {'guidance'}, DATA_FIELDS | {'guidance', 'coaching'}):
        raise WritingError('作文草稿格式无效，请刷新页面后重试。')
    if 'guidance' in value:
        if lesson is not None and 'guidance' not in lesson:
            raise WritingError('这份旧版练习不能切换为新版引导，请另开一次练习。')
        validate_guidance(value['guidance'], lesson.get('guidance') if lesson else None)
    if 'coaching' in value:
        if lesson is not None and 'coaching' not in lesson:
            raise WritingError('这份旧版练习不能加入新版辅助，请另开一次练习。')
        validate_coaching(value['coaching'], lesson.get('coaching') if lesson else None)
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


def _guidance_configuration(value):
    _object(value, ('version', 'steps', 'themes', 'structures', 'options', 'details', 'examples', 'transfer', 'limits'))
    if type(value['version']) is not int or value['version'] != 1:
        raise ValueError('Invalid guidance configuration version')

    def text(value, maximum=2000):
        _string(value, maximum)
        if not value.strip() or '\x00' in value:
            raise ValueError('Empty or invalid teaching text')

    def choices(items, fields, maximum=20):
        if not isinstance(items, list) or not 1 <= len(items) <= maximum:
            raise ValueError('Invalid teaching choices')
        seen = set()
        for item in items:
            _object(item, fields)
            identifier = item['id']
            if (not isinstance(identifier, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', identifier)
                    or identifier in seen):
                raise ValueError('Invalid or duplicate teaching choice ID')
            seen.add(identifier)
        return seen

    if not isinstance(value['steps'], list) or len(value['steps']) != 9:
        raise ValueError('Guidance requires nine questions')
    for index, step in enumerate(value['steps']):
        _object(step, ('id', 'title', 'phase', 'question', 'description', 'confirm_label'))
        if type(step['id']) is not int or step['id'] != index:
            raise ValueError('Invalid guidance question order')
        for field in ('title', 'phase', 'question', 'description', 'confirm_label'):
            text(step[field])
    choices(value['themes'], ('id', 'text'))
    for item in value['themes']:
        text(item['text'])
    structure_ids = choices(value['structures'], ('id', 'name', 'short', 'roles'))
    for item in value['structures']:
        text(item['name']); text(item['short'])
        _object(item['roles'], GUIDANCE_PARTS)
        for role in item['roles'].values():
            text(role)
    _object(value['options'], GUIDANCE_PARTS)
    for part in GUIDANCE_PARTS:
        choices(value['options'][part], ('id', 'text'))
        for item in value['options'][part]:
            text(item['text'])
    choices(value['details'], ('id', 'text', 'kind'))
    for item in value['details']:
        text(item['text']); text(item['kind'])
    example_ids = choices(value['examples'], ('id', 'title', 'paragraphs'), 10)
    if not example_ids <= structure_ids:
        raise ValueError('Example refers to an unknown structure')
    for item in value['examples']:
        text(item['title'])
        if not isinstance(item['paragraphs'], list) or len(item['paragraphs']) != 3:
            raise ValueError('Example requires three commented sections')
        for paragraph in item['paragraphs']:
            _object(paragraph, ('role', 'text', 'hint'))
            text(paragraph['role']); text(paragraph['text'], 6000); text(paragraph['hint'])
    _object(value['transfer'], ('title', 'text', 'hint'))
    for field in value['transfer'].values():
        text(field)
    _object(value['limits'], ('customTheme', 'ownDetail', 'sentence', 'paragraph', 'draft', 'notes'))
    for key, limit in value['limits'].items():
        if type(limit) is not int or not 1 <= limit <= (6000 if key in ('sentence', 'paragraph', 'draft') else 2000):
            raise ValueError('Invalid suggested input limit')


def _coaching_configuration(value):
    _object(value, ('version', 'material', 'comparison', 'evidence'))
    if type(value['version']) is not int or value['version'] != 1:
        raise ValueError('Invalid coaching configuration version')

    def text(value, maximum=2000):
        _string(value, maximum)
        if not value.strip() or '\x00' in value:
            raise ValueError('Empty or invalid coaching text')

    material = _object(value['material'], ('title', 'steps', 'sources', 'suggestions'))
    text(material['title'])
    if not isinstance(material['steps'], list) or len(material['steps']) != 4:
        raise ValueError('Material recall requires four questions')
    for field, step in zip(COACHING_PARTS, material['steps']):
        _object(step, ('id', 'question', 'help'))
        if step['id'] != field:
            raise ValueError('Invalid material question order')
        text(step['question']); text(step['help'])
    if not isinstance(material['sources'], list) or len(material['sources']) != 2:
        raise ValueError('Material recall requires personal and scenario sources')
    sources = set()
    for source in material['sources']:
        _object(source, ('id', 'label', 'help'))
        if not isinstance(source['id'], str) or source['id'] not in ('personal', 'scenario') or source['id'] in sources:
            raise ValueError('Invalid material source')
        sources.add(source['id']); text(source['label']); text(source['help'])
    _object(material['suggestions'], ('personal', 'scenario'))
    for suggestions in material['suggestions'].values():
        _object(suggestions, ('activity', 'moment', 'feeling'))
        for items in suggestions.values():
            if not isinstance(items, list) or not 1 <= len(items) <= 12:
                raise ValueError('Invalid material suggestions')
            for suggestion in items:
                text(suggestion)
    comparison = _object(value['comparison'], ('title', 'question', 'description', 'plain', 'options', 'prompt'))
    for field in ('title', 'question', 'description', 'plain', 'prompt'):
        text(comparison[field])
    if not isinstance(comparison['options'], list) or not 2 <= len(comparison['options']) <= 10:
        raise ValueError('Detail comparison requires several options')
    identifiers = set()
    for option in comparison['options']:
        _object(option, ('id', 'text', 'detail', 'why'))
        identifier = option['id']
        if (not isinstance(identifier, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', identifier)
                or identifier in identifiers):
            raise ValueError('Invalid detail comparison ID')
        identifiers.add(identifier)
        for field in ('text', 'detail', 'why'):
            text(option[field])
    evidence = _object(value['evidence'], ('title', 'description', 'rounds'))
    text(evidence['title']); text(evidence['description'])
    _object(evidence['rounds'], ('structure', 'language'))
    for round in evidence['rounds'].values():
        _object(round, ('question', 'help'))
        text(round['question']); text(round['help'])


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
        if 'guidance' in lesson:
            if lesson['version'] < 2:
                raise ValueError('Nine-question guidance requires a new lesson version')
            _guidance_configuration(lesson['guidance'])
        if 'coaching' in lesson:
            if lesson['version'] < 3 or 'guidance' not in lesson:
                raise ValueError('Coaching requires a version three guided lesson')
            _coaching_configuration(lesson['coaching'])
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
    summary = {key: deepcopy(lesson[key]) for key in ('id', 'version', 'title', 'grade', 'description', 'goal', 'time_hint') if key in lesson}
    if 'guidance' in lesson:
        summary['flow_version'] = 2 if 'coaching' in lesson else lesson['guidance']['version']
    return summary


def _completion_missing(data):
    missing = []
    if 'guidance' in data:
        guidance = data['guidance']
        if not guidance['customTheme'].strip() and not guidance['theme']:
            missing.append('guidance.theme')
        if not guidance['structure']:
            missing.append('guidance.structure')
        for part in GUIDANCE_PARTS:
            if not guidance['slots'][part] and not guidance['notes'][part].strip():
                missing.append('guidance.slots.' + part)
        for field in ('sentence', 'paragraph'):
            if not guidance[field].strip():
                missing.append('guidance.' + field)
        for field in ('paragraphs', 'final_paragraphs'):
            if not data[field].get('p_full', '').strip():
                missing.append(field + '.p_full')
        if set(guidance['confirmed']) != set(range(9)):
            missing.append('guidance.confirmed')
        if guidance['review']:
            missing.append('guidance.review')
        if not guidance['endingOkay']:
            missing.append('guidance.endingOkay')
    else:
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
    if 'coaching' in data:
        coaching = data['coaching']
        for field in COACHING_PARTS:
            if not coaching['material'][field].strip():
                missing.append('coaching.material.' + field)
        if not coaching['comparison']['choices']:
            missing.append('coaching.comparison.choices')
        for round in ('structure', 'language'):
            if not data['no_change'][round]:
                for field in ('before', 'after', 'reason'):
                    if not coaching['evidence'][round][field].strip():
                        missing.append('coaching.evidence.' + round + '.' + field)
                for field, manuscript in (
                    ('before', 'paragraphs' if round == 'structure' else 'structure_paragraphs'),
                    ('after', 'structure_paragraphs' if round == 'structure' else 'final_paragraphs'),
                ):
                    quote = coaching['evidence'][round][field].strip()
                    paragraphs = data[manuscript]
                    order = [card['id'] for card in data['outline'] if card['id'] in paragraphs]
                    order += sorted(set(paragraphs) - set(order))
                    text = '\n\n'.join(paragraphs[key] for key in order if paragraphs[key].strip())
                    # This is a factual quotation check, not a judgment of
                    # writing quality. Incomplete or detached evidence remains
                    # saveable; only completing a work requires actual excerpts.
                    if quote and quote not in text:
                        missing.append('coaching.evidence.' + round + '.' + field)
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
        except (OSError, sqlite3.Error) as error:
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
        lesson, data = self._stored(row)
        return {**{key: row[key] for key in ('id', 'lesson_id', 'revision', 'status', 'created_at', 'updated_at', 'completed_at')},
                'lesson_snapshot': lesson, 'data': data}

    @staticmethod
    def _stored(row):
        try:
            lesson = validate_lesson(_decode(row['lesson_json']))
            data = validate_data(_decode(row['data_json']), lesson)
            if any(field in lesson and field not in data for field in ('guidance', 'coaching')):
                raise WritingError('写作引导记录不完整。')
            return lesson, data
        except (WritingError, KeyError, TypeError, ValueError) as error:
            raise WritingError('作文记录暂时无法读取，原稿已保留。', 503) from error

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
        if row['status'] == 'abandoned':
            raise WritingError('这次练习已放弃，原稿和版本已保留，可以另开一次练习。',
                               409, code='abandoned', current_revision=row['revision'])
        if row['status'] != 'in_progress':
            raise WritingError('这次练习已完成，可以另开一次练习。', 409, code='completed', current_revision=row['revision'])
        if _revision(base_revision) != row['revision']:
            raise WritingError('另一页面已有更新。本机这份内容仍可保留，请先查看最新稿。', 409,
                               code='conflict', current_revision=row['revision'])
        if row['revision'] == 9_007_199_254_740_991:
            raise WritingError('草稿版本已达到上限，原稿已保留，请联系管理员。', 503)

    def start(self, lesson):
        lesson = validate_lesson(lesson)
        data = initial_data(lesson)
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
            rows = connection.execute("SELECT * FROM writing_attempts WHERE status!='abandoned' ORDER BY updated_at DESC,id DESC LIMIT 200").fetchall()
            result = []
            for row in rows:
                lesson, data = self._stored(row)
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
            lesson, previous = self._stored(row)
            if isinstance(data, dict):
                for field in ('guidance', 'coaching'):
                    if field not in data and field in previous:
                        data = {**data, field: deepcopy(previous[field])}
            data = validate_data(data, lesson)
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
            attempt = self._row(connection, attempt_id)
            lesson, _ = self._stored(attempt)
            row = connection.execute('SELECT * FROM writing_versions WHERE id=? AND attempt_id=?', (version_id, attempt_id)).fetchone()
            if row is None:
                raise WritingError('版本不存在。', 404)
            try:
                data = validate_data(_decode(row['data_json']), lesson)
                if any(field in lesson and field not in data for field in ('guidance', 'coaching')):
                    raise WritingError('写作引导版本不完整。')
            except WritingError as error:
                raise WritingError('作文版本暂时无法读取，原稿已保留。', 503) from error
            return {**{key: row[key] for key in ('id', 'attempt_id', 'label', 'revision', 'created_at')}, 'data': data}

    def abandon(self, attempt_id, base_revision):
        _valid_id(attempt_id, ATTEMPT_ID)
        _revision(base_revision)
        with self._connection(write=True) as connection:
            row = self._row(connection, attempt_id)
            if row['status'] == 'abandoned':
                if base_revision != row['revision']:
                    raise WritingError('这次练习的状态已有更新，请刷新列表后查看。', 409,
                                       code='conflict', current_revision=row['revision'])
                return {'attempt': self._public(row), 'versions': self._versions(connection, attempt_id)}
            self._check_write(row, base_revision)
            _, data = self._stored(row)
            now, revision = china_now_iso(timespec='microseconds'), row['revision'] + 1
            connection.execute("UPDATE writing_attempts SET revision=?,status='abandoned',updated_at=? WHERE id=?",
                               (revision, now, attempt_id))
            self._snapshot(connection, attempt_id, data, revision, '放弃时草稿', now)
            return {'attempt': self._public(self._row(connection, attempt_id)), 'versions': self._versions(connection, attempt_id)}

    def restore(self, attempt_id, base_revision, version_id):
        _valid_id(attempt_id, ATTEMPT_ID)
        _valid_id(version_id, VERSION_ID, '版本不存在。')
        with self._connection(write=True) as connection:
            row = self._row(connection, attempt_id)
            self._check_write(row, base_revision)
            lesson, previous = self._stored(row)
            version = connection.execute('SELECT * FROM writing_versions WHERE id=? AND attempt_id=?', (version_id, attempt_id)).fetchone()
            if version is None:
                raise WritingError('版本不存在。', 404)
            now, revision = china_now_iso(timespec='microseconds'), row['revision'] + 1
            try:
                restored = validate_data(_decode(version['data_json']), lesson)
                if any(field in lesson and field not in restored for field in ('guidance', 'coaching')):
                    raise WritingError('写作引导版本不完整。')
            except WritingError as error:
                raise WritingError('作文版本暂时无法恢复，原稿已保留。', 503) from error
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

    def flow_access(username, attempt_id):
        learning_store = store(username)
        result = learning_store.get(attempt_id)
        snapshot = result['attempt']['lesson_snapshot']
        capability = request.headers.get('X-Writing-Flow')
        if (('coaching' in snapshot and capability != 'guided-v2')
                or ('guidance' in snapshot and capability not in ('guided-v1', 'guided-v2'))):
            raise WritingError('这份练习使用新版写作引导，请刷新页面后重新打开。原稿会保留。',
                               409, code='upgrade_required')
        return learning_store, result

    def body(fields, optional=()):
        data = request.get_json(silent=True)
        if (not isinstance(data, dict) or not set(fields) <= set(data)
                or set(data) - set(fields) - set(optional)):
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
        data = body(('lesson_id',), ('flow_version',))
        if 'flow_version' in data and (type(data['flow_version']) is not int or data['flow_version'] not in (1, 2)):
            raise WritingError('写作引导版本无效，请刷新后再试。')
        _valid_id(data['lesson_id'], LESSON_ID, '练习题目不存在。')
        lesson = next((item for item in lessons() if item['id'] == data['lesson_id']), None)
        if lesson is None:
            raise WritingError('练习题目不存在。', 404)
        if 'flow_version' in data:
            if 'guidance' not in lesson:
                raise WritingError('这道题暂未提供新版写作引导，请选择已有练习方式。')
            if data['flow_version'] == 2:
                if 'coaching' not in lesson:
                    raise WritingError('这道题暂未提供新版写作辅助，请选择已有练习方式。')
            else:
                lesson = {key: value for key, value in lesson.items() if key != 'coaching'}
        elif 'guidance' in lesson:
            # An already-open classic client can still create a complete classic
            # attempt. Its immutable snapshot never pretends to support nine steps.
            lesson = {key: value for key, value in lesson.items() if key not in ('guidance', 'coaching')}
        return jsonify({'attempt': store(username).start(lesson), 'read_only': False}), 201

    @routes.get('/attempts/<attempt_id>')
    @services.token_required
    def get(username, attempt_id):
        _, result = flow_access(username, attempt_id)
        return jsonify({**result, 'read_only': bool(getattr(g, 'is_parent', False)) or result['attempt']['status'] == 'abandoned'})

    @routes.patch('/attempts/<attempt_id>')
    @services.token_required
    @services.parent_forbidden
    def save(username, attempt_id):
        learning_store, _ = flow_access(username, attempt_id)
        data = body(('base_revision', 'data'))
        return jsonify(learning_store.save(attempt_id, data['base_revision'], data['data']))

    @routes.post('/attempts/<attempt_id>/checkpoints')
    @services.token_required
    @services.parent_forbidden
    def checkpoint(username, attempt_id):
        learning_store, _ = flow_access(username, attempt_id)
        data = body(('base_revision', 'data', 'label'))
        if not isinstance(data['label'], str) or not data['label'].strip() or len(data['label']) > 80:
            raise WritingError('保存版本的名称无效。')
        return jsonify(learning_store.save(attempt_id, data['base_revision'], data['data'], label=data['label']))

    @routes.post('/attempts/<attempt_id>/complete')
    @services.token_required
    @services.parent_forbidden
    def complete(username, attempt_id):
        learning_store, _ = flow_access(username, attempt_id)
        data = body(('base_revision', 'data'))
        return jsonify(learning_store.save(attempt_id, data['base_revision'], data['data'], complete=True))

    @routes.post('/attempts/<attempt_id>/abandon')
    @services.token_required
    @services.parent_forbidden
    def abandon(username, attempt_id):
        learning_store, _ = flow_access(username, attempt_id)
        data = body(('base_revision',))
        return jsonify({**learning_store.abandon(attempt_id, data['base_revision']), 'read_only': True})

    @routes.get('/attempts/<attempt_id>/versions/<version_id>')
    @services.token_required
    def get_version(username, attempt_id, version_id):
        learning_store, _ = flow_access(username, attempt_id)
        return jsonify({'version': learning_store.get_version(attempt_id, version_id)})

    @routes.post('/attempts/<attempt_id>/restore')
    @services.token_required
    @services.parent_forbidden
    def restore(username, attempt_id):
        learning_store, _ = flow_access(username, attempt_id)
        data = body(('base_revision', 'version_id'))
        return jsonify(learning_store.restore(attempt_id, data['base_revision'], data['version_id']))

    app.register_blueprint(routes)

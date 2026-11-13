"""Private fixed-paper snapshots, exact answer grading, and per-subject attempts."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
import re
import secrets
import sqlite3
import unicodedata

from flask import Blueprint, current_app, jsonify, request

from app_time import china_now_iso
from storage_utils import interprocess_file_lock


IDENTIFIER = re.compile(r'[a-z0-9][a-z0-9_-]{0,95}')
ATTEMPT_ID = re.compile(r'[a-f0-9]{32}')
QUESTION_TYPES = {'choice', 'fill', 'calculation', 'application'}


class PracticeError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _text(value, limit=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise PracticeError('练习内容格式无效', 503)
    return value


def _identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise PracticeError('练习编号无效', 404)
    return value


def normalized_answer(value, field):
    """Accept ordinary numeric notation; never execute a student's expression."""
    text = unicodedata.normalize('NFKC', value).strip().replace('−', '-')
    if field['format'] == 'text':
        return re.sub(r'\s+', '', text).replace('〇', '零').rstrip('。.!！')
    for unit in sorted(set([field.get('unit', ''), *field.get('unit_aliases', [])]) - {''}, key=len, reverse=True):
        suffix = unicodedata.normalize('NFKC', unit)
        if text.endswith(suffix):
            text = text[:-len(suffix)].strip()
            break
    # Correct thousands separators and spaces are allowed; arbitrary punctuation is not.
    if ',' in text:
        if not re.fullmatch(r'[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?', text):
            return None
        text = text.replace(',', '')
    text = re.sub(r'\s+', '', text)
    if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d+)?|\d+/\d+)', text) or len(text) > 80:
        return None
    try:
        number = Fraction(text)
    except (ValueError, ZeroDivisionError):
        return None
    if field['format'] == 'integer' and number.denominator != 1:
        return None
    return number


def load_private_paper(directory: Path, subject: str, metadata: dict) -> dict:
    paper_id = _identifier(metadata['id'])
    try:
        paper = json.loads((directory / subject / 'papers' / (paper_id + '.json')).read_text(encoding='utf-8'))
        if (paper.get('id') != paper_id or paper.get('subject') != subject or paper.get('schema_version') != 1
                or not isinstance(paper.get('questions'), list) or not 1 <= len(paper['questions']) <= 100):
            raise PracticeError('练习内容版本无效', 503)
        _text(paper.get('title'), 200)
        _text(paper.get('scope'), 500)
        if type(paper.get('time_minutes')) is not int or not 1 <= paper['time_minutes'] <= 180:
            raise PracticeError('练习时长无效', 503)
        seen, total = set(), 0
        for question in paper['questions']:
            question_id = _identifier(question.get('id'))
            if question_id in seen or question.get('type') not in QUESTION_TYPES:
                raise PracticeError('练习题目配置无效', 503)
            seen.add(question_id)
            _text(question.get('prompt'))
            _text(question.get('explanation'))
            if question.get('difficulty', 'standard') not in ('standard', 'challenge'):
                raise PracticeError('练习难度标记无效', 503)
            if not isinstance(question.get('hints', []), list) or len(question.get('hints', [])) > 4:
                raise PracticeError('练习提示格式无效', 503)
            for hint in question.get('hints', []):
                _text(hint, 500)
            points = question.get('points')
            if type(points) is not int or not 1 <= points <= 100:
                raise PracticeError('练习分值无效', 503)
            total += points
            if question.get('diagram_url'):
                prefix = f'/static/subject-content/{subject}/'
                if (not question['diagram_url'].startswith(prefix)
                        or not re.fullmatch(r'/static/subject-content/[a-z]+/[a-z0-9_-]+\.(?:svg|png)', question['diagram_url'])):
                    raise PracticeError('练习插图地址无效', 503)
                _text(question.get('diagram_alt'), 300)
            if question['type'] == 'choice':
                options = question.get('options')
                if not isinstance(options, list) or not 2 <= len(options) <= 6:
                    raise PracticeError('练习选项无效', 503)
                ids = [_identifier(option.get('id')) for option in options]
                if len(ids) != len(set(ids)) or question.get('answer') not in ids:
                    raise PracticeError('练习选择题答案无效', 503)
                for option in options:
                    _text(option.get('text'), 500)
            else:
                fields = question.get('fields')
                if not isinstance(fields, list) or not 1 <= len(fields) <= 8:
                    raise PracticeError('练习答题栏无效', 503)
                ids, field_points = set(), 0
                for field in fields:
                    field_id = _identifier(field.get('id'))
                    if field_id in ids or field.get('format') not in ('integer', 'number', 'fraction', 'text'):
                        raise PracticeError('练习答题格式无效', 503)
                    ids.add(field_id)
                    _text(field.get('label'), 200)
                    if type(field.get('points')) is not int or field['points'] < 1:
                        raise PracticeError('练习小题分值无效', 503)
                    field_points += field['points']
                    if not isinstance(field.get('accepted_answers'), list) or not field['accepted_answers']:
                        raise PracticeError('练习参考答案缺失', 503)
                    for answer in field['accepted_answers']:
                        _text(answer, 120)
                        if normalized_answer(answer, field) is None:
                            raise PracticeError('练习参考答案格式无效', 503)
                    if field.get('answer') not in field['accepted_answers']:
                        raise PracticeError('练习参考答案不一致', 503)
                if field_points != points:
                    raise PracticeError('练习小题分值不一致', 503)
        if total != 100 or len(paper['questions']) != metadata['question_count']:
            raise PracticeError('练习题数或总分不一致', 503)
        return paper
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        if isinstance(error, PracticeError):
            raise
        raise PracticeError('练习内容暂不可用，请稍后再试', 503) from error


def public_question(question):
    result = {key: deepcopy(question[key]) for key in ('id', 'type', 'prompt', 'points', 'diagram_url', 'diagram_alt', 'difficulty', 'hints') if key in question}
    if question['type'] == 'choice':
        result['options'] = deepcopy(question['options'])
    else:
        result['fields'] = [{key: deepcopy(field[key]) for key in ('id', 'label', 'format', 'unit', 'hint', 'points') if key in field}
                            for field in question['fields']]
    return result


def validate_answers(paper, answers, workings=None):
    if not isinstance(answers, dict) or (workings is not None and not isinstance(workings, dict)):
        raise PracticeError('作答格式无效')
    known = {question['id']: question for question in paper['questions']}
    if set(answers) - set(known) or set(workings or {}) - set(known):
        raise PracticeError('作答包含不属于本次练习的题目')
    for question_id, answer in answers.items():
        question = known[question_id]
        if question['type'] == 'choice':
            if not isinstance(answer, str) or (answer and answer not in {option['id'] for option in question['options']}):
                raise PracticeError('请选择本题提供的选项')
        else:
            field_ids = {field['id'] for field in question['fields']}
            if not isinstance(answer, dict) or set(answer) - field_ids:
                raise PracticeError('答题栏格式无效')
            if any(not isinstance(value, str) or len(value) > 120 for value in answer.values()):
                raise PracticeError('答案超过长度限制')
    if any(not isinstance(text, str) or len(text) > 2000 for text in (workings or {}).values()):
        raise PracticeError('解题过程超过长度限制')


def grade_paper(paper, answers, workings=None):
    validate_answers(paper, answers, workings)
    rows, score = [], 0
    for question in paper['questions']:
        row = public_question(question)
        answer = answers.get(question['id'], '' if question['type'] == 'choice' else {})
        earned = 0
        if question['type'] == 'choice':
            correct = answer == question['answer']
            earned = question['points'] if correct else 0
            options = {option['id']: option['text'] for option in question['options']}
            row.update(user_answer=options.get(answer, '未作答'), correct_answer=options[question['answer']], fields=[])
        else:
            fields = []
            for field in question['fields']:
                value = answer.get(field['id'], '').strip()
                normalized = normalized_answer(value, field) if value else None
                correct = normalized is not None and normalized in {normalized_answer(item, field) for item in field['accepted_answers']}
                points = field['points'] if correct else 0
                earned += points
                fields.append({'id': field['id'], 'label': field['label'], 'unit': field.get('unit', ''),
                               'user_answer': value, 'correct_answer': field['answer'],
                               'correct': correct, 'earned_points': points, 'points': field['points']})
            row['fields'] = fields
        row.update(earned_points=earned, correct=earned == question['points'], explanation=question['explanation'],
                   working=(workings or {}).get(question['id'], ''))
        rows.append(row)
        score += earned
    return {'paper_id': paper['id'], 'paper_title': paper['title'], 'score': score, 'max_score': 100,
            'base_score': sum(row['earned_points'] for row in rows if row.get('difficulty') != 'challenge'),
            'challenge_score': sum(row['earned_points'] for row in rows if row.get('difficulty') == 'challenge'),
            'base_max_score': sum(row['points'] for row in rows if row.get('difficulty') != 'challenge'),
            'challenge_max_score': sum(row['points'] for row in rows if row.get('difficulty') == 'challenge'),
            'total': len(rows), 'correct_count': sum(row['correct'] for row in rows),
            'partial_count': sum(0 < row['earned_points'] < row['points'] for row in rows),
            'questions': rows, 'grading_note': paper.get('grading_note', '')}


class SubjectPracticeStore:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connection(self):
        # Initialization and transactions share one per-student/subject lock.
        # Changing journal mode during another connection's first write can
        # otherwise return SQLITE_BUSY immediately despite the busy timeout.
        with interprocess_file_lock(self.path.with_name('.practice.lock')):
            with self._sqlite_connection() as connection:
                yield connection

    @contextmanager
    def _sqlite_connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=20)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute('PRAGMA journal_mode=WAL')
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS practice_attempts (
                    id TEXT PRIMARY KEY, paper_id TEXT NOT NULL, paper_json TEXT NOT NULL,
                    status TEXT NOT NULL, answers_json TEXT NOT NULL DEFAULT '{}',
                    workings_json TEXT NOT NULL DEFAULT '{}', question_index INTEGER NOT NULL DEFAULT 0,
                    revision INTEGER NOT NULL DEFAULT 0, started_at TEXT NOT NULL,
                    submitted_at TEXT, result_json TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS practice_one_active
                    ON practice_attempts(status) WHERE status='in_progress';
            ''')
            yield connection
            connection.commit()
        except sqlite3.DatabaseError as error:
            connection.rollback()
            raise PracticeError('练习数据暂时无法读取，原记录已保留，请稍后重试', 503) from error
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _public(self, row):
        paper = json.loads(row['paper_json'])
        return {'id': row['id'], 'paper_id': paper['id'], 'paper_title': paper['title'], 'status': row['status'],
                'questions': [public_question(question) for question in paper['questions']],
                'answers': json.loads(row['answers_json']), 'workings': json.loads(row['workings_json']),
                'index': row['question_index'], 'revision': row['revision'], 'started_at': row['started_at'],
                'time_minutes': paper['time_minutes'], 'scope': paper['scope'],
                'grading_note': paper.get('grading_note', '')}

    def _row(self, connection, attempt_id):
        if not isinstance(attempt_id, str) or not ATTEMPT_ID.fullmatch(attempt_id):
            raise PracticeError('练习不存在', 404)
        row = connection.execute('SELECT * FROM practice_attempts WHERE id=?', (attempt_id,)).fetchone()
        if row is None:
            raise PracticeError('练习不存在', 404)
        return row

    def start(self, paper):
        with self.connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            current = connection.execute("SELECT * FROM practice_attempts WHERE status='in_progress'").fetchone()
            if current:
                if current['paper_id'] == paper['id']:
                    return self._public(current)
                raise PracticeError('还有一份未完成练习，请先继续或放弃本次练习', 409)
            attempt_id = secrets.token_hex(16)
            connection.execute('INSERT INTO practice_attempts(id,paper_id,paper_json,status,started_at) VALUES(?,?,?,?,?)',
                               (attempt_id, paper['id'], json.dumps(paper, ensure_ascii=False), 'in_progress', china_now_iso()))
            return self._public(self._row(connection, attempt_id))

    def active(self):
        if not self.path.is_file():
            return None
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM practice_attempts WHERE status='in_progress'").fetchone()
            return self._public(row) if row else None

    def get(self, attempt_id, result_only=False):
        if not self.path.is_file():
            raise PracticeError('练习不存在', 404)
        with self.connection() as connection:
            row = self._row(connection, attempt_id)
            if result_only:
                if row['status'] != 'submitted':
                    raise PracticeError('这份练习尚未交卷', 404)
                return json.loads(row['result_json'])
            if row['status'] != 'in_progress':
                raise PracticeError('这份练习已结束，请返回练习列表', 409)
            return self._public(row)

    def save(self, attempt_id, body):
        if not self.path.is_file():
            raise PracticeError('练习不存在', 404)
        with self.connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = self._row(connection, attempt_id)
            if row['status'] != 'in_progress':
                raise PracticeError('这份练习已结束', 409)
            paper = json.loads(row['paper_json'])
            answers, workings, index, revision = body.get('answers'), body.get('workings', {}), body.get('index'), body.get('revision')
            validate_answers(paper, answers, workings)
            if type(index) is not int or not 0 <= index < len(paper['questions']) or type(revision) is not int or revision < 1:
                raise PracticeError('答题进度格式无效')
            encoded, work = json.dumps(answers, ensure_ascii=False, sort_keys=True), json.dumps(workings, ensure_ascii=False, sort_keys=True)
            if revision <= row['revision']:
                if revision == row['revision'] and encoded == row['answers_json'] and work == row['workings_json'] and index == row['question_index']:
                    return {'revision': revision}
                raise PracticeError('练习进度已在其他页面更新，请重新进入后继续', 409)
            connection.execute('UPDATE practice_attempts SET answers_json=?,workings_json=?,question_index=?,revision=? WHERE id=?',
                               (encoded, work, index, revision, attempt_id))
            return {'revision': revision}

    def submit(self, attempt_id, body):
        if not self.path.is_file():
            raise PracticeError('练习不存在', 404)
        with self.connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = self._row(connection, attempt_id)
            if row['status'] == 'submitted':
                return json.loads(row['result_json'])
            if row['status'] != 'in_progress':
                raise PracticeError('已放弃的练习不能交卷', 409)
            paper = json.loads(row['paper_json'])
            answers, workings = body.get('answers'), body.get('workings', {})
            result = grade_paper(paper, answers, workings)
            result.update(id=attempt_id, submitted_at=china_now_iso(), status='submitted')
            connection.execute("UPDATE practice_attempts SET status='submitted',answers_json=?,workings_json=?,submitted_at=?,result_json=? WHERE id=?",
                               (json.dumps(answers, ensure_ascii=False), json.dumps(workings, ensure_ascii=False),
                                result['submitted_at'], json.dumps(result, ensure_ascii=False), attempt_id))
            return result

    def abandon(self, attempt_id):
        if not self.path.is_file():
            raise PracticeError('练习不存在', 404)
        with self.connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = self._row(connection, attempt_id)
            if row['status'] == 'submitted':
                raise PracticeError('已交卷的练习不能放弃', 409)
            connection.execute("UPDATE practice_attempts SET status='abandoned',answers_json='{}',workings_json='{}' WHERE id=?", (attempt_id,))
            return {'ok': True}

    def records(self):
        if not self.path.is_file():
            return []
        with self.connection() as connection:
            rows = connection.execute("SELECT id,result_json FROM practice_attempts WHERE status='submitted' ORDER BY submitted_at DESC LIMIT 200").fetchall()
            return [{key: result[key] for key in ('id', 'paper_id', 'paper_title', 'score', 'total', 'correct_count', 'submitted_at', 'status', 'base_score', 'challenge_score') if key in result}
                    for row in rows for result in [json.loads(row['result_json'])]]


def register_subject_practice_routes(app, services, catalog):
    routes = Blueprint('subject_practice', __name__)

    @routes.errorhandler(PracticeError)
    def invalid(error):
        return jsonify({'error': str(error)}), error.status

    @routes.after_request
    def private(response):
        response.cache_control.no_store = True
        return response

    def store(username, subject):
        if subject not in ('math', 'chinese'):
            raise PracticeError('学科不存在', 404)
        return SubjectPracticeStore(services.DATA_DIR / username / 'subjects' / subject / 'practice.sqlite3')

    def body():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise PracticeError('请提交有效的作答内容')
        return data

    @routes.post('/api/subjects/<subject>/papers/<paper_id>/attempts')
    @services.token_required
    @services.parent_forbidden
    def start(username, subject, paper_id):
        user_store = store(username, subject)
        try:
            metadata = next((paper for paper in catalog(subject) if paper['id'] == paper_id), None)
        except ValueError as error:
            raise PracticeError('练习目录暂时无法读取，请稍后重试', 503) from error
        if metadata is None or not metadata.get('interactive'):
            raise PracticeError('这份练习暂不支持在线作答', 404)
        directory = Path(current_app.config.get('SUBJECT_CONTENT_DIR', Path(__file__).resolve().parent / 'subject_content'))
        return jsonify({'attempt': user_store.start(load_private_paper(directory, subject, metadata))}), 201

    @routes.get('/api/subjects/<subject>/attempts/current')
    @services.token_required
    @services.parent_forbidden
    def active(username, subject):
        return jsonify({'attempt': store(username, subject).active()})

    @routes.get('/api/subjects/<subject>/attempts/<attempt_id>')
    @services.token_required
    @services.parent_forbidden
    def get(username, subject, attempt_id):
        return jsonify({'attempt': store(username, subject).get(attempt_id)})

    @routes.put('/api/subjects/<subject>/attempts/<attempt_id>')
    @services.token_required
    @services.parent_forbidden
    def save(username, subject, attempt_id):
        return jsonify(store(username, subject).save(attempt_id, body()))

    @routes.post('/api/subjects/<subject>/attempts/<attempt_id>/submit')
    @services.token_required
    @services.parent_forbidden
    def submit(username, subject, attempt_id):
        return jsonify({'result': store(username, subject).submit(attempt_id, body())})

    @routes.post('/api/subjects/<subject>/attempts/<attempt_id>/abandon')
    @services.token_required
    @services.parent_forbidden
    def abandon(username, subject, attempt_id):
        return jsonify(store(username, subject).abandon(attempt_id))

    @routes.get('/api/subjects/<subject>/records/<attempt_id>')
    @services.token_required
    def result(username, subject, attempt_id):
        return jsonify({'result': store(username, subject).get(attempt_id, result_only=True)})

    app.register_blueprint(routes)

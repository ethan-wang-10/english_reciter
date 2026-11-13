"""Subject portals sharing accounts while keeping their learning content separate."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from flask import Blueprint, abort, current_app, g, jsonify, make_response, render_template_string, request

from exam_grades import normalize_grade, normalize_term


ROOT = Path(__file__).resolve().parent
SUBJECTS = {
    'chinese': {'name': '语文', 'title': '语文学习空间', 'description': '读懂文字，积累表达。'},
    'math': {'name': '数学', 'title': '数学学习空间', 'description': '理解思路，练习解题。'},
}
DEFAULT_SITE_URLS = {
    'english': 'https://english.itorange.online',
    'chinese': 'https://chinese.itorange.online',
    'math': 'https://math.itorange.online',
}
ID_PATTERN = re.compile(r'[a-z0-9][a-z0-9_-]{0,95}')
RECORD_FIELDS = ('id', 'paper_id', 'paper_title', 'score', 'total', 'correct_count', 'submitted_at', 'status')


class SubjectContentError(ValueError):
    pass


def configured_sites(app) -> dict:
    urls = app.config.get('SUBJECT_SITE_URLS', DEFAULT_SITE_URLS)
    sites = {}
    for subject_id, default in DEFAULT_SITE_URLS.items():
        value = str(urls.get(subject_id, default)).rstrip('/')
        parsed = urlsplit(value)
        parsed.port
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username
                or parsed.password or parsed.path or parsed.query or parsed.fragment
                or (parsed.scheme != 'https' and not app.testing)):
            raise ValueError('Subject site URLs must be configured absolute HTTPS origins')
        sites[subject_id] = {'id': subject_id, 'url': value,
                             'name': '英语' if subject_id == 'english' else SUBJECTS[subject_id]['name']}
    if len({site['url'] for site in sites.values()}) != 3:
        raise ValueError('Each subject site needs a distinct origin')
    return sites


def subject_for_host(app, host: str):
    # Host is only used to choose a public page, never to construct an auth return URL.
    try:
        requested = urlsplit('//' + host)
        hostname, port = (requested.hostname or '').lower().rstrip('.'), requested.port
    except ValueError:
        return None
    for subject_id, site in configured_sites(app).items():
        configured = urlsplit(site['url'])
        default_port = 443 if configured.scheme == 'https' else 80
        if (subject_id in SUBJECTS and configured.hostname.lower().rstrip('.') == hostname
                and (port or default_port) == (configured.port or default_port)):
            return subject_id
    return None


def site_config(app, subject: str) -> dict:
    if subject not in SUBJECTS:
        abort(404)
    sites = configured_sites(app)
    return {'id': subject, **SUBJECTS[subject], 'origin': sites[subject]['url'],
            'subjects': list(sites.values()), 'english_url': sites['english']['url'],
            'auth_origin': sites['english']['url']}


def render_subject_home(app, subject: str):
    template = (Path(app.root_path) / 'static/subject.html').read_text(encoding='utf-8')
    response = make_response(render_template_string(template, site_config=site_config(app, subject)))
    response.cache_control.no_store = True
    response.headers['Referrer-Policy'] = 'same-origin'
    return response


def _text(value, *, required=False, maximum=2000):
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise SubjectContentError('Invalid subject content text')
    return value


def _identifier(value):
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise SubjectContentError('Invalid subject content id')
    return value


def _catalog(subject: str) -> dict:
    directory = Path(current_app.config.get('SUBJECT_CONTENT_DIR', ROOT / 'subject_content'))
    try:
        raw = json.loads((directory / subject / 'catalog.json').read_text(encoding='utf-8'))
        if not isinstance(raw, dict) or raw.get('schema_version') != 1 or raw.get('subject') != subject:
            raise SubjectContentError('Subject content version or subject does not match')
        result = {'papers': [], 'knowledge': []}
        for kind in result:
            if not isinstance(raw.get(kind), list):
                raise SubjectContentError('Subject content must contain lists')
            seen = set()
            for item in raw[kind]:
                if not isinstance(item, dict):
                    raise SubjectContentError('Subject content entry must be an object')
                content_id = _identifier(item.get('id'))
                if content_id in seen:
                    raise SubjectContentError('Duplicate subject content id')
                seen.add(content_id)
                public = {'id': content_id, 'title': _text(item.get('title'), required=True, maximum=200),
                          'description': _text(item.get('description', '')),
                          'grade': normalize_grade(item.get('grade')), 'term': normalize_term(item.get('term'))}
                for label in ('scope', 'edition'):
                    if item.get(label):
                        public[label] = _text(item[label], maximum=500)
                if item.get('pdf_url'):
                    url = item['pdf_url']
                    if (not isinstance(url, str) or not url.startswith(f'/static/subject-content/{subject}/')
                            or not re.fullmatch(r'/static/subject-content/[a-z]+/[a-z0-9_-]+\.pdf', url)):
                        raise SubjectContentError('Invalid subject PDF URL')
                    public['pdf_url'] = url
                if kind == 'papers':
                    phase = item.get('exam_kind', 'practice')
                    if phase not in ('midterm', 'final', 'practice'):
                        raise SubjectContentError('Invalid exam kind')
                    public['exam_kind'] = phase
                    if type(item.get('interactive', False)) is not bool:
                        raise SubjectContentError('Invalid interactive paper flag')
                    public['interactive'] = item.get('interactive', False)
                    for name in ('question_count', 'time_minutes'):
                        value = item.get(name, 0)
                        if type(value) is not int or not 0 <= value <= 10000:
                            raise SubjectContentError('Invalid paper count or duration')
                        public[name] = value
                    if item.get('knowledge_id'):
                        public['knowledge_id'] = _identifier(item['knowledge_id'])
                else:
                    sections = item.get('sections', [])
                    if not isinstance(sections, list):
                        raise SubjectContentError('Invalid knowledge sections')
                    public['sections'] = []
                    for section in sections:
                        if not isinstance(section, dict) or not isinstance(section.get('items'), list):
                            raise SubjectContentError('Invalid knowledge section')
                        public['sections'].append({'title': _text(section.get('title'), required=True, maximum=200),
                                                   'items': [_text(text, required=True) for text in section['items']]})
                result[kind].append(public)
        return result
    except (OSError, ValueError, TypeError) as error:
        if isinstance(error, SubjectContentError):
            raise
        raise SubjectContentError('Subject content is unavailable') from error


def _records(services, username: str, subject: str) -> list:
    from subject_practice import PracticeError, SubjectPracticeStore
    try:
        practice = SubjectPracticeStore(services.DATA_DIR / username / 'subjects' / subject / 'practice.sqlite3').records()
    except PracticeError as error:
        raise SubjectContentError(str(error)) from error
    from subject_writing import WritingStore
    writing = WritingStore(services.DATA_DIR / username / 'subjects' / 'chinese' / 'writing.sqlite3').records() if subject == 'chinese' else []
    path = services.DATA_DIR / username / 'subjects' / subject / 'records.json'
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return sorted([*practice, *writing], key=lambda row: row.get('submitted_at', ''), reverse=True)[:200]
    except (OSError, ValueError) as error:
        raise SubjectContentError('Subject records are unavailable') from error
    if not isinstance(raw, list) or any(not isinstance(row, dict) for row in raw):
        raise SubjectContentError('Invalid subject records')
    # Only the record summary belongs on the portal; grading keys remain private.
    legacy = [{key: deepcopy(row[key]) for key in RECORD_FIELDS if key in row} for row in raw[-200:]]
    return sorted([*legacy, *practice, *writing], key=lambda row: row.get('submitted_at', ''), reverse=True)[:200]


def register_subject_portal_routes(app, services):
    routes = Blueprint('subject_portal', __name__)

    @routes.errorhandler(SubjectContentError)
    def unavailable(error):
        services.logger.error('Subject content unavailable: %s', error)
        return jsonify({'error': '内容暂时无法读取，请稍后重试。'}), 503

    @routes.after_request
    def private_response(response):
        response.cache_control.no_store = True
        return response

    @routes.get('/subjects/<subject>')
    @routes.get('/subjects/<subject>/')
    def preview(subject):
        return render_subject_home(app, subject)

    def checked(subject):
        if subject not in SUBJECTS:
            abort(404)
        return _catalog(subject)

    @routes.get('/api/subjects/<subject>/bootstrap')
    @services.token_required
    def bootstrap(username, subject):
        catalog = checked(subject)
        records = _records(services, username, subject)
        login = getattr(g, 'login_username', username)
        row = services.get_user(login) or {}
        learner = services.get_user(username) or {}
        parent = bool(getattr(g, 'is_parent', False))
        account = {'username': login, 'display_name': login, 'email': row.get('email') or '',
                   'is_parent': parent, 'child_username': username if parent else None,
                   'plan': learner.get('plan', 'free')}
        return jsonify({'subject': site_config(app, subject), 'account': account,
                        'summary': {'paper_count': len(catalog['papers']), 'knowledge_count': len(catalog['knowledge']),
                                    'record_count': len(records)}, **catalog, 'records': records})

    @routes.get('/api/subjects/<subject>/papers')
    @services.token_required
    def papers(username, subject):
        items = checked(subject)['papers']
        return jsonify({'items': items, 'count': len(items)})

    @routes.get('/api/subjects/<subject>/knowledge')
    @services.token_required
    def knowledge(username, subject):
        items = checked(subject)['knowledge']
        return jsonify({'items': items, 'count': len(items)})

    @routes.get('/api/subjects/<subject>/records')
    @services.token_required
    def records(username, subject):
        checked(subject)
        items = _records(services, username, subject)
        return jsonify({'items': items, 'count': len(items)})

    app.register_blueprint(routes)
    from subject_practice import register_subject_practice_routes
    register_subject_practice_routes(app, services, lambda subject: checked(subject)['papers'])

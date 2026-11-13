"""One-time, PKCE-bound sign-in handoff between explicitly configured sites.

All sites use the existing account/session store. The browser transports a
60-second authorization code; the existing bearer session remains server-side.
"""

from __future__ import annotations

import base64
from datetime import timedelta
import hashlib
import hmac
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from flask import Blueprint, jsonify, render_template_string, request

import auth_session_store


CODE_KIND = 'subject_sso_code'
CODE_TTL_SECONDS = 60
SITE_IDS = frozenset(('english', 'chinese', 'math'))
TOKEN_PATTERN = re.compile(r'[A-Za-z0-9_-]{43}')
VERIFIER_PATTERN = re.compile(r'[A-Za-z0-9._~-]{43,128}')
STATE_PATTERN = re.compile(r'[A-Za-z0-9_-]{32,128}')


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).decode('ascii').rstrip('=')


def _valid_challenge(value) -> bool:
    if not isinstance(value, str) or not TOKEN_PATTERN.fullmatch(value):
        return False
    try:
        decoded = base64.urlsafe_b64decode(value + '=')
    except ValueError:
        return False
    return len(decoded) == 32 and base64.urlsafe_b64encode(decoded).decode('ascii').rstrip('=') == value


def _site_config(sites):
    configured = sites()
    result = {}
    for site_id, site in configured.items():
        if site_id not in SITE_IDS:
            raise ValueError('Unsupported subject site')
        parsed = urlsplit(site['url'])
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                or parsed.username or parsed.password or parsed.path not in ('', '/')
                or parsed.query or parsed.fragment):
            raise ValueError('Subject sites must be explicit HTTP(S) origins')
        parsed.port  # Reject invalid ports before exposing a configuration.
        origin = f'{parsed.scheme.lower()}://{parsed.netloc.lower()}'
        result[site_id] = {'id': site_id, 'name': str(site.get('name') or site_id), 'url': origin + '/'}
    if len({urlsplit(site['url']).netloc for site in result.values()}) != len(result):
        raise ValueError('Subject sites require distinct hosts')
    return result


def _current_site(config):
    # Scheme comes from the configured canonical origin, rather than trusting
    # a user-supplied Forwarded/X-Forwarded-Host header behind the proxy.
    try:
        requested = urlsplit('//' + request.host)
        hostname = (requested.hostname or '').lower().rstrip('.')
        port = requested.port
    except ValueError:
        return None
    for site_id, site in config.items():
        configured = urlsplit(site['url'])
        default_port = 443 if configured.scheme == 'https' else 80
        if (configured.hostname.lower().rstrip('.') == hostname
                and (port or default_port) == (configured.port or default_port)):
            return site_id
    return None


def _same_origin(site):
    supplied = request.headers.get('Origin')
    return not supplied or supplied == site['url'].rstrip('/')


def _enabled_identity(services, username):
    row = services.get_user(username)
    if not services._user_row_is_enabled(row):
        return False
    if services.is_parent_user_record(row):
        child = row.get('child_username')
        if not isinstance(child, str) or not services.is_valid_username(child):
            return False
        child_row = services.get_user(child)
        return services._user_row_is_enabled(child_row) and not services.is_parent_user_record(child_row)
    return True


def register_subject_sso_routes(app, services, sites):
    routes = Blueprint('subject_sso', __name__)

    @routes.after_request
    def private_response(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Frame-Options'] = 'DENY'
        return response

    def context():
        config = _site_config(sites)
        current = _current_site(config)
        if current is None:
            return config, None, (jsonify({'error': '当前站点未启用学科登录切换'}), 404)
        if not _same_origin(config[current]):
            return config, current, (jsonify({'error': '登录切换来源无效'}), 403)
        return config, current, None

    @routes.get('/api/auth/sso/sites')
    def site_list():
        config, current, error = context()
        return error or jsonify({'sites': config, 'current_site': current})

    @routes.post('/api/auth/sso/ticket')
    @services.token_required
    def ticket(_effective_username):
        config, current, error = context()
        if error:
            return error
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({'error': '请提交有效的登录切换请求'}), 400
        target = body.get('target')
        challenge = body.get('code_challenge')
        if not isinstance(target, str) or target not in config or target == current:
            return jsonify({'error': '请选择有效的目标学科站点'}), 400
        if not _valid_challenge(challenge):
            return jsonify({'error': '登录切换校验信息无效'}), 400
        source_token = request.headers.get('Authorization', '')[7:].strip()
        login_username = services.verify_token(source_token)
        if not login_username or not _enabled_identity(services, login_username):
            return jsonify({'error': '登录已失效，请重新登录'}), 401
        principal = json.dumps({
            'username': login_username, 'source': current, 'target': target,
            'challenge': challenge, 'source_token': source_token,
        }, separators=(',', ':'))
        code = auth_session_store.create_session(CODE_KIND, principal, timedelta(seconds=CODE_TTL_SECONDS))
        return jsonify({'code': code, 'expires_in': CODE_TTL_SECONDS}), 201

    @routes.post('/api/auth/sso/exchange')
    def exchange():
        config, current, error = context()
        if error:
            return error
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({'error': '登录切换请求无效，请重新切换学科'}), 400
        code, verifier, target = body.get('code'), body.get('code_verifier'), body.get('target')
        if (not isinstance(code, str) or not TOKEN_PATTERN.fullmatch(code)
                or not isinstance(verifier, str) or not VERIFIER_PATTERN.fullmatch(verifier)
                or not isinstance(target, str) or target != current):
            return jsonify({'error': '登录切换校验失败，请重新切换学科'}), 400
        principal = auth_session_store.verify_session(code, CODE_KIND)
        try:
            payload = json.loads(principal) if principal else {}
        except (ValueError, TypeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        username = payload.get('username')
        source_token = payload.get('source_token')
        source = payload.get('source')
        challenge = payload.get('challenge')
        if (payload.get('target') != current or source not in config or source == current
                or not isinstance(challenge, str) or not hmac.compare_digest(challenge, _challenge(verifier))
                or not isinstance(username, str) or not isinstance(source_token, str)
                or services.verify_token(source_token) != username):
            return jsonify({'error': '登录切换已失效，请重新切换学科'}), 400
        if not _enabled_identity(services, username):
            return jsonify({'error': '账号或关联学生不可用，请联系管理员'}), 403
        # Validate before consuming, so a request with the wrong verifier or
        # target cannot burn another tab's valid one-time code.
        if auth_session_store.consume_session(code, CODE_KIND) != principal:
            return jsonify({'error': '登录切换已失效，请重新切换学科'}), 400
        token = services.create_token(username)
        # Logout/password reset may race with the handoff. A new session must
        # not outlive the revocation of the source session during exchange.
        if services.verify_token(source_token) != username or not _enabled_identity(services, username):
            auth_session_store.revoke_token(auth_session_store.SESSION_KIND_USER, token)
            return jsonify({'error': '登录已失效，请重新登录'}), 401
        return jsonify({
            'access_token': token, 'token_type': 'bearer', 'username': username,
            **services._auth_session_payload(username),
        })

    def bridge(mode):
        config, current, error = context()
        if error:
            return error
        query = request.args
        details = {}
        if mode == 'start':
            source = query.get('from')
            if source not in config or source == current:
                return jsonify({'error': '登录切换来源无效'}), 400
            details['source'] = source
        elif mode == 'authorize':
            target, state, challenge = query.get('target'), query.get('state'), query.get('code_challenge')
            if (target not in config or target == current or not STATE_PATTERN.fullmatch(state or '')
                    or not _valid_challenge(challenge)):
                return jsonify({'error': '登录切换校验失败'}), 400
            details.update(target=target, state=state, code_challenge=challenge)
        root = Path(__file__).resolve().parent
        script_url = '/static/js/subject-sso.js?v=20261113-sso-v1'
        try:
            manifest = json.loads((root / 'static/asset-manifest.json').read_text(encoding='utf-8'))
            script_url = manifest['assets']['/static/js/subject-sso.js']['url']
        except (OSError, ValueError, KeyError, TypeError):
            pass
        html = (root / 'static/subject-sso.html').read_text(encoding='utf-8')
        return render_template_string(html, sso_script_url=script_url, sso_config={
            'mode': mode, 'sites': config, 'current_site': current, **details,
        })

    routes.add_url_rule('/auth/subject-start', 'start', lambda: bridge('start'))
    routes.add_url_rule('/auth/subject-authorize', 'authorize', lambda: bridge('authorize'))
    routes.add_url_rule('/auth/subject-callback', 'callback', lambda: bridge('callback'))
    app.register_blueprint(routes)

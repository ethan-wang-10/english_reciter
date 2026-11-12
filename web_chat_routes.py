"""Chat HTTP handlers with explicit application services."""
from __future__ import annotations

from flask import Blueprint


def register_routes(app, services):
    blueprint = Blueprint('chat', __name__)

    @blueprint.route('/api/chat/stream-token', methods=['POST'])
    @services.token_required
    def api_chat_stream_token(username):
        """用登录 token 换取短期 SSE token；SSE URL 不再携带长期 bearer token。"""
        if not services._rate_allow(f'chat_stream_token:{username}', services._RATE_MAX_CHAT_STREAM_TOKEN):
            return (services.jsonify({'error': '聊天连接过于频繁，请稍后再试'}), 429)
        login = getattr(services.g, 'login_username', username)
        return (services.jsonify({'stream_token': services.create_chat_stream_token(login), 'expires_in': 120}), 200)

    @blueprint.route('/api/chat/messages', methods=['GET'])
    @services.token_required
    def api_chat_messages_get(username):
        if not services._rate_allow(f'chat_get:{username}', services._RATE_MAX_CHAT_GET):
            return (services.jsonify({'error': '聊天拉取过于频繁，请稍后再试'}), 429)
        before_id = (services.request.args.get('before_id') or '').strip() or None
        after_id = (services.request.args.get('after_id') or '').strip() or None
        try:
            limit = int(services.request.args.get('limit') or 50)
        except ValueError:
            return (services.jsonify({'error': '参数 limit 无效'}), 400)
        try:
            msgs = services.chat_room.get_messages(before_id=before_id, after_id=after_id, limit=limit)
        except OSError as e:
            services.logger.warning('聊天历史读取失败: %s', e)
            return (services.jsonify({'error': '聊天记录不可用'}), 503)
        except Exception as e:
            services.logger.exception('聊天历史读取异常: %s', e)
            return (services.jsonify({'error': '服务器内部错误'}), 500)
        return (services.jsonify({'messages': msgs}), 200)

    @blueprint.route('/api/chat/messages', methods=['POST'])
    @services.token_required
    def api_chat_messages_post(username):
        if not services._rate_allow(f'chat_post:{username}', services._RATE_MAX_CHAT_POST):
            return (services.jsonify({'error': '发送过于频繁，请稍后再试'}), 429)
        data = services.request.get_json(silent=True) or {}
        body = services._chat_sanitize_body(data.get('body') or '')
        if not body:
            return (services.jsonify({'error': '消息不能为空'}), 400)
        valid = services._chat_mentionable_usernames()
        mentions = services._chat_extract_mentions(body, valid)
        try:
            msg = services.chat_room.append_message(username, body, mentions)
        except OSError as e:
            services.logger.warning('聊天写入失败: %s', e)
            return (services.jsonify({'error': '聊天服务不可用'}), 503)
        except Exception as e:
            services.logger.exception('聊天写入异常: %s', e)
            return (services.jsonify({'error': '服务器内部错误'}), 500)
        return (services.jsonify(msg), 201)

    @blueprint.route('/api/chat/users', methods=['GET'])
    @services.token_required
    def api_chat_users_suggest(username):
        q = (services.request.args.get('q') or '').strip().lower()
        users = services.load_users()
        out: services.List[str] = []
        for u in sorted(users.keys()):
            if len(out) >= 20:
                break
            row = users.get(u)
            if not services._user_row_is_enabled(row):
                continue
            if services.is_parent_user_record(row):
                continue
            if not q or u.lower().startswith(q):
                out.append(u)
        return (services.jsonify({'users': out}), 200)

    @blueprint.route('/api/chat/stream')
    def api_chat_stream():
        if services._verify_chat_stream_auth() is None:
            return (services.jsonify({'error': '需要有效登录'}), 401)
        if not services._env_flag('CHAT_SSE_ENABLED', False):
            return (services.jsonify({'error': '实时长连接未启用，请使用消息轮询'}), 503)
        return services.Response(services.stream_with_context(services.chat_room.sse_generator()), mimetype='text/event-stream', headers={'Cache-Control': 'no-cache', 'Connection': 'keep-alive', 'X-Accel-Buffering': 'no'})

    app.register_blueprint(blueprint)
    return {
        'api_chat_stream_token': api_chat_stream_token,
        'api_chat_messages_get': api_chat_messages_get,
        'api_chat_messages_post': api_chat_messages_post,
        'api_chat_users_suggest': api_chat_users_suggest,
        'api_chat_stream': api_chat_stream,
    }

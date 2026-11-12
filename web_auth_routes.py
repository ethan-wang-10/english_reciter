"""Auth HTTP handlers with explicit application services."""
from __future__ import annotations

from flask import Blueprint


def register_routes(app, services):
    blueprint = Blueprint('auth', __name__)

    @blueprint.route('/api/auth/register', methods=['POST'])
    def register():
        """用户注册"""
        try:
            if not services._rate_allow(f'reg:{services._client_ip()}', services._RATE_MAX_REGISTER):
                return (services.jsonify({'error': '请求过于频繁，请稍后再试'}), 429)
            data = services.request.get_json(silent=True)
            if not isinstance(data, dict):
                return (services.jsonify({'error': '无效的JSON数据'}), 400)
            username = data.get('username', '').strip()
            password = data.get('password', '').strip()
            try:
                email = services.normalize_email(data.get('email'))
            except ValueError as exc:
                return (services.jsonify({'error': str(exc)}), 400)
            if not username or not password:
                return (services.jsonify({'error': '用户名和密码不能为空'}), 400)
            if not services.is_valid_username(username):
                return (services.jsonify({'error': '用户名须为3-32位字母、数字或下划线'}), 400)
            if len(password) < 6:
                return (services.jsonify({'error': '密码至少6个字符'}), 400)
            invite_code = (data.get('invite_code') or '').strip()
            if not invite_code:
                return (services.jsonify({'error': '请填写邀请码'}), 400)
            ok, err = services.register_user_with_invite(username, password, email, invite_code)
            if ok:
                token = services.create_token(username)
                return (services.jsonify({'username': username, 'email': email, 'created_at': services.china_now_iso(timespec='seconds'), 'access_token': token, 'token_type': 'bearer', **services._auth_session_payload(username)}), 201)
            return (services.jsonify({'error': err or '注册失败'}), 400)
        except Exception as e:
            services.logger.error(f'注册失败: {e}')
            return (services.jsonify({'error': '服务器内部错误'}), 500)

    @blueprint.route('/api/auth/forgot-password', methods=['POST'])
    def forgot_password():
        """按邮箱发送一次性重置链接；无论邮箱是否存在均返回统一结果。"""
        if not services._rate_allow(f'forgot_password:{services._client_ip()}', services._RATE_MAX_FORGOT_PASSWORD):
            return (services.jsonify({'error': '请求过于频繁，请稍后再试'}), 429)
        data = services.request.get_json(silent=True) or {}
        try:
            email = services.normalize_email(data.get('email'))
        except ValueError as exc:
            return (services.jsonify({'error': str(exc)}), 400)
        if not email:
            return (services.jsonify({'error': '请填写邮箱'}), 400)
        generic = {'message': '如果该邮箱已绑定账号，重置邮件将在几分钟内发送'}
        try:
            services._smtp_config()
            ttl_minutes, cooldown_seconds = services._password_reset_policy()
            public_origin = services._public_origin()
        except RuntimeError as exc:
            services.logger.error('无法发送密码重置邮件: %s', exc)
            return (services.jsonify({'error': '邮件服务暂不可用，请联系管理员'}), 503)
        owner = services.get_email_owner(email)
        row = services.get_user(owner) if owner else None
        if not services._user_row_is_enabled(row):
            return (services.jsonify(generic), 200)
        login_username = owner
        cooldown_token = None
        if cooldown_seconds > 0:
            cooldown_token = services.create_session_if_absent(services.SESSION_KIND_PASSWORD_RESET_COOLDOWN, login_username, services.timedelta(seconds=cooldown_seconds))
            if not cooldown_token:
                return (services.jsonify(generic), 200)
        services.revoke_principal(services.SESSION_KIND_PASSWORD_RESET, login_username)
        reset_token = services._db_create_auth_session(services.SESSION_KIND_PASSWORD_RESET, login_username, services.timedelta(minutes=ttl_minutes))
        reset_url = f"{public_origin}/?reset_token={services.quote(reset_token, safe='')}"
        try:
            services._send_password_reset_email(email, reset_url, ttl_minutes)
        except Exception as exc:
            services.revoke_token(services.SESSION_KIND_PASSWORD_RESET, reset_token)
            if cooldown_token:
                services.revoke_token(services.SESSION_KIND_PASSWORD_RESET_COOLDOWN, cooldown_token)
            services.logger.exception('发送密码重置邮件失败: %s', exc)
        return (services.jsonify(generic), 200)

    @blueprint.route('/api/auth/reset-password', methods=['POST'])
    def reset_password():
        if not services._rate_allow(f'reset_password:{services._client_ip()}', services._RATE_MAX_RESET_PASSWORD):
            return (services.jsonify({'error': '请求过于频繁，请稍后再试'}), 429)
        data = services.request.get_json(silent=True) or {}
        reset_token = str(data.get('token') or '').strip()
        password = str(data.get('password') or '').strip()
        password_confirm = str(data.get('password_confirm') or '').strip()
        if len(password) < 6:
            return (services.jsonify({'error': '密码至少6个字符'}), 400)
        if password != password_confirm:
            return (services.jsonify({'error': '两次输入的密码不一致'}), 400)
        login_username = services.consume_session(reset_token, services.SESSION_KIND_PASSWORD_RESET)
        if not login_username:
            return (services.jsonify({'error': '重置链接无效或已过期，请重新申请'}), 400)

        def _set_password(users: Dict[str, Any]) -> bool:
            row = users.get(login_username)
            if not isinstance(row, dict):
                return False
            row['password_hash'] = services.hash_password(password)
            return True
        if not services.mutate_users(_set_password, usernames=[login_username]):
            return (services.jsonify({'error': '重置链接无效或已过期，请重新申请'}), 400)
        services.revoke_principal(services.SESSION_KIND_PASSWORD_RESET, login_username)
        services._revoke_user_tokens(login_username)
        services.logger.info('用户通过邮箱重置密码: %s', login_username)
        return (services.jsonify({'message': '密码已重置，请使用新密码登录'}), 200)

    @blueprint.route('/api/auth/login', methods=['POST'])
    def login():
        """用户登录"""
        try:
            if not services._rate_allow(f'login:{services._client_ip()}', services._RATE_MAX_LOGIN):
                return (services.jsonify({'error': '登录尝试过多，请稍后再试'}), 429)
            if services.request.is_json:
                data = services.request.get_json(silent=True)
                if not isinstance(data, dict):
                    return (services.jsonify({'error': '无效的JSON数据'}), 400)
                username = data.get('username', '').strip()
                password = data.get('password', '').strip()
            else:
                username = services.request.form.get('username', '').strip()
                password = services.request.form.get('password', '').strip()
            if not username or not password:
                return (services.jsonify({'error': '用户名和密码不能为空'}), 400)
            if services.verify_user(username, password):
                urow = services.get_user(username)
                if not isinstance(urow, dict) or urow.get('enabled', True) is False:
                    return (services.jsonify({'error': '账号已停用，请联系管理员'}), 403)
                if isinstance(urow, dict) and services.is_parent_user_record(urow):
                    child = (urow.get('child_username') or '').strip()
                    if not child or not services.is_valid_username(child):
                        return (services.jsonify({'error': '家长账户配置错误'}), 403)
                    child_row = services.get_user(child)
                    if not isinstance(child_row, dict) or child_row.get('enabled', True) is False:
                        return (services.jsonify({'error': '学生账号已停用，无法以家长身份登录'}), 403)
                token = services.create_token(username)
                body = {'access_token': token, 'token_type': 'bearer', 'username': username, **services._auth_session_payload(username)}
                return (services.jsonify(body), 200)
            return (services.jsonify({'error': '用户名或密码错误'}), 401)
        except Exception as e:
            services.logger.error(f'登录失败: {e}')
            return (services.jsonify({'error': '服务器内部错误'}), 500)

    @blueprint.route('/api/auth/logout', methods=['POST'])
    @services.token_required
    def logout(username):
        """用户退出"""
        login = getattr(services.g, 'login_username', username)
        services._revoke_user_tokens(login)
        return (services.jsonify({'message': '已退出登录'}), 200)

    @blueprint.route('/api/auth/session', methods=['GET'])
    @services.token_required
    def auth_session(username):
        """刷新页后恢复 is_parent / child_username。"""
        login = getattr(services.g, 'login_username', username)
        return (services.jsonify(services._auth_session_payload(login)), 200)

    @blueprint.route('/api/auth/broadcast/ack', methods=['POST'])
    @services.token_required
    def ack_system_broadcast(username):
        """用户确认已读当前系统广播，之后不再展示同一条。"""
        login = getattr(services.g, 'login_username', username)
        data = services.request.get_json(silent=True) or {}
        bid = (data.get('id') or '').strip()
        if not bid:
            return (services.jsonify({'error': '缺少广播 id'}), 400)
        with services._SYSTEM_BROADCAST_LOCK:
            doc = services._read_system_broadcast_unlocked()
        cur = (doc.get('id') or '').strip()
        if bid != cur:
            return (services.jsonify({'error': '无效或过期的广播'}), 400)
        lock = services._user_mutex(login)
        with lock:
            services._write_user_broadcast_ack(login, bid)
        return (services.jsonify({'ok': True}), 200)

    @blueprint.route('/api/auth/parent-password', methods=['PATCH'])
    @services.token_required
    def patch_parent_password(username):
        """家长修改自己的登录密码（不影响学生账号）。"""
        if not getattr(services.g, 'is_parent', False):
            return (services.jsonify({'error': '仅家长账户可修改'}), 403)
        data = services.request.get_json(silent=True) or {}
        p1 = (data.get('password') or '').strip()
        p2 = (data.get('password_confirm') or '').strip()
        if len(p1) < 6:
            return (services.jsonify({'error': '密码至少6个字符'}), 400)
        if p1 != p2:
            return (services.jsonify({'error': '两次输入的密码不一致'}), 400)
        login = getattr(services.g, 'login_username', username)

        def _set_password(users: Dict[str, Any]) -> bool:
            if login not in users:
                return False
            users[login]['password_hash'] = services.hash_password(p1)
            return True
        if not services.mutate_users(_set_password, usernames=[login]):
            return (services.jsonify({'error': '用户不存在'}), 404)
        services.logger.info('家长账户修改密码: login=%s', login)
        return (services.jsonify({'message': '密码已更新'}), 200)

    @blueprint.route('/api/user/learning-settings', methods=['GET', 'PATCH'])
    @services.token_required
    def user_learning_settings(username):
        """家长读取或调整关联学生的每日学习任务上限。"""
        if not getattr(services.g, 'is_parent', False):
            return (services.jsonify({'error': '仅家长账户可调整学习任务上限'}), 403)
        if services.request.method == 'PATCH':
            data = services.request.get_json(silent=True)
            if not isinstance(data, dict):
                return (services.jsonify({'error': '无效的JSON数据'}), 400)
            try:
                daily_review_limit = data['daily_review_limit']
            except KeyError:
                return (services.jsonify({'error': '请填写每日任务上限'}), 400)
            if not isinstance(daily_review_limit, int) or isinstance(daily_review_limit, bool):
                return (services.jsonify({'error': '学习任务上限须为整数'}), 400)
            if not 1 <= daily_review_limit <= services.MAX_DAILY_REVIEW_LIMIT:
                return (services.jsonify({'error': f'每日任务上限须在 1–{services.MAX_DAILY_REVIEW_LIMIT} 之间'}), 400)
            with services._user_mutex(username):
                with services._user_learning_interprocess_lock(username):
                    settings = services._read_user_learning_settings(username)
                    settings['daily_review_limit'] = daily_review_limit
                    settings.pop('daily_new_word_limit', None)
                    services._write_user_learning_settings(username, settings)
            services._invalidate_user_reciter_cache(username)
            services.logger.info('家长更新学生学习任务上限: student=%s daily=%s', username, daily_review_limit)
        config = services._build_user_config(username)
        return (services.jsonify({'daily_review_limit': config.DAILY_REVIEW_LIMIT, 'default_daily_review_limit': services.DEFAULT_DAILY_REVIEW_LIMIT, 'max_daily_review_limit': services.MAX_DAILY_REVIEW_LIMIT, 'new_words_are_automatic': True, 'minimum_review_share_percent': 60, 'applies_from_next_task': True}), 200)

    @blueprint.route('/api/user/settings', methods=['GET'])
    @services.token_required
    def get_user_settings(username):
        """设置页汇总：游戏化、月度奖池、挑战列表、头像。"""
        try:
            with services.user_reciter_session(username) as reciter:
                mastered_n = len(reciter.mastered_words)
            pkw, pkm = services._pk_stats_for_gamification(username)
            prof = services.gamification_mod.public_profile(services.DATA_DIR, username, mastered_words=mastered_n, pk_wins=pkw, pk_matches=pkm)
            pool = services.enrich_monthly_pool_with_avatars(services.challenges_mod.get_monthly_pool_state(services.DATA_DIR, username))
            duels = services.challenges_mod.list_duels_for_user(services.DATA_DIR, username)
            av = services.user_avatar_disk_path(username)
            prof['avatar_url'] = f'/api/user/avatar/{username}' if av else None
            prof.update(services.invite_quota_payload(services.get_user(username) or {}))
            with services._locked_invite_storage():
                prof['invites'] = services._list_invites_created_by(username)
            prof['invite_register_url'] = services._invite_registration_url()
            prof['monthly_pool'] = pool
            prof['duels'] = duels
            prof['wager_tiers'] = list(services.challenges_mod.WAGER_TIERS)
            prof['stake_safety_reserve_xp'] = services.challenges_mod.STAKE_SAFETY_RESERVE_XP
            prof['duel_opponents'] = services.list_challenge_opponent_usernames(username)
            return (services.jsonify(prof), 200)
        except services.PersistentStateLoadError:
            raise
        except Exception as e:
            services.logger.error(f'获取用户设置失败: {e}')
            return (services.jsonify({'error': '服务器内部错误'}), 500)

    @blueprint.route('/api/user/email', methods=['GET', 'PATCH'])
    @services.token_required
    def user_email(username):
        """读取或更新当前登录身份的邮箱；家长账号不会写到关联学生上。"""
        login_username = getattr(services.g, 'login_username', username)
        if services.request.method == 'GET':
            row = services.get_user(login_username)
            if not isinstance(row, dict):
                return (services.jsonify({'error': '用户不存在'}), 404)
            return (services.jsonify({'email': row.get('email') or ''}), 200)
        data = services.request.get_json(silent=True) or {}
        try:
            email = services.normalize_email(data.get('email'))
        except ValueError as exc:
            return (services.jsonify({'error': str(exc)}), 400)

        if email and services.get_email_owner(email, exclude=login_username):
            return (services.jsonify({'error': '该邮箱已被其他账号使用'}), 409)

        def _set_email(users: Dict[str, Any]) -> Tuple[bool, str]:
            row = users.get(login_username)
            if not isinstance(row, dict):
                return (False, '用户不存在')
            if email and services._email_owner(users, email, exclude=login_username):
                return (False, '该邮箱已被其他账号使用')
            if email:
                row['email'] = email
            else:
                row.pop('email', None)
            return (True, '')
        try:
            ok, error = services.mutate_users(_set_email, usernames=[login_username])
        except services.EmailAlreadyUsedError:
            return (services.jsonify({'error': '该邮箱已被其他账号使用'}), 409)
        if not ok:
            return (services.jsonify({'error': error}), 404 if error == '用户不存在' else 409)
        return (services.jsonify({'email': email or '', 'message': '邮箱已保存' if email else '邮箱已移除'}), 200)

    @blueprint.route('/api/user/avatar-meta', methods=['GET'])
    @services.token_required
    def get_user_avatar_meta(username):
        """轻量头像元信息；避免顶栏头像刷新调用完整 settings。"""
        av = services.user_avatar_disk_path(username)
        return (services.jsonify({'avatar_url': f'/api/user/avatar/{username}' if av else None}), 200)

    @blueprint.route('/api/user/avatar/<uname>', methods=['GET'])
    def get_user_avatar_file(uname):
        """公开读取头像（供 img src）。可选 ?w=64 等生成小尺寸 WebP，减轻传输。"""
        path = services.user_avatar_disk_path(uname)
        if not path:
            return ('', 404)
        wq = services.request.args.get('w', type=int)
        if wq is not None and services.PILImage is not None and (services.AVATAR_THUMB_MIN <= wq <= services.AVATAR_THUMB_MAX):
            try:
                im = services.PILImage.open(path)
                im = services._avatar_pil_to_rgb(im)
                im = im.resize((wq, wq), services.PILImage.LANCZOS)
                buf = services.BytesIO()
                im.save(buf, 'WEBP', quality=services.AVATAR_THUMB_WEBP_QUALITY, method=4)
                buf.seek(0)
                return services.send_file(buf, mimetype='image/webp', max_age=86400)
            except Exception as e:
                services.logger.warning('头像缩略图生成失败，回退原文件: %s', e)
        return services.send_file(path, max_age=3600)

    @blueprint.route('/api/user/avatar', methods=['POST'])
    @services.token_required
    @services.parent_forbidden
    def post_user_avatar(username):
        """上传头像：有 Pillow 时统一为压缩 WebP；否则原样保存为单个 avatar.<ext>（覆盖旧文件）。"""
        if 'file' not in services.request.files:
            return (services.jsonify({'error': '缺少 file 字段'}), 400)
        f = services.request.files['file']
        if not f or not f.filename:
            return (services.jsonify({'error': '未选择文件'}), 400)
        ct = (f.mimetype or '').lower()
        if ct not in ('image/jpeg', 'image/png', 'image/webp'):
            return (services.jsonify({'error': '仅支持 JPEG、PNG、WebP'}), 400)
        ext_map = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp'}
        user_dir = services.DATA_DIR / username
        user_dir.mkdir(parents=True, exist_ok=True)
        for old in user_dir.glob('avatar.*'):
            try:
                old.unlink()
            except OSError:
                pass
        if services.PILImage is not None:
            dst = user_dir / 'avatar.webp'
            try:
                services._save_user_avatar_webp(f.stream, dst)
            except Exception as e:
                services.logger.error(f'保存头像失败: {e}')
                try:
                    if dst.exists():
                        dst.unlink()
                except OSError:
                    pass
                return (services.jsonify({'error': '无法解析或保存图片'}), 400)
        else:
            dst = user_dir / f'avatar{ext_map[ct]}'
            services.logger.warning('Pillow 未安装，头像以原图保存；建议执行 pip install -r requirements-simple.txt')
            try:
                f.stream.seek(0)
            except (OSError, AttributeError, TypeError):
                pass
            try:
                f.save(dst)
            except OSError as e:
                services.logger.error(f'保存头像失败: {e}')
                return (services.jsonify({'error': '保存失败'}), 500)
        return (services.jsonify({'ok': True, 'avatar_url': f'/api/user/avatar/{username}'}), 200)

    @blueprint.route('/api/user/avatar', methods=['DELETE'])
    @services.token_required
    @services.parent_forbidden
    def delete_user_avatar(username):
        path = services.user_avatar_disk_path(username)
        if path:
            try:
                path.unlink()
            except OSError:
                pass
        return (services.jsonify({'ok': True, 'avatar_url': None}), 200)

    @blueprint.route('/api/user/invites', methods=['GET'])
    @services.token_required
    @services.parent_forbidden
    def get_user_invites(username):
        """当前用户创建的邀请码列表与剩余额度（不含明文）。"""
        u = services.get_user(username)
        if not isinstance(u, dict) or services.is_parent_user_record(u):
            return (services.jsonify({'error': '用户不存在'}), 404)
        with services._locked_invite_storage():
            invites = services._list_invites_created_by(username)
        return (services.jsonify({**services.invite_quota_payload(u), 'invites': invites, 'invite_register_url': services._invite_registration_url()}), 200)

    @blueprint.route('/api/user/invites/unused', methods=['GET'])
    @services.token_required
    @services.parent_forbidden
    def get_unused_user_invites(username):
        """Return only this user's unused invite codes, without allowing intermediary caching."""
        u = services.get_user(username)
        if not isinstance(u, dict) or services.is_parent_user_record(u):
            return (services.jsonify({'error': '用户不存在'}), 404)
        with services._locked_invite_storage():
            invites = services._list_unused_invites_created_by(username)
        response = services.jsonify({**services.invite_quota_payload(u), 'invites': invites, 'invite_register_url': services._invite_registration_url()})
        response.headers['Cache-Control'] = 'private, no-store'
        return (response, 200)

    @blueprint.route('/api/user/invites', methods=['POST'])
    @services.token_required
    @services.parent_forbidden
    def create_user_invite(username):
        """当前用户生成一次性邀请码（每个用户默认最多 15 个）。"""
        with services._locked_invite_storage():
            data = services.load_invites()
            invites = data.setdefault('invites', [])
            plain = services._fresh_invite_code(invites).strip().upper()
            try:
                ciphertext = services._encrypt_invite_code_for_storage(plain)
            except RuntimeError:
                return (services.jsonify({'error': '邀请码安全存储暂不可用，请稍后重试'}), 503)
            inv_id = str(services.uuid.uuid4())
            quota_after: services.Optional[dict] = None
            entry = {'id': inv_id, 'code_hash': services._hash_invite_code(plain), 'code_ciphertext': ciphertext, 'created_at': services.china_now_iso(timespec='seconds'), 'created_by': username, 'created_by_kind': 'user', 'used_at': None, 'used_by': None}

            def _consume_quota(users: Dict[str, Any]) -> Tuple[bool, int, str, Optional[dict]]:
                u = users.get(username)
                if not isinstance(u, dict):
                    return (False, 404, '用户不存在', None)
                if services.is_parent_user_record(u):
                    return (False, 403, '家长账户不能生成邀请码', None)
                limit = services.invite_quota_limit(u)
                used = services.invite_quota_used(u)
                if used >= limit:
                    return (False, 400, '邀请码次数已用完，请联系管理员重置', services.invite_quota_payload(u))
                u['invite_quota_limit'] = limit
                u['invite_quota_used'] = used + 1
                invites.append(entry)
                try:
                    services.save_invites(data)
                except Exception:
                    invites.remove(entry)
                    raise
                return (True, 201, '', services.invite_quota_payload(u))
            try:
                ok, status_code, err, quota_after = services.mutate_users(_consume_quota, usernames=[username])
            except Exception:
                if entry in invites:
                    invites.remove(entry)
                    try:
                        services.save_invites(data)
                    except Exception:
                        services.logger.exception('生成失败后恢复邀请码列表失败: invite_id=%s', inv_id)
                raise
            if not ok:
                return (services.jsonify({'error': err, **(quota_after or {})}), status_code)
        services.logger.info('用户生成邀请码: user=%s id=%s', username, inv_id)
        return (services.jsonify({'id': inv_id, 'invite_code': plain, 'invite_register_url': services._invite_registration_url(), 'hint': '未使用前可在邀请窗口中再次选择', **(quota_after or {})}), 201)

    @blueprint.route('/api/user/plan', methods=['GET'])
    @services.token_required
    def get_user_plan_api(username):
        """获取当前用户套餐类型。"""
        return (services.jsonify({'plan': services.get_user_plan(username), 'article_ai_extract_available': bool(services.get_deepseek_api_key()), 'article_ai_extract_enabled': services._article_ai_extract_enabled()}), 200)

    app.register_blueprint(blueprint)
    return {
        'register': register,
        'forgot_password': forgot_password,
        'reset_password': reset_password,
        'login': login,
        'logout': logout,
        'auth_session': auth_session,
        'ack_system_broadcast': ack_system_broadcast,
        'patch_parent_password': patch_parent_password,
        'user_learning_settings': user_learning_settings,
        'get_user_settings': get_user_settings,
        'user_email': user_email,
        'get_user_avatar_meta': get_user_avatar_meta,
        'get_user_avatar_file': get_user_avatar_file,
        'post_user_avatar': post_user_avatar,
        'delete_user_avatar': delete_user_avatar,
        'get_user_invites': get_user_invites,
        'get_unused_user_invites': get_unused_user_invites,
        'create_user_invite': create_user_invite,
        'get_user_plan_api': get_user_plan_api,
    }

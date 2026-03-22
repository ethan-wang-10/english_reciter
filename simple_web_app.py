#!/usr/bin/env python3
"""
简化版Web应用 - 智能英语背诵系统
使用Flask替代FastAPI，简化依赖和架构
支持多用户、跨平台访问
"""

import os
import json
import re
import hashlib
import secrets
import shutil
import subprocess
import threading
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, date
from pathlib import Path
from functools import wraps
from typing import Dict, Generator, List, Optional, Tuple
from time import time
import uuid

from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash

# 导入核心功能
from reciter import (
    WordReciter,
    Config,
    get_logger,
)
import gamification as gamification_mod

# 日志配置
logger = get_logger(__name__)

# Flask应用
app = Flask(__name__, static_folder='static')
_secret = os.getenv("SECRET_KEY")
if os.getenv("FLASK_ENV", "").lower() == "production" and not _secret:
    raise RuntimeError(
        "生产环境必须设置环境变量 SECRET_KEY（例如在 docker-compose 或 systemd 中配置）"
    )
app.secret_key = _secret or secrets.token_urlsafe(32)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB上传限制
CORS(app, supports_credentials=True)

# 用户名：防止路径穿越与非法目录名，仅允许字母数字下划线
USERNAME_PATTERN = re.compile(r'^[a-zA-Z0-9_]{3,32}$')

# 数据目录
DATA_DIR = Path("user_data_simple")
DATA_DIR.mkdir(exist_ok=True)

# Token存储（内存中，重启后失效）
# 实际应用中应使用数据库或Redis
user_tokens: Dict[str, str] = {}  # token -> username
token_expiry: Dict[str, datetime] = {}  # token -> expiry time

# 管理员会话（与学生 token 隔离）
admin_tokens: Dict[str, str] = {}  # token -> admin 用户名
admin_token_expiry: Dict[str, datetime] = {}

# 登录/注册简单限流（按 IP，内存存储）
_rate_buckets: Dict[str, List[float]] = defaultdict(list)
_RATE_WINDOW_SEC = 60
_RATE_MAX_LOGIN = 20
_RATE_MAX_REGISTER = 10
_RATE_MAX_ADMIN_LOGIN = 10

INVITES_FILE = DATA_DIR / "invites.json"
_invites_lock = threading.Lock()

# 每用户背诵器缓存 + 互斥锁（避免并发写 JSON 与重复初始化）
_reciter_registry_lock = threading.Lock()
_user_reciter_locks: Dict[str, threading.Lock] = {}
_user_reciter_cache: Dict[str, WordReciter] = {}


def _client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _rate_allow(bucket_key: str, max_events: int) -> bool:
    now = time()
    window: List[float] = _rate_buckets[bucket_key]
    window[:] = [t for t in window if now - t < _RATE_WINDOW_SEC]
    if len(window) >= max_events:
        return False
    window.append(now)
    return True


def is_valid_username(username: str) -> bool:
    return bool(username and USERNAME_PATTERN.fullmatch(username))


def _is_legacy_sha256_hex(stored: str) -> bool:
    return len(stored) == 64 and all(c in "0123456789abcdefABCDEF" for c in stored)


# ==================== 工具函数 ====================

def hash_password(password: str) -> str:
    """使用 Werkzeug 安全哈希（含盐）。"""
    return generate_password_hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """验证密码；兼容旧版 SHA256 无盐哈希。"""
    try:
        if check_password_hash(password_hash, password):
            return True
    except (ValueError, TypeError):
        pass
    if _is_legacy_sha256_hex(password_hash):
        return hashlib.sha256(password.encode()).hexdigest() == password_hash.lower()
    return False


def _hash_invite_code(plain: str) -> str:
    return hashlib.sha256(plain.strip().encode('utf-8')).hexdigest()


def load_users() -> dict:
    """加载所有用户数据；为旧数据补充 enabled 字段。"""
    users_file = DATA_DIR / "users.json"
    if not users_file.exists():
        return {}

    try:
        with open(users_file, 'r', encoding='utf-8') as f:
            users = json.load(f)
    except Exception as e:
        logger.error(f"加载用户数据失败: {e}")
        return {}

    changed = False
    for _uname, u in users.items():
        if isinstance(u, dict) and 'enabled' not in u:
            u['enabled'] = True
            changed = True
    if changed:
        save_users(users)
    return users


def save_users(users: dict) -> None:
    """保存用户数据"""
    users_file = DATA_DIR / "users.json"
    try:
        with open(users_file, 'w', encoding='utf-8') as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存用户数据失败: {e}")

def load_invites() -> dict:
    """加载邀请码列表。"""
    if not INVITES_FILE.exists():
        return {"invites": []}
    try:
        with open(INVITES_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"加载邀请码失败: {e}")
        return {"invites": []}


def save_invites(data: dict) -> None:
    """保存邀请码文件。"""
    try:
        with open(INVITES_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存邀请码失败: {e}")


def register_user_with_invite(
    username: str,
    password: str,
    email: Optional[str],
    invite_code: str,
) -> Tuple[bool, str]:
    """
    使用一次性邀请码注册用户。
    返回 (是否成功, 错误信息)。
    """
    if not is_valid_username(username):
        return False, '用户名须为3-32位字母、数字或下划线'

    code_hash = _hash_invite_code(invite_code)
    with _invites_lock:
        users = load_users()
        if username in users:
            return False, '用户名已存在'

        data = load_invites()
        invites = data.get('invites', [])
        matched = None
        for inv in invites:
            if inv.get('code_hash') == code_hash and inv.get('used_at') is None:
                matched = inv
                break
        if not matched:
            return False, '邀请码无效或已使用'

        password_hash = hash_password(password)
        users[username] = {
            'password_hash': password_hash,
            'email': email,
            'created_at': datetime.now().isoformat(),
            'enabled': True,
        }
        matched['used_at'] = datetime.now().isoformat()
        matched['used_by'] = username

        user_dir = DATA_DIR / username
        user_dir.mkdir(exist_ok=True)

        save_users(users)
        save_invites(data)
        logger.info("新用户注册: %s (invite_id=%s)", username, matched.get('id'))
        return True, ''


def is_user_enabled(username: str) -> bool:
    users = load_users()
    u = users.get(username)
    if not u or not isinstance(u, dict):
        return False
    return u.get('enabled', True) is not False


def _revoke_user_tokens(username: str) -> None:
    to_remove = [t for t, u in user_tokens.items() if u == username]
    for t in to_remove:
        user_tokens.pop(t, None)
        token_expiry.pop(t, None)


def _invalidate_user_reciter_cache(username: str) -> None:
    with _reciter_registry_lock:
        _user_reciter_cache.pop(username, None)


def verify_user(username: str, password: str) -> bool:
    """验证用户；若仍为旧版哈希则自动升级为 Werkzeug 哈希。"""
    users = load_users()

    if username not in users:
        return False

    stored = users[username]["password_hash"]
    if not verify_password(password, stored):
        return False

    if _is_legacy_sha256_hex(stored):
        users[username]["password_hash"] = hash_password(password)
        save_users(users)
        logger.info("用户 %s 的密码哈希已升级为安全格式", username)

    return True

def create_token(username: str) -> str:
    """创建访问令牌"""
    # 清理过期的token
    now = datetime.now()
    expired_tokens = [t for t, exp in token_expiry.items() if exp < now]
    for token in expired_tokens:
        user_tokens.pop(token, None)
        token_expiry.pop(token, None)
    
    # 生成新token
    token = secrets.token_urlsafe(32)
    user_tokens[token] = username
    token_expiry[token] = now + timedelta(hours=24)  # 24小时有效期
    
    return token

def verify_token(token: str) -> Optional[str]:
    """验证令牌，返回用户名"""
    if not token:
        return None
    
    # 检查token是否存在且未过期
    if token in user_tokens:
        expiry = token_expiry.get(token)
        if expiry and expiry >= datetime.now():
            return user_tokens[token]
        else:
            # Token已过期，清理
            user_tokens.pop(token, None)
            token_expiry.pop(token, None)
    
    return None


def _cleanup_admin_tokens() -> None:
    now = datetime.now()
    expired = [t for t, exp in admin_token_expiry.items() if exp < now]
    for t in expired:
        admin_tokens.pop(t, None)
        admin_token_expiry.pop(t, None)


def create_admin_token() -> str:
    """签发管理员会话 token（与学生 token 隔离）。"""
    _cleanup_admin_tokens()
    now = datetime.now()
    token = secrets.token_urlsafe(32)
    admin_name = os.getenv('ADMIN_USERNAME', '').strip() or 'admin'
    admin_tokens[token] = admin_name
    admin_token_expiry[token] = now + timedelta(hours=8)
    return token


def verify_admin_token(token: str) -> bool:
    if not token:
        return False
    _cleanup_admin_tokens()
    if token not in admin_tokens:
        return False
    exp = admin_token_expiry.get(token)
    if not exp or exp < datetime.now():
        admin_tokens.pop(token, None)
        admin_token_expiry.pop(token, None)
        return False
    return True


def admin_configured() -> bool:
    """是否已配置管理员账号（环境变量）。"""
    au = os.getenv('ADMIN_USERNAME', '').strip()
    if not au:
        return False
    if os.getenv('ADMIN_PASSWORD_HASH', '').strip():
        return True
    return bool(os.getenv('ADMIN_PASSWORD', ''))


def verify_admin_credentials(username: str, password: str) -> bool:
    if not admin_configured():
        return False
    if username != os.getenv('ADMIN_USERNAME', '').strip():
        return False
    pwd_hash = os.getenv('ADMIN_PASSWORD_HASH', '').strip()
    if pwd_hash:
        return verify_password(password, pwd_hash)
    plain = os.getenv('ADMIN_PASSWORD', '')
    if not plain:
        return False
    return secrets.compare_digest(password.encode('utf-8'), plain.encode('utf-8'))


def _learning_data_summary(username: str) -> Dict[str, int]:
    path = DATA_DIR / username / 'learning_data.json'
    if not path.exists():
        return {'pending': 0, 'mastered': 0}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            d = json.load(f)
        return {
            'pending': len(d.get('all_words', [])),
            'mastered': len(d.get('mastered_words', [])),
        }
    except Exception:
        return {'pending': 0, 'mastered': 0}


# ==================== 认证装饰器 ====================

def token_required(f):
    """要求token认证的装饰器"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # 从Authorization头获取token
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({'error': '需要认证'}), 401

        token = auth_header[7:].strip()
        username = verify_token(token)

        if not username:
            return jsonify({'error': '无效或过期的token'}), 401

        if not is_user_enabled(username):
            _revoke_user_tokens(username)
            return jsonify({'error': '账号已停用'}), 403

        # 将用户名传递给路由函数
        return f(username, *args, **kwargs)
    return decorated_function


def admin_required(f):
    """管理员 token。"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({'error': '需要管理员认证'}), 401
        tok = auth_header[7:].strip()
        if not verify_admin_token(tok):
            return jsonify({'error': '无效或过期的管理员会话'}), 401
        return f(*args, **kwargs)
    return decorated_function

# ==================== 用户数据管理 ====================

def _user_mutex(username: str) -> threading.Lock:
    with _reciter_registry_lock:
        if username not in _user_reciter_locks:
            _user_reciter_locks[username] = threading.Lock()
        return _user_reciter_locks[username]


def _build_user_reciter(username: str) -> WordReciter:
    user_dir = DATA_DIR / username
    config_file = user_dir / "config.json"
    config = Config(str(config_file)) if config_file.exists() else Config()
    config.DATA_FILE = str(user_dir / "learning_data.json")
    config.EXAMPLE_DB = str(user_dir / "word_examples.json")
    return WordReciter(config)


@contextmanager
def user_reciter_session(username: str) -> Generator[WordReciter, None, None]:
    """在同用户请求间复用 WordReciter，并以互斥锁序列化读写。"""
    lock = _user_mutex(username)
    with lock:
        if username not in _user_reciter_cache:
            _user_reciter_cache[username] = _build_user_reciter(username)
        reciter = _user_reciter_cache[username]
        reciter.refresh_for_new_day()
        yield reciter


def sanitize_tts_text(text: str, max_len: int = 500) -> str:
    """去除控制字符并限制长度，避免异常输入与命令注入面。"""
    text = (text or "").strip()[:max_len]
    return "".join(ch for ch in text if ch.isprintable() or ch.isspace()).strip()[:max_len]

# ==================== 路由 ====================

@app.route('/')
def index():
    """主页"""
    return send_file('static/index.html')

@app.route('/static/<path:path>')
def send_static(path):
    """静态文件服务"""
    return send_from_directory('static', path)

@app.route('/api/auth/register', methods=['POST'])
def register():
    """用户注册"""
    try:
        if not _rate_allow(f"reg:{_client_ip()}", _RATE_MAX_REGISTER):
            return jsonify({'error': '请求过于频繁，请稍后再试'}), 429

        data = request.get_json()
        if not data:
            return jsonify({'error': '无效的JSON数据'}), 400
        
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        email = data.get('email')
        
        if not username or not password:
            return jsonify({'error': '用户名和密码不能为空'}), 400
        
        if not is_valid_username(username):
            return jsonify({'error': '用户名须为3-32位字母、数字或下划线'}), 400
        
        if len(password) < 6:
            return jsonify({'error': '密码至少6个字符'}), 400

        invite_code = (data.get('invite_code') or '').strip()
        if not invite_code:
            return jsonify({'error': '请填写邀请码'}), 400

        ok, err = register_user_with_invite(username, password, email, invite_code)
        if ok:
            token = create_token(username)
            return jsonify({
                'username': username,
                'email': email,
                'created_at': datetime.now().isoformat(),
                'access_token': token,
                'token_type': 'bearer'
            }), 201
        return jsonify({'error': err or '注册失败'}), 400
    except Exception as e:
        logger.error(f"注册失败: {e}")
        return jsonify({'error': '服务器内部错误'}), 500

@app.route('/api/auth/login', methods=['POST'])
def login():
    """用户登录"""
    try:
        if not _rate_allow(f"login:{_client_ip()}", _RATE_MAX_LOGIN):
            return jsonify({'error': '登录尝试过多，请稍后再试'}), 429

        # 支持表单数据和JSON数据
        if request.content_type == 'application/json':
            data = request.get_json()
            username = data.get('username', '').strip()
            password = data.get('password', '').strip()
        else:
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '').strip()
        
        if not username or not password:
            return jsonify({'error': '用户名和密码不能为空'}), 400

        if verify_user(username, password):
            if not is_user_enabled(username):
                return jsonify({'error': '账号已停用，请联系管理员'}), 403
            token = create_token(username)
            return jsonify({
                'access_token': token,
                'token_type': 'bearer',
                'username': username
            }), 200
        return jsonify({'error': '用户名或密码错误'}), 401
    except Exception as e:
        logger.error(f"登录失败: {e}")
        return jsonify({'error': '服务器内部错误'}), 500

@app.route('/api/auth/logout', methods=['POST'])
@token_required
def logout(username):
    """用户退出"""
    # 清除所有该用户的token
    tokens_to_remove = [t for t, u in user_tokens.items() if u == username]
    for token in tokens_to_remove:
        user_tokens.pop(token, None)
        token_expiry.pop(token, None)
    
    return jsonify({'message': '已退出登录'}), 200


@app.route('/api/gamification', methods=['GET'])
@token_required
def get_gamification(username):
    """XP、等级、连续打卡、成就列表"""
    try:
        with user_reciter_session(username) as reciter:
            mastered_n = len(reciter.mastered_words)
        profile = gamification_mod.public_profile(
            DATA_DIR, username, mastered_words=mastered_n
        )
        return jsonify(profile), 200
    except Exception as e:
        logger.error(f"获取游戏化数据失败: {e}")
        return jsonify({'error': '服务器内部错误'}), 500


@app.route('/api/gamification', methods=['PATCH'])
@token_required
def patch_gamification_settings(username):
    """更新排行榜展示等设置"""
    try:
        data = request.get_json()
        if data is None:
            return jsonify({'error': '无效的JSON数据'}), 400
        opt_in = data.get('leaderboard_opt_in')
        if opt_in is not None and not isinstance(opt_in, bool):
            return jsonify({'error': 'leaderboard_opt_in 须为布尔值'}), 400
        out = gamification_mod.patch_settings(
            DATA_DIR, username, leaderboard_opt_in=opt_in
        )
        return jsonify(out), 200
    except Exception as e:
        logger.error(f"更新游戏化设置失败: {e}")
        return jsonify({'error': '服务器内部错误'}), 500


@app.route('/api/leaderboard', methods=['GET'])
@token_required
def get_leaderboard(username):
    """按总 XP 排序的小伙伴排行榜（仅含开启展示的用户）"""
    try:
        users = load_users()
        enabled = [
            u for u in users
            if isinstance(users.get(u), dict) and is_user_enabled(u)
        ]
        rows = gamification_mod.build_leaderboard(
            DATA_DIR, enabled, viewer=username
        )
        return jsonify({'leaderboard': rows}), 200
    except Exception as e:
        logger.error(f"获取排行榜失败: {e}")
        return jsonify({'error': '服务器内部错误'}), 500


@app.route('/api/words/status', methods=['GET'])
@token_required
def get_status(username):
    """获取学习状态"""
    try:
        with user_reciter_session(username) as reciter:
            all_words = [
                {
                    'english': w.english,
                    'chinese': w.chinese,
                    'success_count': w.success_count,
                    'max_success_count': reciter.config.MAX_SUCCESS_COUNT,
                    'review_round': w.review_round,
                    'review_count': w.review_count,
                    'next_review_date': w.next_review_date.isoformat(),
                    'remaining_days': (w.next_review_date - date.today()).days
                }
                for w in reciter.all_words
            ]

            stats = {
                'total_words': len(all_words),
                'mastered_words': len(reciter.mastered_words),
                'current_round': reciter.current_review_round,
                'avg_review_count': sum(w['review_count'] for w in all_words) / len(all_words) if all_words else 0
            }

            return jsonify({'words': all_words, 'stats': stats}), 200
    except Exception as e:
        logger.error(f"获取状态失败: {e}")
        return jsonify({'error': '服务器内部错误'}), 500

@app.route('/api/words/review', methods=['GET'])
@token_required
def get_review_list(username):
    """获取今日复习列表"""
    try:
        with user_reciter_session(username) as reciter:
            review_list = reciter.get_today_review_list()

            words = [
                {
                    'english': w.english,
                    'chinese': w.chinese,
                    'success_count': w.success_count,
                    'max_success_count': reciter.config.MAX_SUCCESS_COUNT,
                    'review_count': w.review_count,
                    'example': w.example
                }
                for w in review_list
            ]

            return jsonify({'words': words, 'count': len(words)}), 200
    except Exception as e:
        logger.error(f"获取复习列表失败: {e}")
        return jsonify({'error': '服务器内部错误'}), 500

@app.route('/api/words/extra-review', methods=['GET'])
@token_required
def get_extra_review_list(username):
    """今日无待复习时：从全词库按复习次数最少优先、同层随机抽取加练词（默认 5 个）。"""
    try:
        with user_reciter_session(username) as reciter:
            picked = reciter.get_extra_review_words(5)
            words = [
                {
                    'english': w.english,
                    'chinese': w.chinese,
                    'success_count': w.success_count,
                    'max_success_count': reciter.config.MAX_SUCCESS_COUNT,
                    'review_count': w.review_count,
                    'example': w.example,
                }
                for w in picked
            ]
            return jsonify({'words': words, 'count': len(words)}), 200
    except Exception as e:
        logger.error(f"获取加练列表失败: {e}")
        return jsonify({'error': '服务器内部错误'}), 500

@app.route('/api/words/practice', methods=['POST'])
@token_required
def practice_word(username):
    """练习单词"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': '无效的JSON数据'}), 400
        
        word_id = data.get('word_id', '').strip()
        answer = data.get('answer', '').strip()
        # 当日错题巩固轮次：答对不计入掌握进度（success_count）与排期，见前端 wrongRoundNumber>0
        remedial = bool(data.get('remedial'))
        # 无今日待复习时的加练：仅计复习次数，不改变掌握进度与排期
        bonus_practice = bool(data.get('bonus_practice'))

        if not word_id or not answer:
            return jsonify({'error': '单词ID和答案不能为空'}), 400

        with user_reciter_session(username) as reciter:
            word = None
            for w in reciter.all_words:
                if w.english.lower() == word_id.lower():
                    word = w
                    break
            if not word and bonus_practice:
                for w in reciter.mastered_words:
                    if w.english.lower() == word_id.lower():
                        word = w
                        break

            if not word:
                return jsonify({'error': '单词未找到'}), 404

            is_correct = answer.strip().lower() == word.english.lower()
            old_success_count = word.success_count
            old_mastered_count = len(reciter.mastered_words)

            if bonus_practice:
                if is_correct:
                    message = reciter.record_bonus_answer_correct(word)
                else:
                    reciter.record_answer_incorrect(word)
                    message = '❌ 错误，请继续努力！'
            elif is_correct:
                message = reciter.record_answer_correct(word, remedial=remedial)
            else:
                reciter.record_answer_incorrect(word)
                message = '❌ 错误，请继续努力！'
            reciter.save_learning_data(backup=False)

            new_success_count = word.success_count
            mastered_now = len(reciter.mastered_words) > old_mastered_count
            gam_payload = None
            if is_correct:
                gam_payload = gamification_mod.award_correct_answer(
                    DATA_DIR,
                    username,
                    bonus_practice=bonus_practice,
                    remedial=remedial,
                    old_success_count=old_success_count,
                    new_success_count=new_success_count,
                    mastered_now=mastered_now,
                    mastered_words=len(reciter.mastered_words),
                )

            body = {
                'correct': is_correct,
                'message': message,
                'word': {
                    'english': word.english,
                    'chinese': word.chinese,
                    'success_count': word.success_count
                }
            }
            if gam_payload is not None:
                body['gamification'] = gam_payload
            return jsonify(body), 200
    except Exception as e:
        logger.error(f"练习单词失败: {e}")
        return jsonify({'error': '服务器内部错误'}), 500

@app.route('/api/words/speak', methods=['POST'])
@token_required
def speak_text(username):
    """朗读文本（跨平台支持）
    
    - macOS: 使用系统 say 命令
    - Linux/Windows: 如果 say 命令不存在则静默跳过
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': '无效的JSON数据'}), 400
        
        text = data.get('text', '').strip()
        if not text:
            return jsonify({'error': '文本不能为空'}), 400

        safe_text = sanitize_tts_text(text)
        if not safe_text:
            return jsonify({'error': '文本无效或过长'}), 400
        
        if shutil.which('say') is None:
            logger.debug(f"用户 {username} 尝试朗读但 say 命令不可用")
            return jsonify({'message': '语音播放不可用，已跳过'}), 200
        
        try:
            # 始终使用参数列表传递，禁止 shell=True，避免命令注入
            subprocess.run(
                ['say', safe_text],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            logger.warning(f"用户 {username} 的朗读超时")
            return jsonify({'message': '朗读超时，音频过长'}), 200
        except Exception as e:
            logger.error(f"朗读执行失败: {e}")
            return jsonify({'message': '朗读执行失败'}), 200
        
        return jsonify({'message': '朗读完成'}), 200
    except Exception as e:
        logger.error(f"朗读失败: {e}")
        return jsonify({'error': '服务器内部错误'}), 500

@app.route('/api/words/import', methods=['POST'])
@token_required
def import_words(username):
    """导入单词文件"""
    try:
        if 'file' not in request.files:
            return jsonify({'error': '没有上传文件'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': '没有选择文件'}), 400
        
        content = file.read().decode('utf-8', errors='replace')
        
        words = []
        for line in content.split('\n'):
            if ',' in line:
                parts = line.strip().split(',', 1)
                if len(parts) == 2:
                    en = parts[0].strip()[:500]
                    zh = parts[1].strip()[:500]
                    if en and zh:
                        words.append((en, zh))
        
        with user_reciter_session(username) as reciter:
            result = reciter.add_words(words)

        added = result['added']
        skipped = result['skipped_duplicate']
        invalid = result['skipped_invalid']
        logger.info(
            "用户 %s 文件导入: 解析 %s 行, 新增 %s, 重复 %s, 无效 %s",
            username,
            len(words),
            added,
            skipped,
            invalid,
        )
        if added == 0 and not words:
            return jsonify({'error': '文件中没有有效的 英文,中文 行'}), 400
        msg = f'成功加入 {added} 个新单词'
        if skipped:
            msg += f'，已跳过 {skipped} 个重复'
        if invalid:
            msg += f'，{invalid} 行无效已忽略'
        if added == 0 and words:
            msg = f'没有新单词：{skipped} 个与已有词重复' + (f'，{invalid} 行无效' if invalid else '')

        return jsonify({
            'message': msg,
            'count': added,
            'skipped_duplicate': skipped,
            'skipped_invalid': invalid,
        }), 200
    except Exception as e:
        logger.error(f"导入单词失败: {e}")
        return jsonify({'error': '导入失败，请检查文件格式'}), 500


def _parse_import_json_body(request):
    """解析 JSON 导入：根为数组，或 {\"words\": [...]}。"""
    data = request.get_json(silent=True)
    if data is None:
        return None, 'JSON 格式无效或 Content-Type 不是 application/json'
    if isinstance(data, list):
        return data, None
    if isinstance(data, dict) and 'words' in data:
        w = data['words']
        if isinstance(w, list):
            return w, None
    return None, '请提供 JSON 数组，或包含 words 数组的对象'


@app.route('/api/words/import-json', methods=['POST'])
@token_required
def import_words_json(username):
    """家长粘贴学习数据格式的 JSON，合并到当前用户的待复习词库。"""
    items, err = _parse_import_json_body(request)
    if err:
        return jsonify({'error': err}), 400
    if not items:
        return jsonify({'error': '单词列表为空'}), 400
    if len(items) > 5000:
        return jsonify({'error': '单次最多导入 5000 条'}), 400
    try:
        with user_reciter_session(username) as reciter:
            result = reciter.add_words_from_dicts(items)
        n = result['added']
        skipped = result['skipped_duplicate']
        invalid = result['skipped_invalid']
        msg = f'成功加入 {n} 个新单词'
        if skipped:
            msg += f'，已跳过 {skipped} 个重复'
        if invalid:
            msg += f'，{invalid} 条无效已忽略'
        logger.info(
            "用户 %s JSON 导入: added=%s dup=%s invalid=%s",
            username,
            n,
            skipped,
            invalid,
        )
        return jsonify({'message': msg, **result}), 200
    except Exception as e:
        logger.error(f"JSON 导入失败: {e}")
        return jsonify({'error': '导入失败，请检查 JSON 格式'}), 500


@app.route('/api/words/mastered', methods=['GET'])
@token_required
def get_mastered_words(username):
    """获取已掌握单词"""
    try:
        with user_reciter_session(username) as reciter:
            words = [
                {
                    'english': w.english,
                    'chinese': w.chinese,
                    'review_count': w.review_count,
                    'mastered_date': w.next_review_date.isoformat()
                }
                for w in reciter.mastered_words
            ]

            return jsonify({'words': words, 'count': len(words)}), 200
    except Exception as e:
        logger.error(f"获取已掌握单词失败: {e}")
        return jsonify({'error': '服务器内部错误'}), 500

# ==================== 管理员 API ====================

@app.route('/api/admin/status', methods=['GET'])
def admin_status():
    """前端用于判断是否已配置管理员（不泄露账号名）。"""
    return jsonify({'admin_configured': admin_configured()}), 200


@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    """管理员登录，签发独立 admin token。"""
    try:
        if not _rate_allow(f"admin_login:{_client_ip()}", _RATE_MAX_ADMIN_LOGIN):
            return jsonify({'error': '请求过于频繁，请稍后再试'}), 429
        if not admin_configured():
            return jsonify({'error': '服务端未配置管理员（请设置 ADMIN_USERNAME 与 ADMIN_PASSWORD 或 ADMIN_PASSWORD_HASH）'}), 503

        data = request.get_json(silent=True) or {}
        auser = (data.get('username') or '').strip()
        pwd = (data.get('password') or '').strip()
        if not auser or not pwd:
            return jsonify({'error': '用户名和密码不能为空'}), 400

        if not verify_admin_credentials(auser, pwd):
            logger.warning("管理员登录失败: user=%s ip=%s", auser, _client_ip())
            return jsonify({'error': '用户名或密码错误'}), 401

        tok = create_admin_token()
        logger.info("管理员登录成功 ip=%s", _client_ip())
        return jsonify({'access_token': tok, 'token_type': 'bearer'}), 200
    except Exception as e:
        logger.error(f"管理员登录异常: {e}")
        return jsonify({'error': '服务器内部错误'}), 500


@app.route('/api/admin/logout', methods=['POST'])
@admin_required
def admin_logout():
    """注销管理员会话。"""
    auth = request.headers.get('Authorization', '')
    tok = auth[7:].strip() if auth.startswith('Bearer ') else ''
    admin_tokens.pop(tok, None)
    admin_token_expiry.pop(tok, None)
    return jsonify({'message': '已退出'}), 200


@app.route('/api/admin/users', methods=['GET'])
@admin_required
def admin_list_users():
    """所有学生用户及学习概况。"""
    users = load_users()
    out = []
    for uname in sorted(users.keys()):
        u = users[uname]
        if not isinstance(u, dict):
            continue
        summ = _learning_data_summary(uname)
        out.append({
            'username': uname,
            'email': u.get('email'),
            'created_at': u.get('created_at'),
            'enabled': u.get('enabled', True),
            'pending_words': summ['pending'],
            'mastered_words': summ['mastered'],
        })
    return jsonify({'users': out}), 200


@app.route('/api/admin/users/<username>/enabled', methods=['PATCH'])
@admin_required
def admin_set_user_enabled(username):
    """启用或禁用学生账号。"""
    if not is_valid_username(username):
        return jsonify({'error': '无效的用户名'}), 400
    data = request.get_json(silent=True) or {}
    if 'enabled' not in data:
        return jsonify({'error': '缺少 enabled 字段'}), 400
    enabled = bool(data['enabled'])

    users = load_users()
    if username not in users:
        return jsonify({'error': '用户不存在'}), 404

    users[username]['enabled'] = enabled
    save_users(users)
    if not enabled:
        _revoke_user_tokens(username)
        _invalidate_user_reciter_cache(username)
        logger.info("管理员禁用用户: %s", username)
    else:
        logger.info("管理员启用用户: %s", username)

    return jsonify({'username': username, 'enabled': enabled}), 200


@app.route('/api/admin/users/<username>/password', methods=['PATCH'])
@admin_required
def admin_set_user_password(username):
    """管理员重置指定用户登录密码（该用户所有会话失效，需重新登录）。"""
    if not is_valid_username(username):
        return jsonify({'error': '无效的用户名'}), 400
    data = request.get_json(silent=True) or {}
    new_password = (data.get('password') or '').strip()
    if len(new_password) < 6:
        return jsonify({'error': '密码至少6个字符'}), 400

    users = load_users()
    if username not in users:
        return jsonify({'error': '用户不存在'}), 404

    users[username]['password_hash'] = hash_password(new_password)
    save_users(users)
    _revoke_user_tokens(username)
    _invalidate_user_reciter_cache(username)
    logger.info("管理员重置用户密码: %s", username)
    return jsonify({'username': username, 'message': '密码已更新，该用户需重新登录'}), 200


@app.route('/api/admin/invites', methods=['POST'])
@admin_required
def admin_create_invite():
    """生成一次性邀请码（仅响应中明文展示一次）。"""
    plain = secrets.token_urlsafe(18)
    inv_id = str(uuid.uuid4())
    entry = {
        'id': inv_id,
        'code_hash': _hash_invite_code(plain),
        'created_at': datetime.now().isoformat(),
        'created_by': os.getenv('ADMIN_USERNAME', 'admin'),
        'used_at': None,
        'used_by': None,
    }
    with _invites_lock:
        data = load_invites()
        data.setdefault('invites', []).append(entry)
        save_invites(data)

    logger.info("管理员生成邀请码 id=%s", inv_id)
    return jsonify({
        'id': inv_id,
        'invite_code': plain,
        'hint': '请复制保存，关闭后无法再次查看明文',
    }), 201


@app.route('/api/admin/invites', methods=['GET'])
@admin_required
def admin_list_invites():
    """邀请码列表（不含明文）。"""
    data = load_invites()
    rows = []
    for inv in data.get('invites', []):
        rows.append({
            'id': inv.get('id'),
            'created_at': inv.get('created_at'),
            'created_by': inv.get('created_by'),
            'used_at': inv.get('used_at'),
            'used_by': inv.get('used_by'),
            'status': 'used' if inv.get('used_at') else 'unused',
        })
    rows.sort(key=lambda x: x.get('created_at') or '', reverse=True)
    return jsonify({'invites': rows}), 200


# ==================== 健康检查 ====================

@app.route('/api/health', methods=['GET'])
def health():
    """健康检查"""
    return jsonify({'status': 'healthy', 'timestamp': datetime.now().isoformat()}), 200

# ==================== 启动配置 ====================

if __name__ == '__main__':
    _debug = os.getenv("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    app.run(
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        debug=_debug,
        threaded=True,
    )
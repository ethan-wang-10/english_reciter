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
from typing import Dict, Generator, List, Optional
from time import time

from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash

# 导入核心功能
from reciter import (
    WordReciter,
    Config,
    get_logger,
)

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

# 登录/注册简单限流（按 IP，内存存储）
_rate_buckets: Dict[str, List[float]] = defaultdict(list)
_RATE_WINDOW_SEC = 60
_RATE_MAX_LOGIN = 20
_RATE_MAX_REGISTER = 10

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

def load_users() -> dict:
    """加载所有用户数据"""
    users_file = DATA_DIR / "users.json"
    if not users_file.exists():
        return {}
    
    try:
        with open(users_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"加载用户数据失败: {e}")
        return {}

def save_users(users: dict) -> None:
    """保存用户数据"""
    users_file = DATA_DIR / "users.json"
    try:
        with open(users_file, 'w', encoding='utf-8') as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存用户数据失败: {e}")

def create_user(username: str, password: str, email: str = None) -> bool:
    """创建用户"""
    if not is_valid_username(username):
        return False

    users = load_users()

    if username in users:
        return False
    
    password_hash = hash_password(password)
    
    users[username] = {
        "password_hash": password_hash,
        "email": email,
        "created_at": datetime.now().isoformat()
    }
    
    # 为用户创建数据目录
    user_dir = DATA_DIR / username
    user_dir.mkdir(exist_ok=True)
    
    save_users(users)
    logger.info(f"新用户注册: {username}")
    return True

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
        
        # 将用户名传递给路由函数
        return f(username, *args, **kwargs)
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
        
        if create_user(username, password, email):
            # 创建token并返回
            token = create_token(username)
            return jsonify({
                'username': username,
                'email': email,
                'created_at': datetime.now().isoformat(),
                'access_token': token,
                'token_type': 'bearer'
            }), 201
        else:
            return jsonify({'error': '用户名已存在'}), 400
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
            token = create_token(username)
            return jsonify({
                'access_token': token,
                'token_type': 'bearer',
                'username': username
            }), 200
        else:
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
        
        if not word_id or not answer:
            return jsonify({'error': '单词ID和答案不能为空'}), 400

        with user_reciter_session(username) as reciter:
            word = None
            for w in reciter.all_words:
                if w.english.lower() == word_id.lower():
                    word = w
                    break

            if not word:
                return jsonify({'error': '单词未找到'}), 404

            is_correct = answer.strip().lower() == word.english.lower()

            if is_correct:
                word.success_count += 1
                word.review_count += 1

                if word.success_count >= reciter.config.MAX_SUCCESS_COUNT:
                    reciter.mastered_words.append(word)
                    reciter.all_words.remove(word)
                    message = '🎉 已掌握单词！'
                else:
                    delta_days = reciter.calculate_review_days(word.success_count)
                    word.next_review_date = date.today() + timedelta(days=delta_days)
                    message = f'✅ 正确！下次复习: +{delta_days}天'
            else:
                word.review_count += 1
                message = '❌ 错误，请继续努力！'

            # Web 端每次答题不必全量备份，降低 IO
            reciter.save_learning_data(backup=False)

            return jsonify({
                'correct': is_correct,
                'message': message,
                'word': {
                    'english': word.english,
                    'chinese': word.chinese,
                    'success_count': word.success_count
                }
            }), 200
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
            reciter.add_words(words)

        logger.info(f"用户 {username} 导入了 {len(words)} 个单词")
        
        return jsonify({
            'message': f'成功导入 {len(words)} 个单词',
            'count': len(words)
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
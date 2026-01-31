#!/usr/bin/env python3
"""
简化版Web应用 - 智能英语背诵系统
使用Flask替代FastAPI，简化依赖和架构
支持多用户、跨平台访问
"""

import os
import json
import hashlib
import secrets
import shutil
import platform
from datetime import datetime, timedelta, date
from pathlib import Path
from functools import wraps
from typing import Optional, Dict

from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS

# 导入核心功能
from reciter import (
    WordReciter, Word, Config,
    get_logger, MAX_ATTEMPTS
)

# 日志配置
logger = get_logger(__name__)

# Flask应用
app = Flask(__name__, static_folder='static')
app.secret_key = os.getenv("SECRET_KEY", secrets.token_urlsafe(32))
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB上传限制
CORS(app, supports_credentials=True)

# 数据目录
DATA_DIR = Path("user_data_simple")
DATA_DIR.mkdir(exist_ok=True)

# Token存储（内存中，重启后失效）
# 实际应用中应使用数据库或Redis
user_tokens: Dict[str, str] = {}  # token -> username
token_expiry: Dict[str, datetime] = {}  # token -> expiry time

# ==================== 工具函数 ====================

def hash_password(password: str) -> str:
    """哈希密码"""
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password: str, password_hash: str) -> bool:
    """验证密码"""
    return hash_password(password) == password_hash

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
    """验证用户"""
    users = load_users()
    
    if username not in users:
        return False
    
    password_hash = users[username]["password_hash"]
    return verify_password(password, password_hash)

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

def get_user_reciter(username: str) -> WordReciter:
    """获取用户的背诵器实例"""
    user_dir = DATA_DIR / username
    
    config_file = user_dir / "config.json"
    config = Config(str(config_file)) if config_file.exists() else Config()
    
    # 设置用户专属数据文件
    config.DATA_FILE = str(user_dir / "learning_data.json")
    config.EXAMPLE_DB = str(user_dir / "word_examples.json")
    
    return WordReciter(config)

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
        data = request.get_json()
        if not data:
            return jsonify({'error': '无效的JSON数据'}), 400
        
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        email = data.get('email')
        
        if not username or not password:
            return jsonify({'error': '用户名和密码不能为空'}), 400
        
        if len(username) < 3:
            return jsonify({'error': '用户名至少3个字符'}), 400
        
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
        reciter = get_user_reciter(username)
        
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
        reciter = get_user_reciter(username)
        review_list = reciter._get_today_review_list()
        
        words = [
            {
                'english': w.english,
                'chinese': w.chinese,
                'success_count': w.success_count,
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
        
        reciter = get_user_reciter(username)
        
        # 查找单词
        word = None
        for w in reciter.all_words:
            if w.english.lower() == word_id.lower():
                word = w
                break
        
        if not word:
            return jsonify({'error': '单词未找到'}), 404
        
        # 检查答案
        is_correct = answer.strip().lower() == word.english.lower()
        
        if is_correct:
            word.success_count += 1
            word.review_count += 1
            
            if word.success_count >= reciter.config.MAX_SUCCESS_COUNT:
                reciter.mastered_words.append(word)
                reciter.all_words.remove(word)
                message = '🎉 已掌握单词！'
            else:
                delta_days = reciter._calculate_review_days(word.success_count)
                word.next_review_date = date.today() + timedelta(days=delta_days)
                message = f'✅ 正确！下次复习: +{delta_days}天'
        else:
            word.review_count += 1
            message = '❌ 错误，请继续努力！'
        
        # 保存数据
        reciter._save_data()
        
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
        
        # 直接使用传入的文本（前端已提取英文部分）
        en_text = text
        if not en_text:
            return jsonify({'error': '无法提取有效的英文文本'}), 400
        
        # 检查 say 命令是否可用
        if shutil.which('say') is None:
            logger.debug(f"用户 {username} 尝试朗读但 say 命令不可用")
            return jsonify({'message': '语音播放不可用，已跳过'}), 200
        
        # 使用 say 命令，跨平台忽略输出和错误
        if platform.system() == 'Windows':
            os.system(f'say "{en_text}" > NUL 2>&1')
        else:
            os.system(f'say "{en_text}" > /dev/null 2>&1')
        
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
        
        # 读取文件内容
        content = file.read().decode('utf-8')
        
        # 解析单词
        words = []
        for line in content.split('\n'):
            if ',' in line:
                parts = line.strip().split(',', 1)
                if len(parts) == 2:
                    words.append((parts[0], parts[1]))
        
        # 添加单词
        reciter = get_user_reciter(username)
        reciter.add_words(words)
        
        logger.info(f"用户 {username} 导入了 {len(words)} 个单词")
        
        return jsonify({
            'message': f'成功导入 {len(words)} 个单词',
            'count': len(words)
        }), 200
    except Exception as e:
        logger.error(f"导入单词失败: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/words/mastered', methods=['GET'])
@token_required
def get_mastered_words(username):
    """获取已掌握单词"""
    try:
        reciter = get_user_reciter(username)
        
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
    # 开发环境
    app.run(
        host='0.0.0.0',
        port=8000,
        debug=True,
        threaded=True
    )
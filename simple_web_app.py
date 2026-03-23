#!/usr/bin/env python3
"""
简化版Web应用 - 智能英语背诵系统
使用Flask替代FastAPI，简化依赖和架构
支持多用户、跨平台访问
"""

import os
import csv
import json
import random
import re
import tempfile
import hashlib
import secrets
import shutil
import subprocess
import threading
import urllib.request
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

# 全站共享词库（家长贡献，持久化在 user_data_simple/_shared/）
SHARED_DATA_DIR = DATA_DIR / "_shared"
COMMUNITY_WB_FILE = SHARED_DATA_DIR / "community_wordbank.json"
_community_wb_lock = threading.Lock()
STATIC_WB_DIR = Path("static/wordbanks")
_COMMUNITY_SCHEMA = "english_reciter.wordbank.community/v1"

# 新 CSV 词汇表路径
WORDS_CSV_FILE = STATIC_WB_DIR / "words.csv"
_words_csv_lock = threading.Lock()
_words_csv_cache: Optional[List[dict]] = None
_words_csv_cache_mtime: float = 0.0

# DeepSeek API
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
_APP_CONFIG_FILE = Path("config.json")


def _load_app_config() -> dict:
    if not _APP_CONFIG_FILE.exists():
        return {}
    try:
        with open(_APP_CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("读取 config.json 失败: %s", e)
        return {}


def _save_app_config(data: dict) -> None:
    fd, tmp = tempfile.mkstemp(suffix=".json", dir=str(_APP_CONFIG_FILE.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _APP_CONFIG_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def get_deepseek_api_key() -> str:
    """读取 DeepSeek API Key：环境变量优先，其次 config.json。"""
    env_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if env_key:
        return env_key
    return str(_load_app_config().get("deepseek_api_key", "") or "").strip()


# 动态读取（每次调用 get_deepseek_api_key() 而不是模块级常量）
DEEPSEEK_API_KEY = ""  # 保持兼容，实际使用 get_deepseek_api_key()

# CSV 字段
_CSV_FIELDS = ["english", "chinese", "level", "phonetic",
               "example1", "example1_form", "example1_cn",
               "example2", "example2_form", "example2_cn"]


def _empty_community_doc() -> dict:
    return {
        "schema": _COMMUNITY_SCHEMA,
        "phase": "community",
        "label": "共享（家长贡献）",
        "description": "全账户共享：家长通过简单格式导入且不在系统词库中的单词",
        "version": 1,
        "count": 0,
        "words": [],
    }


def _read_community_file_unlocked() -> dict:
    """读取共享词库（调用方需已持锁或保证无并发写）。"""
    SHARED_DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not COMMUNITY_WB_FILE.exists():
        data = _empty_community_doc()
        _write_community_file_atomic(data)
        return data
    try:
        with open(COMMUNITY_WB_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error("共享词库损坏，将重建空文件: %s", e)
        data = _empty_community_doc()
        _write_community_file_atomic(data)
        return data
    if not isinstance(raw, dict):
        raw = _empty_community_doc()
    words = raw.get("words")
    if not isinstance(words, list):
        words = []
    raw["words"] = words
    raw.setdefault("schema", _COMMUNITY_SCHEMA)
    raw.setdefault("label", "共享（家长贡献）")
    raw["count"] = len(words)
    return raw


def _write_community_file_atomic(data: dict) -> None:
    SHARED_DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = COMMUNITY_WB_FILE
    data = dict(data)
    words = data.get("words")
    if not isinstance(words, list):
        words = []
    data["words"] = words
    data["count"] = len(words)
    fd, tmp_name = tempfile.mkstemp(suffix=".json", dir=str(SHARED_DATA_DIR), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def load_system_wordbank_english_lower() -> set:
    """主词库 ``static/wordbanks/words.csv`` 中的英文（小写），用于「共享词库」与家长导入去重。"""
    return get_csv_english_set()


def parse_simple_parent_import_text(text: str) -> Tuple[List[dict], Optional[str]]:
    """
    解析家长简易导入：每行「单词、例句、译文」，Tab 或 | 分隔；
    也支持 JSON 数组或 {\"words\": [...]}，字段 english / example / chinese（或 translation）。
    """
    text = text.strip()
    if not text:
        return [], "内容为空"
    if text[0] in "[{":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            return [], f"JSON 解析失败: {e}"
        if isinstance(data, dict) and "words" in data:
            data = data["words"]
        if not isinstance(data, list):
            return [], "JSON 应为数组，或包含 words 数组的对象"
        out: List[dict] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            en = str(item.get("english", "")).strip()
            zh = str(item.get("chinese", "") or item.get("translation", "")).strip()
            ex = str(item.get("example", "")).strip()
            out.append({"english": en, "chinese": zh, "example": ex})
        return out, None
    out: List[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts: Optional[List[str]] = None
        if "\t" in line:
            parts = [p.strip() for p in line.split("\t")]
            if len(parts) < 3:
                continue
            en, ex, zh = parts[0], parts[1], parts[2]
        elif "|" in line:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 3:
                continue
            en = parts[0]
            zh = parts[-1]
            ex = "|".join(parts[1:-1]).strip()
        else:
            continue
        out.append({"english": en, "chinese": zh, "example": ex})
    if not out:
        return [], (
            "未解析到有效行。每行格式：单词、例句、译文，中间用 Tab 或 | 分隔"
            "（示例：apple\\tI like apples.\\t苹果）"
        )
    return out, None


# ==================== CSV 词汇表工具 ====================

def load_words_csv() -> List[dict]:
    """读取 CSV 词汇表，带缓存（文件未修改则复用内存缓存）。"""
    global _words_csv_cache, _words_csv_cache_mtime
    with _words_csv_lock:
        try:
            mtime = WORDS_CSV_FILE.stat().st_mtime if WORDS_CSV_FILE.exists() else 0.0
        except OSError:
            mtime = 0.0
        if _words_csv_cache is not None and mtime == _words_csv_cache_mtime:
            return _words_csv_cache
        if not WORDS_CSV_FILE.exists():
            _words_csv_cache = []
            _words_csv_cache_mtime = 0.0
            return []
        rows = []
        try:
            with open(WORDS_CSV_FILE, "r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    rows.append(dict(row))
        except Exception as e:
            logger.error("读取词汇CSV失败: %s", e)
            rows = []
        _words_csv_cache = rows
        _words_csv_cache_mtime = mtime
        return rows


def invalidate_words_csv_cache() -> None:
    global _words_csv_cache
    with _words_csv_lock:
        _words_csv_cache = None


def get_csv_english_set() -> set:
    """返回 CSV 中所有英文单词的小写集合。"""
    return {r.get("english", "").strip().lower() for r in load_words_csv() if r.get("english", "").strip()}


def append_words_to_csv(new_rows: List[dict]) -> int:
    """将新词条 append 到 CSV 文件，返回实际写入数量。"""
    if not new_rows:
        return 0
    with _words_csv_lock:
        file_exists = WORDS_CSV_FILE.exists()
        WORDS_CSV_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(suffix=".csv", dir=str(WORDS_CSV_FILE.parent), text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS, extrasaction="ignore")
                if file_exists:
                    with open(WORDS_CSV_FILE, "r", encoding="utf-8", newline="") as src:
                        f.write(src.read())
                else:
                    writer.writeheader()
                for row in new_rows:
                    clean = {k: str(row.get(k, "") or "").strip() for k in _CSV_FIELDS}
                    writer.writerow(clean)
            os.replace(tmp, WORDS_CSV_FILE)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        global _words_csv_cache
        _words_csv_cache = None
    return len(new_rows)


def csv_word_to_review_item(row: dict, example_key: str = "1") -> dict:
    """将 CSV 行转换为复习所用的词条字典，example_key 为 '1' 或 '2'。"""
    k = example_key
    ex_en = row.get(f"example{k}", "").strip()
    ex_form = row.get(f"example{k}_form", "").strip()
    ex_cn = row.get(f"example{k}_cn", "").strip()
    example = f"{ex_en}_{ex_cn}" if ex_en or ex_cn else ""
    return {
        "english": row.get("english", "").strip(),
        "chinese": row.get("chinese", "").strip(),
        "level": row.get("level", "").strip(),
        "phonetic": row.get("phonetic", "").strip(),
        "example": example,
        "example_form": ex_form,
        "example_en": ex_en,
        "example_cn": ex_cn,
    }


def pick_example_for_word(row: dict) -> dict:
    """从词条的 2 个例句中随机选 1 个返回复习条目。"""
    has1 = bool(row.get("example1", "").strip())
    has2 = bool(row.get("example2", "").strip())
    if has1 and has2:
        k = random.choice(["1", "2"])
    elif has2:
        k = "2"
    else:
        k = "1"
    return csv_word_to_review_item(row, k)


def lookup_csv_word(english: str) -> Optional[dict]:
    """在 CSV 中按英文精确匹配（不区分大小写），返回原始行或 None。"""
    key = english.strip().lower()
    for row in load_words_csv():
        if row.get("english", "").strip().lower() == key:
            return row
    return None


# ==================== 用户权限 ====================

def get_user_plan(username: str) -> str:
    """返回用户套餐类型: 'free' 或 'paid'。默认 free。"""
    users = load_users()
    u = users.get(username)
    if isinstance(u, dict):
        return u.get("plan", "free")
    return "free"


def set_user_plan(username: str, plan: str) -> bool:
    """设置用户套餐。plan 必须为 'free' 或 'paid'。"""
    if plan not in ("free", "paid"):
        return False
    users = load_users()
    if username not in users:
        return False
    users[username]["plan"] = plan
    save_users(users)
    return True


def is_paid_user(username: str) -> bool:
    return get_user_plan(username) == "paid"


# ==================== DeepSeek API ====================

def _deepseek_chat(messages: List[dict], model: str = "deepseek-chat",
                   max_tokens: int = 2048, temperature: float = 0.7) -> Optional[str]:
    """调用 DeepSeek Chat API，返回助手回复文本；失败返回 None。"""
    api_key = get_deepseek_api_key()
    if not api_key:
        logger.warning("DEEPSEEK_API_KEY 未配置，无法调用 DeepSeek API")
        return None
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    req = urllib.request.Request(DEEPSEEK_API_URL, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error("DeepSeek API 调用失败: %s", e)
        return None


def deepseek_extract_lemmas(text: str) -> Optional[List[str]]:
    """用 DeepSeek 从文章中提取单词原形列表。"""
    prompt = (
        "请从以下英文文章中提取所有实义词（名词、动词、形容词、副词），"
        "还原为原形（lemma），去重，用英文逗号分隔，只返回单词列表，不要其他说明。\n\n"
        f"{text[:3000]}"
    )
    reply = _deepseek_chat([{"role": "user", "content": prompt}], max_tokens=500)
    if not reply:
        return None
    words = [w.strip().lower() for w in re.split(r'[,，\s]+', reply) if w.strip() and re.match(r'^[a-zA-Z]+$', w.strip())]
    return words if words else None


def deepseek_generate_word_entries(words: List[str], level: str = "") -> Optional[List[dict]]:
    """
    用 DeepSeek 为单词列表生成词汇表条目（chinese, level, phonetic, examples）。
    返回 list of dict，每个 dict 含 CSV 字段。
    """
    level_hint = f"，这批词汇难度级别为：{level}" if level else ""
    words_str = "、".join(words[:30])  # 每批最多30词
    prompt = f"""请为以下英语单词生成词汇表条目{level_hint}。

单词列表：{words_str}

请严格按照以下JSON数组格式返回，不要任何额外说明：
[
  {{
    "english": "单词原形",
    "chinese": "中文释义（简洁）",
    "level": "小学/初中/高中/GRE（根据难度，如用户指定则使用指定值）",
    "phonetic": "音标（如/æpl/）",
    "example1": "第一个英文例句（难度与level匹配，句子自然，含该词的变形或原形）",
    "example1_form": "该词在例句1中的实际形式（如与原形相同则为空字符串）",
    "example1_cn": "例句1的中文翻译",
    "example2": "第二个英文例句（与例句1不同语境）",
    "example2_form": "该词在例句2中的实际形式（如与原形相同则为空字符串）",
    "example2_cn": "例句2的中文翻译"
  }}
]

注意：
- level必须是"小学"、"初中"、"高中"或"GRE"之一{level_hint}
- 例句难度要与level相符，小学/初中例句要简单易懂
- example1_form 和 example2_form：只写在句子中实际出现的变形形式，如与原形完全相同则写空字符串
"""
    reply = _deepseek_chat([{"role": "user", "content": prompt}], max_tokens=3000)
    if not reply:
        return None
    # 提取JSON
    json_match = re.search(r'\[[\s\S]*\]', reply)
    if not json_match:
        logger.error("DeepSeek 返回格式不含JSON数组: %s", reply[:200])
        return None
    try:
        data = json.loads(json_match.group(0))
        return data if isinstance(data, list) else None
    except json.JSONDecodeError as e:
        logger.error("DeepSeek 返回JSON解析失败: %s", e)
        return None


# ==================== Token存储（内存中，重启后失效）====================
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


def _get_admin_config() -> dict:
    """
    读取管理员配置，优先级：环境变量 > config.json。
    返回 {'username': str, 'password_hash': str, 'password': str}，不存在时为空字符串。
    """
    username = os.getenv('ADMIN_USERNAME', '').strip()
    pwd_hash = os.getenv('ADMIN_PASSWORD_HASH', '').strip()
    pwd_plain = os.getenv('ADMIN_PASSWORD', '').strip()

    # 环境变量未设置时，从 config.json 读取
    if not username:
        cfg = _load_app_config()
        username = str(cfg.get('admin_username', '') or '').strip()
        if not pwd_hash:
            pwd_hash = str(cfg.get('admin_password_hash', '') or '').strip()
        if not pwd_plain:
            pwd_plain = str(cfg.get('admin_password', '') or '').strip()

    return {'username': username, 'password_hash': pwd_hash, 'password': pwd_plain}


def admin_configured() -> bool:
    """是否已配置管理员账号（环境变量或 config.json）。"""
    cfg = _get_admin_config()
    if not cfg['username']:
        return False
    return bool(cfg['password_hash']) or bool(cfg['password'])


def verify_admin_credentials(username: str, password: str) -> bool:
    if not admin_configured():
        return False
    cfg = _get_admin_config()
    if username != cfg['username']:
        return False
    if cfg['password_hash']:
        return verify_password(password, cfg['password_hash'])
    if cfg['password']:
        return secrets.compare_digest(password.encode('utf-8'), cfg['password'].encode('utf-8'))
    return False


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
            all_words = []
            for w in reciter.all_words:
                csv_row = lookup_csv_word(w.english)
                all_words.append({
                    'english': w.english,
                    'chinese': w.chinese,
                    'phonetic': csv_row.get('phonetic', '') if csv_row else '',
                    'success_count': w.success_count,
                    'max_success_count': reciter.config.MAX_SUCCESS_COUNT,
                    'review_round': w.review_round,
                    'review_count': w.review_count,
                    'next_review_date': w.next_review_date.isoformat(),
                    'remaining_days': (w.next_review_date - date.today()).days
                })

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
    """获取今日复习列表（从CSV中补充 example_form、随机选择例句）"""
    try:
        with user_reciter_session(username) as reciter:
            review_list = reciter.get_today_review_list()

            words = []
            for w in review_list:
                item = {
                    'english': w.english,
                    'chinese': w.chinese,
                    'success_count': w.success_count,
                    'max_success_count': reciter.config.MAX_SUCCESS_COUNT,
                    'review_count': w.review_count,
                    'example': w.example,
                    'example_form': '',
                }
                # 尝试从 CSV 中获取更丰富的例句信息
                csv_row = lookup_csv_word(w.english)
                if csv_row:
                    picked = pick_example_for_word(csv_row)
                    if picked.get('example'):
                        item['example'] = picked['example']
                    item['example_form'] = picked.get('example_form', '')
                    item['phonetic'] = csv_row.get('phonetic', '')
                    item['level'] = csv_row.get('level', '')
                words.append(item)

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
            words = []
            for w in picked:
                item = {
                    'english': w.english,
                    'chinese': w.chinese,
                    'success_count': w.success_count,
                    'max_success_count': reciter.config.MAX_SUCCESS_COUNT,
                    'review_count': w.review_count,
                    'example': w.example,
                    'example_form': '',
                }
                csv_row = lookup_csv_word(w.english)
                if csv_row:
                    picked_ex = pick_example_for_word(csv_row)
                    if picked_ex.get('example'):
                        item['example'] = picked_ex['example']
                    item['example_form'] = picked_ex.get('example_form', '')
                    item['phonetic'] = csv_row.get('phonetic', '')
                    item['level'] = csv_row.get('level', '')
                words.append(item)
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

            submitted = answer.strip().lower()
            correct_forms = {word.english.lower()}
            # 检查 example_form（从 CSV 获取）
            example_form = data.get('example_form', '').strip().lower()
            if example_form:
                correct_forms.add(example_form)
            else:
                csv_row = lookup_csv_word(word.english)
                if csv_row:
                    f1 = csv_row.get('example1_form', '').strip().lower()
                    f2 = csv_row.get('example2_form', '').strip().lower()
                    if f1:
                        correct_forms.add(f1)
                    if f2:
                        correct_forms.add(f2)
            is_correct = submitted in correct_forms
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


@app.route('/api/user/plan', methods=['GET'])
@token_required
def get_user_plan_api(username):
    """获取当前用户套餐类型。"""
    return jsonify({'plan': get_user_plan(username)}), 200


@app.route('/api/words/reset-today', methods=['POST'])
@token_required
def reset_today_review(username):
    """清空今日待复习列表（将所有到期单词的 next_review_date 推迟一天）。"""
    try:
        with user_reciter_session(username) as reciter:
            today = date.today()
            count = 0
            for w in reciter.all_words:
                if w.next_review_date <= today:
                    w.next_review_date = today + timedelta(days=1)
                    count += 1
            reciter.save_learning_data(backup=False)
        _invalidate_user_reciter_cache(username)
        return jsonify({'message': f'已清空 {count} 个今日待复习单词', 'count': count}), 200
    except Exception as e:
        logger.error("清空今日复习失败: %s", e)
        return jsonify({'error': '服务器内部错误'}), 500


@app.route('/api/wordbank/csv', methods=['GET'])
@token_required
def get_wordbank_csv(username):
    """返回 CSV 词汇表（所有词或按 level 过滤）。"""
    level = request.args.get('level', '').strip()
    rows = load_words_csv()
    if level:
        rows = [r for r in rows if r.get('level', '') == level]
    return jsonify({'words': rows, 'count': len(rows)}), 200


@app.route('/api/wordbank/csv/search', methods=['GET'])
@token_required
def search_wordbank_csv(username):
    """在 CSV 词汇表中搜索（支持英文/中文，逗号分隔多词）。"""
    q = request.args.get('q', '').strip()
    level = request.args.get('level', '').strip()
    if not q:
        return jsonify({'words': [], 'count': 0}), 200
    terms = [t.strip().lower() for t in re.split(r'[,，]', q) if t.strip()]
    rows = load_words_csv()
    if level:
        rows = [r for r in rows if r.get('level', '') == level]
    result = []
    seen = set()
    for row in rows:
        en = row.get('english', '').lower()
        zh = row.get('chinese', '')
        for term in terms:
            # 英文字段：整词精确匹配（搜 run 不匹配 running/return）
            if re.match(r'[a-z]', term):
                matched = (en == term)
            else:
                # 中文搜索：包含匹配
                matched = term in zh
            if matched:
                if en not in seen:
                    seen.add(en)
                    result.append(row)
                break
    return jsonify({'words': result, 'count': len(result)}), 200


@app.route('/api/words/import-from-article', methods=['POST'])
@token_required
def import_from_article(username):
    """
    从文章文本提取单词，返回匹配词条列表（不直接加入待复习）：
    - 免费版：按空格分词后去查 CSV
    - 付费版：用 DeepSeek 提取原形，再查 CSV
    前端拿到词条列表后注入选框，让用户确认后再加入待复习。
    """
    data = request.get_json(silent=True) or {}
    text = str(data.get('text', '')).strip()
    if not text:
        return jsonify({'error': '文章内容不能为空'}), 400
    if len(text) > 20000:
        return jsonify({'error': '文章内容过长（最多20000字符）'}), 400

    plan = get_user_plan(username)
    if plan == 'paid' and get_deepseek_api_key():
        lemmas = deepseek_extract_lemmas(text)
        if lemmas is None:
            return jsonify({'error': 'DeepSeek API 调用失败，请稍后重试'}), 500
        method = 'deepseek'
    else:
        # 免费版：按空格和标点分词
        raw_words = re.findall(r"[a-zA-Z']+", text)
        lemmas = list({w.lower().strip("'") for w in raw_words if len(w) >= 2})
        method = 'simple'

    csv_set = get_csv_english_set()
    matched_keys = [w for w in lemmas if w in csv_set]
    if not matched_keys:
        return jsonify({
            'message': '未在词库中找到匹配词汇',
            'method': method,
            'words': [],
        }), 200

    # 返回完整词条数据，供前端注入选框
    words = []
    for en in matched_keys:
        row = lookup_csv_word(en)
        if row:
            words.append(row)

    return jsonify({
        'message': f'从文章提取到 {len(words)} 个匹配词汇，请勾选后加入待复习',
        'method': method,
        'words': words,
    }), 200


@app.route('/api/wordbank/csv/import-words', methods=['POST'])
@token_required
def import_vocab_to_csv(username):
    """
    词汇导入功能（仅付费版）：
    - 接收逗号分隔的单词列表
    - 查找 CSV 中没有的词
    - 用 DeepSeek 生成完整词条
    - append 到 CSV
    - 同时加入用户待复习
    """
    if not is_paid_user(username):
        return jsonify({'error': '词汇导入功能仅限付费版用户使用'}), 403

    data = request.get_json(silent=True) or {}
    raw = str(data.get('words', '')).strip()
    level_hint = str(data.get('level', '')).strip()  # 用户指定的level（可选）
    also_queue = bool(data.get('also_add_to_queue', True))

    if not raw:
        return jsonify({'error': '单词列表不能为空'}), 400

    # 解析逗号分隔（支持 a,b 和 a, b）
    input_words = [w.strip().lower() for w in re.split(r'[,，]', raw) if w.strip()]
    if not input_words:
        return jsonify({'error': '未解析到有效单词'}), 400
    if len(input_words) > 500:
        return jsonify({'error': '单次最多处理 500 个单词'}), 400

    if not get_deepseek_api_key():
        return jsonify({'error': '服务端未配置 DEEPSEEK_API_KEY，无法使用此功能'}), 503

    # 查找哪些不在 CSV 中
    existing = get_csv_english_set()
    new_words = [w for w in input_words if w not in existing]
    already_in_csv = [w for w in input_words if w in existing]

    generated_entries = []
    failed_words = []

    if new_words:
        # 分批调用 DeepSeek（每批最多 50 个）
        for i in range(0, len(new_words), 50):
            batch = new_words[i : i + 50]
            entries = deepseek_generate_word_entries(batch, level=level_hint)
            if entries:
                # 验证和清理返回数据
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    en = str(entry.get('english', '')).strip().lower()
                    if not en or en not in [w.lower() for w in batch]:
                        continue
                    # 如果用户指定了level，覆盖DeepSeek生成的level
                    if level_hint:
                        entry['level'] = level_hint
                    generated_entries.append(entry)
            else:
                failed_words.extend(batch)

        if generated_entries:
            try:
                append_words_to_csv(generated_entries)
                invalidate_words_csv_cache()
            except Exception as e:
                logger.error("写入CSV失败: %s", e)
                return jsonify({'error': f'写入词库失败: {e}'}), 500

    # 加入待复习
    queue_result = None
    if also_queue:
        items_to_queue = []
        # 已在CSV的词
        for en in already_in_csv:
            row = lookup_csv_word(en)
            if row:
                picked = pick_example_for_word(row)
                items_to_queue.append({
                    'english': picked['english'],
                    'chinese': picked['chinese'],
                    'example': picked['example'],
                })
        # 新生成的词
        for entry in generated_entries:
            picked = pick_example_for_word(entry)
            items_to_queue.append({
                'english': picked['english'],
                'chinese': picked['chinese'],
                'example': picked['example'],
            })
        if items_to_queue:
            try:
                with user_reciter_session(username) as reciter:
                    queue_result = reciter.add_words_from_dicts(items_to_queue)
            except Exception as e:
                logger.error("加入待复习失败: %s", e)

    msg = f"处理 {len(input_words)} 个单词：{len(generated_entries)} 个新词已写入词库"
    if already_in_csv:
        msg += f"，{len(already_in_csv)} 个已在词库中"
    if failed_words:
        msg += f"，{len(failed_words)} 个生成失败"
    if queue_result:
        msg += f"；已加入待复习 {queue_result.get('added', 0)} 个"

    return jsonify({
        'message': msg,
        'new_in_csv': len(generated_entries),
        'already_in_csv': len(already_in_csv),
        'failed': failed_words,
        'queue_result': queue_result,
    }), 200


@app.route('/api/wordbank/community', methods=['GET'])
@token_required
def get_community_wordbank(username):
    """返回全站共享词库（家长贡献），供导入页勾选。"""
    with _community_wb_lock:
        data = _read_community_file_unlocked()
    return jsonify(
        {
            "schema": data.get("schema"),
            "phase": "community",
            "label": data.get("label", "共享（家长贡献）"),
            "count": len(data.get("words") or []),
            "words": data.get("words") or [],
        }
    ), 200


@app.route('/api/wordbank/community/import-simple', methods=['POST'])
@token_required
def community_import_simple(username):
    """
    家长简易导入：单词 + 例句 + 译文 → 写入共享词库。
    若英文已出现在小学/初中/高中系统词库，或已在共享词库中，则拒绝/跳过并返回明细。
    可选 also_add_to_queue：同时将新词加入当前用户待复习。
    """
    if not _rate_allow(f"comm_import:{_client_ip()}", 40):
        return jsonify({"error": "导入请求过于频繁，请稍后再试"}), 429
    body = request.get_json(silent=True) or {}
    text = str(body.get("text", "")).strip()
    also_queue = bool(body.get("also_add_to_queue"))

    rows, parse_err = parse_simple_parent_import_text(text)
    if parse_err:
        return jsonify({"error": parse_err}), 400
    if len(rows) > 500:
        return jsonify({"error": "单次最多导入 500 条"}), 400

    system_keys = load_system_wordbank_english_lower()
    added_entries: List[dict] = []
    rejected_in_system: List[str] = []
    skipped_duplicate_community: List[str] = []
    skipped_invalid = 0

    with _community_wb_lock:
        data = _read_community_file_unlocked()
        words: List[dict] = list(data.get("words") or [])
        comm_keys = {str(w.get("english", "")).strip().lower() for w in words if w.get("english")}

        for row in rows:
            en = str(row.get("english", "")).strip()[:500]
            zh = str(row.get("chinese", "")).strip()[:500]
            ex_raw = row.get("example")
            ex = str(ex_raw).strip()[:4000] if ex_raw is not None else ""
            if not en or not zh:
                skipped_invalid += 1
                continue
            key = en.lower()
            if key in system_keys:
                rejected_in_system.append(en)
                continue
            if key in comm_keys:
                skipped_duplicate_community.append(en)
                continue
            entry = {
                "english": en,
                "chinese": zh,
                "example": ex or None,
                "added_by": username,
                "added_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            words.append(entry)
            comm_keys.add(key)
            added_entries.append(entry)

        data["words"] = words
        if added_entries:
            _write_community_file_atomic(data)

    queue_result: Optional[dict] = None
    queue_error: Optional[str] = None
    if also_queue and added_entries:
        to_queue = [
            {"english": e["english"], "chinese": e["chinese"], "example": e.get("example")}
            for e in added_entries
        ]
        try:
            with user_reciter_session(username) as reciter:
                queue_result = reciter.add_words_from_dicts(to_queue)
        except Exception as e:
            logger.error("简易导入后加入待复习失败: %s", e)
            queue_error = str(e)

    msg_parts = [f"共享词库新增 {len(added_entries)} 个单词"]
    if rejected_in_system:
        msg_parts.append(f"{len(rejected_in_system)} 个因已在系统词库中未加入")
    if skipped_duplicate_community:
        msg_parts.append(f"{len(skipped_duplicate_community)} 个已在共享词库中")
    if skipped_invalid:
        msg_parts.append(f"{skipped_invalid} 条缺少单词或译文已忽略")

    msg = "；".join(msg_parts) + "。"
    if queue_result:
        msg += (
            f" 待复习：新加 {queue_result.get('added', 0)}，"
            f"跳过重复 {queue_result.get('skipped_duplicate', 0)}。"
        )
    if queue_error:
        msg += " 共享词库已保存，但加入待复习失败，请稍后在共享词库中勾选导入。"

    logger.info(
        "用户 %s 共享词库简易导入: added=%s sys=%s dup=%s invalid=%s queue=%s",
        username,
        len(added_entries),
        len(rejected_in_system),
        len(skipped_duplicate_community),
        skipped_invalid,
        bool(queue_result),
    )

    payload: dict = {
        "message": msg,
        "added_to_community": len(added_entries),
        "rejected_in_system": rejected_in_system,
        "skipped_duplicate_community": skipped_duplicate_community,
        "skipped_invalid": skipped_invalid,
    }
    if queue_result:
        payload["queue_added"] = queue_result.get("added")
        payload["queue_skipped_duplicate"] = queue_result.get("skipped_duplicate")
        payload["queue_skipped_invalid"] = queue_result.get("skipped_invalid")
    if queue_error:
        payload["queue_error"] = queue_error
    return jsonify(payload), 200


@app.route('/api/words/mastered', methods=['GET'])
@token_required
def get_mastered_words(username):
    """获取已掌握单词"""
    try:
        with user_reciter_session(username) as reciter:
            words = []
            for w in reciter.mastered_words:
                csv_row = lookup_csv_word(w.english)
                words.append({
                    'english': w.english,
                    'chinese': w.chinese,
                    'phonetic': csv_row.get('phonetic', '') if csv_row else '',
                    'review_count': w.review_count,
                    'mastered_date': w.next_review_date.isoformat()
                })

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
            'plan': u.get('plan', 'free'),
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


@app.route('/api/admin/config', methods=['GET'])
@admin_required
def admin_get_config():
    """读取 config.json（敏感字段脱敏显示）。"""
    cfg = _load_app_config()
    key = str(cfg.get("deepseek_api_key", "") or "").strip()
    return jsonify({
        "deepseek_api_key_set": bool(key),
        "deepseek_api_key_preview": (key[:8] + "…" if len(key) > 8 else ("（已设置）" if key else "")),
    }), 200


@app.route('/api/admin/config', methods=['PATCH'])
@admin_required
def admin_update_config():
    """更新 config.json 中的运行时配置（目前支持 deepseek_api_key）。"""
    data = request.get_json(silent=True) or {}
    cfg = _load_app_config()
    changed = False
    if "deepseek_api_key" in data:
        new_key = str(data["deepseek_api_key"] or "").strip()
        cfg["deepseek_api_key"] = new_key
        changed = True
    if not changed:
        return jsonify({"error": "没有可更新的字段"}), 400
    try:
        _save_app_config(cfg)
    except Exception as e:
        logger.error("保存 config.json 失败: %s", e)
        return jsonify({"error": "保存失败"}), 500
    logger.info("管理员更新了 config.json")
    return jsonify({"message": "配置已保存"}), 200


@app.route('/api/admin/users/<username>/plan', methods=['PATCH'])
@admin_required
def admin_set_user_plan(username):
    """管理员设置用户套餐（free/paid）。"""
    if not is_valid_username(username):
        return jsonify({'error': '无效的用户名'}), 400
    data = request.get_json(silent=True) or {}
    plan = str(data.get('plan', '')).strip()
    if plan not in ('free', 'paid'):
        return jsonify({'error': "plan 须为 'free' 或 'paid'"}), 400
    if not set_user_plan(username, plan):
        return jsonify({'error': '用户不存在'}), 404
    logger.info("管理员设置用户 %s 套餐为 %s", username, plan)
    return jsonify({'username': username, 'plan': plan}), 200


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
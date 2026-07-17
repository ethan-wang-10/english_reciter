#!/usr/bin/env python3
"""
简化版Web应用 - 智能英语背诵系统
使用Flask替代FastAPI，简化依赖和架构
支持多用户、跨平台访问
"""

import os
import sys
import atexit
import csv
import json
import re
import tempfile
import base64
import gzip
import hashlib
import secrets
import shutil
import smtplib
import ssl
import subprocess
import threading
import urllib.error
import urllib.request
from collections import defaultdict
from contextlib import contextmanager
from datetime import timedelta
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from io import BytesIO, StringIO
from pathlib import Path
from functools import wraps
from typing import Any, Dict, Generator, List, Optional, Set, Tuple
from time import sleep, time
import uuid
from urllib.parse import quote

from flask import Flask, request, jsonify, send_file, send_from_directory, Response, g, stream_with_context
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash

# 导入核心功能
from reciter import (
    DEFAULT_DAILY_REVIEW_LIMIT,
    MAX_DAILY_REVIEW_LIMIT,
    WordReciter,
    Config,
    exercise_attempt_limit,
    get_logger,
)
import gamification as gamification_mod
import challenges as challenges_mod
import leaderboard_periods as leaderboard_periods_mod
import chat_room
import wordbank_v2
import gaokao_questions
from review_scheduler import EXERCISE_TYPES, ReviewEventConflict
from project_paths import STATIC_WB_DIR, WORDS_INTERPROCESS_LOCKFILE
from auth_session_store import (
    SESSION_KIND_ADMIN,
    SESSION_KIND_PASSWORD_RESET,
    SESSION_KIND_PASSWORD_RESET_COOLDOWN,
    SESSION_KIND_USER,
    close_connection as close_auth_session_sqlite,
    create_session as _db_create_auth_session,
    create_session_if_absent,
    consume_session,
    init_auth_session_store,
    revoke_principal,
    revoke_token,
    verify_session,
)
from user_store import (
    DEFAULT_INVITE_QUOTA,
    close_connection as close_user_store_sqlite,
    get_user,
    init_user_store,
    load_users,
    mutate_users,
    update_password_hash,
)
from app_time import china_now_iso, china_today
from performance_store import (
    PERFORMANCE_SHARE_MAX_TTL_SEC,
    backend_sample_rate,
    browser_sample_rate,
    is_valid_performance_log_name,
    list_performance_logs,
    max_report_bytes,
    max_report_events,
    performance_enabled,
    performance_log_dir,
    sign_performance_log_name,
    should_sample,
    slow_request_threshold_ms,
    verify_performance_share_signature,
    write_performance_events,
)

try:
    from tts_piper import piper_runtime_ready, piper_synthesize_wav
except ImportError:
    piper_runtime_ready = None  # type: ignore[misc, assignment]
    piper_synthesize_wav = None  # type: ignore[misc, assignment]

try:
    from PIL import Image as PILImage
except ImportError:
    PILImage = None  # type: ignore

try:
    import pytesseract
    from pytesseract.pytesseract import TesseractNotFoundError
except ImportError:
    pytesseract = None  # type: ignore[misc, assignment]

    class TesseractNotFoundError(Exception):
        """pytesseract 未安装时的占位，避免 except 误捕 RuntimeError。"""

        pass

try:
    import spacy
except ImportError:
    spacy = None  # type: ignore


def _load_dotenv_from_file() -> None:
    """从项目根目录 .env 注入环境变量（不覆盖已有非空值）。

    Gunicorn worker 有时不会继承 PM2 传入的环境；与 ecosystem.config.cjs 行为对齐。
    """
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.is_file():
        return
    try:
        # utf-8-sig：避免 Windows 记事本等保存的 BOM 导致首行键名变成 \ufeffSECRET_KEY
        raw = env_path.read_text(encoding="utf-8-sig")
    except OSError:
        return
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        if not key:
            continue
        if os.environ.get(key):
            continue
        os.environ[key] = val


_load_dotenv_from_file()

# 头像：磁盘仅保留 avatar.webp；长边上限；GET ?w= 为按需缩略图（不传则原图）
AVATAR_MAX_SIDE = 512
AVATAR_WEBP_QUALITY = 82
AVATAR_THUMB_WEBP_QUALITY = 72
AVATAR_THUMB_MAX = 512
AVATAR_THUMB_MIN = 32

# 日志配置
logger = get_logger(__name__)

# Flask应用
app = Flask(__name__, static_folder=None)
_secret = os.getenv("SECRET_KEY")
if os.getenv("FLASK_ENV", "").lower() == "production" and not _secret:
    raise RuntimeError(
        "生产环境必须设置 SECRET_KEY：在项目根目录创建 .env（见 .env.example），"
        "或设置环境变量 SECRET_KEY（docker-compose / systemd / PM2）"
    )
app.secret_key = _secret or secrets.token_urlsafe(32)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB上传限制
CORS(app, supports_credentials=True)


@app.after_request
def _gzip_large_text_responses(response: Response) -> Response:
    """压缩首屏文本资源和较大的 JSON，缩短慢网络首次打开时间。"""
    if response.status_code != 200 or request.method != "GET":
        return response
    if response.headers.get("Content-Encoding") or request.headers.get("Range"):
        return response
    ae = request.headers.get("Accept-Encoding") or ""
    if "gzip" not in ae.lower():
        return response

    ct = (response.headers.get("Content-Type") or "").lower()
    path = request.path or ""
    text_like = (
        "text/html" in ct
        or "text/css" in ct
        or "javascript" in ct
        or path.endswith((".js", ".css", ".html"))
    )
    json_like = "application/json" in ct
    if not (text_like or json_like):
        return response

    try:
        if response.direct_passthrough:
            response.direct_passthrough = False
        data = response.get_data()
    except Exception:
        return response
    if len(data) < 2048:
        return response
    gz = gzip.compress(data, compresslevel=6)
    if len(gz) >= len(data):
        return response
    response.set_data(gz)
    response.headers["Content-Encoding"] = "gzip"
    response.headers["Content-Length"] = str(len(gz))
    response.headers.add("Vary", "Accept-Encoding")
    return response

# 用户名：防止路径穿越与非法目录名，仅允许字母数字下划线
USERNAME_PATTERN = re.compile(r'^[a-zA-Z0-9_]{3,32}$')

USER_ROLE_PARENT = "parent"
PARENT_LOGIN_SUFFIX = "_parent"
DEFAULT_PARENT_PASSWORD = "123123"
SESSION_KIND_CHAT_STREAM = "chat_stream"
USER_SESSION_TTL = timedelta(days=30)
PASSWORD_RESET_TTL_MINUTES_DEFAULT = 30
PASSWORD_RESET_COOLDOWN_SECONDS_DEFAULT = 60
ADMIN_SESSION_TTL = timedelta(hours=8)
CHAT_STREAM_SESSION_TTL = timedelta(minutes=2)
USER_INVITE_QUOTA_DEFAULT = DEFAULT_INVITE_QUOTA

# 数据目录
DATA_DIR = Path(os.getenv("ENGLISH_RECITER_DATA_DIR", "user_data_simple")).expanduser()
DATA_DIR.mkdir(parents=True, exist_ok=True)
init_auth_session_store(DATA_DIR)
init_user_store(DATA_DIR)


def _atexit_close_app_sqlite() -> None:
    """进程退出时关闭 SQLite，减轻 Gunicorn gthread 与解释器 shutdown 竞态下的噪音日志。"""
    try:
        close_auth_session_sqlite()
    except Exception:
        pass
    try:
        close_user_store_sqlite()
    except Exception:
        pass


atexit.register(_atexit_close_app_sqlite)

# 全站共享词库（家长贡献，持久化在 user_data_simple/_shared/）
SHARED_DATA_DIR = DATA_DIR / "_shared"
COMMUNITY_WB_FILE = SHARED_DATA_DIR / "community_wordbank.json"
SYSTEM_BROADCAST_FILE = SHARED_DATA_DIR / "system_broadcast.json"
_SYSTEM_BROADCAST_SCHEMA = "english_reciter.system_broadcast/v1"
_SYSTEM_BROADCAST_LOCK = threading.Lock()
SYSTEM_BROADCAST_MAX_LEN = 8000
_community_wb_lock = threading.Lock()
_COMMUNITY_SCHEMA = "english_reciter.wordbank.community/v2"
_COMMUNITY_PROMOTION_STATUSES = {"pending", "failed", "promoted"}

# 新 CSV 词汇表路径（STATIC_WB_DIR 见 project_paths，与 wordbank_v2 一致）
WORDS_CSV_FILE = STATIC_WB_DIR / "words.csv"
TEXTBOOKS_INDEX_PATH = STATIC_WB_DIR / "textbooks" / "index.json"
_words_csv_lock = threading.Lock()
_words_csv_cache: Optional[List[dict]] = None
_words_csv_cache_mtime: float = 0.0
_words_csv_by_key_cache: Optional[Dict[str, dict]] = None
_words_csv_by_key_cache_mtime: float = -1.0

# 跨进程互斥：threading.Lock 仅同进程内有效；多进程/多脚本写 words.csv 需文件锁（路径见 project_paths）

# merge_wordbank_rows_for_search 结果：按词库文件 mtime 失效，按难度键缓存多档
_merge_wordbank_rows_lock = threading.Lock()
_merge_wordbank_rows_cache: Dict[str, Tuple[List[dict], Set[str]]] = {}
_merge_wordbank_rows_cache_rev: Tuple[float, float] = (-1.0, -1.0)


def _safe_request_path() -> str:
    path = request.path or ""
    if not path.startswith("/"):
        path = "/" + path
    return path[:240]


def _safe_request_endpoint() -> str:
    return str(request.endpoint or "")[:120]


def _safe_user_agent() -> str:
    return (request.headers.get("User-Agent") or "")[:300]


def _safe_referrer_path() -> str:
    raw = request.headers.get("Referer") or request.headers.get("Referrer") or ""
    if not raw:
        return ""
    try:
        from urllib.parse import urlsplit

        parts = urlsplit(raw)
        return (parts.path or "/")[:240]
    except Exception:
        return raw[:240]


def _performance_user_context() -> dict:
    out = {
        "login_username": getattr(g, "login_username", None),
        "effective_username": getattr(g, "effective_username", None),
        "is_parent": getattr(g, "is_parent", None),
    }
    return {k: v for k, v in out.items() if v is not None}


def _record_performance_event(event: dict) -> None:
    if not performance_enabled():
        return
    try:
        write_performance_events(DATA_DIR, [event])
    except Exception as exc:
        logger.debug("写入性能采集日志失败: %s", exc)


@app.before_request
def _performance_before_request() -> None:
    g.request_started_at = time()
    rid = (request.headers.get("X-Request-ID") or "").strip()
    g.request_id = rid[:80] if rid else uuid.uuid4().hex


@app.after_request
def _performance_after_request(response: Response) -> Response:
    rid = getattr(g, "request_id", "")
    if rid:
        response.headers["X-Request-ID"] = rid

    if not performance_enabled():
        return response
    path = _safe_request_path()
    if path == "/api/performance/report" or path.startswith("/api/chat/stream"):
        return response
    started_at = getattr(g, "request_started_at", None)
    if not isinstance(started_at, (int, float)):
        return response
    duration_ms = round((time() - started_at) * 1000.0, 1)
    status = int(response.status_code or 0)
    slow = duration_ms >= slow_request_threshold_ms()
    failed = status >= 500
    sampled = should_sample(backend_sample_rate())
    if not (slow or failed or sampled):
        return response

    event = {
        "source": "server",
        "type": "http_request",
        "request_id": rid,
        "method": request.method,
        "path": path,
        "endpoint": _safe_request_endpoint(),
        "status": status,
        "duration_ms": duration_ms,
        "slow": slow,
        "content_length": response.content_length,
        "remote_addr": _client_ip() if request else "",
        "user_agent": _safe_user_agent(),
        "referrer_path": _safe_referrer_path(),
        "user": _performance_user_context(),
    }
    _record_performance_event(event)
    return response


@app.teardown_request
def _performance_teardown_request(exc: Optional[BaseException]) -> None:
    if exc is not None and performance_enabled():
        event = {
            "source": "server",
            "type": "exception",
            "request_id": getattr(g, "request_id", ""),
            "method": request.method if request else "",
            "path": _safe_request_path() if request else "",
            "endpoint": _safe_request_endpoint() if request else "",
            "duration_ms": round((time() - getattr(g, "request_started_at", time())) * 1000.0, 1),
            "error_class": exc.__class__.__name__,
            "error": str(exc)[:500],
            "user": _performance_user_context(),
        }
        _record_performance_event(event)


@contextmanager
def _words_csv_interprocess_lock() -> Generator[None, None, None]:
    """独占锁，保护 words.csv 的读与写（多进程安全）。

    与 _words_csv_lock 嵌套时必须先本锁、再线程锁，避免与 load_words_csv 缓存未命中路径死锁。"""
    WORDS_INTERPROCESS_LOCKFILE.parent.mkdir(parents=True, exist_ok=True)
    f = open(WORDS_INTERPROCESS_LOCKFILE, "a+b", buffering=0)
    try:
        if sys.platform == "win32":
            import msvcrt

            f.seek(0, os.SEEK_END)
            if f.tell() == 0:
                f.write(b"\0")
                f.flush()
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if sys.platform == "win32":
            import msvcrt

            try:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        else:
            import fcntl

            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        try:
            f.close()
        except OSError:
            pass

# DeepSeek API
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
# 单次请求：大 JSON 输出易超过 60s；可通过环境变量覆盖
DEEPSEEK_HTTP_TIMEOUT_SEC = int(os.getenv("DEEPSEEK_HTTP_TIMEOUT_SEC", "120"))
DEEPSEEK_HTTP_RETRIES = max(1, int(os.getenv("DEEPSEEK_HTTP_RETRIES", "3")))
DEEPSEEK_RETRY_BACKOFF_SEC = float(os.getenv("DEEPSEEK_RETRY_BACKOFF_SEC", "2"))
# 词汇批量生成时，每批结束后暂停（秒），减轻限流；默认 0
DEEPSEEK_BATCH_PAUSE_SEC = float(os.getenv("DEEPSEEK_BATCH_PAUSE_SEC", "0") or 0)
# deepseek-chat 将于 2026/07/24 弃用；默认 deepseek-v4-flash + 关闭思考（与旧 chat 行为一致）。可用 DEEPSEEK_CHAT_MODEL 覆盖。
DEEPSEEK_CHAT_MODEL = (os.getenv("DEEPSEEK_CHAT_MODEL", "deepseek-v4-flash") or "deepseek-v4-flash").strip()
_APP_CONFIG_FILE = Path("config.json")

# 从 config 解密的 DeepSeek Key 短期缓存（秒），避免每次 API 调用读盘 + 解密
_DEEPSEEK_KEY_CACHE_TTL_SEC = 60.0
_deepseek_key_cache_ts: float = 0.0
_deepseek_key_cache_val: str = ""


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


# config.json 中 deepseek_api_key 的密文前缀（Fernet）；旧版明文无此前缀
_DEEPSEEK_ENC_PREFIX = "er-enc:v1:"


def _master_secret_for_deepseek_crypto() -> str:
    """
    用于派生 Fernet 密钥。优先专用变量，其次环境变量 SECRET_KEY；
    若均未设置，则使用 Flask app.secret_key（开发时常见），便于本地保存密文。
    生产环境请固定设置 SECRET_KEY 或 DEEPSEEK_KEY_ENCRYPTION_SECRET，否则重启后随机 secret 会导致无法解密。
    """
    s = os.getenv("DEEPSEEK_KEY_ENCRYPTION_SECRET", "").strip()
    if s:
        return s
    s = os.getenv("SECRET_KEY", "").strip()
    if s:
        return s
    try:
        sk = app.secret_key
        if sk is not None and str(sk).strip():
            return str(sk)
    except Exception:
        pass
    return ""


def _fernet_for_deepseek_storage():
    """用于加密/解密写入 config.json 的 DeepSeek API Key。"""
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        logger.warning("未安装 cryptography，无法使用 DeepSeek API Key 加密存储")
        return None
    master = _master_secret_for_deepseek_crypto()
    if not master:
        return None
    key = base64.urlsafe_b64encode(
        hashlib.sha256((master + "|english_reciter.deepseek.v1").encode("utf-8")).digest()
    )
    return Fernet(key)


def _decrypt_deepseek_from_config(raw: str) -> str:
    """将 config 中的字段解密为明文 Key；明文旧配置或解密失败时返回可解析结果。"""
    raw = (raw or "").strip()
    if not raw:
        return ""
    if not raw.startswith(_DEEPSEEK_ENC_PREFIX):
        return raw
    f = _fernet_for_deepseek_storage()
    if f is None:
        logger.error(
            "config.json 中 DeepSeek API Key 已加密，但未安装 cryptography 或无法派生密钥，无法解密",
        )
        return ""
    token = raw[len(_DEEPSEEK_ENC_PREFIX) :].encode("utf-8")
    try:
        return f.decrypt(token).decode("utf-8")
    except Exception as e:
        logger.error("DeepSeek API Key 解密失败（请确认 SECRET_KEY / DEEPSEEK_KEY_ENCRYPTION_SECRET 与加密时一致）: %s", e)
        return ""


def _encrypt_deepseek_for_config(plaintext: str) -> str:
    try:
        import cryptography.fernet  # noqa: F401
    except ImportError:
        raise RuntimeError(
            "未安装 cryptography，无法加密保存 API Key。请执行: pip install -r requirements-simple.txt"
        ) from None
    f = _fernet_for_deepseek_storage()
    if f is None:
        raise RuntimeError(
            "无法派生加密密钥：请设置环境变量 SECRET_KEY 或 DEEPSEEK_KEY_ENCRYPTION_SECRET；"
            " 本地开发未设置时可在运行中的 Web 进程内保存（将使用 Flask app.secret_key）；"
            " 生产环境务必固定 SECRET_KEY，否则重启后无法解密已存密文。"
        ) from None
    return _DEEPSEEK_ENC_PREFIX + f.encrypt(plaintext.encode("utf-8")).decode("ascii")


def get_deepseek_api_key() -> str:
    """读取 DeepSeek API Key：环境变量 DEEPSEEK_API_KEY 优先；其次 config.json（可密文），带短期内存缓存。"""
    env_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if env_key:
        return env_key
    global _deepseek_key_cache_ts, _deepseek_key_cache_val
    now = time()
    if _deepseek_key_cache_val and (now - _deepseek_key_cache_ts) < _DEEPSEEK_KEY_CACHE_TTL_SEC:
        return _deepseek_key_cache_val
    raw = str(_load_app_config().get("deepseek_api_key", "") or "").strip()
    _deepseek_key_cache_val = _decrypt_deepseek_from_config(raw)
    _deepseek_key_cache_ts = now
    return _deepseek_key_cache_val


def _article_ai_extract_enabled() -> bool:
    """管理后台开启后，管理员可凭管理员会话在课文导入中使用 AI 分词；普通用户统一 spaCy。"""
    return bool(_load_app_config().get("article_ai_extract_enabled", False))


def _admin_token_from_request() -> str:
    """课文导入等场景：用户 JWT 与管理员 token 分开发送。"""
    h = (request.headers.get("X-Admin-Token") or "").strip()
    if h:
        return h
    data = request.get_json(silent=True) or {}
    return str(data.get("admin_token", "") or "").strip()


# 动态读取（每次调用 get_deepseek_api_key() 而不是模块级常量）
DEEPSEEK_API_KEY = ""  # 保持兼容，实际使用 get_deepseek_api_key()

# CSV 字段
_CSV_FIELDS = ["english", "chinese", "level", "phonetic",
               "example1", "example1_form", "example1_cn",
               "example2", "example2_form", "example2_cn"]

# 「单词学习」等场景仅需释义与例句，可省略 example*_form 以减小 JSON。
# 多义项 v2 最多 8 条；须包含 example3..8，否则多义卡片第 3 条及以后会缺例句。
_WORDBANK_CSV_MINIMAL_FIELDS = (
    "english", "chinese", "level", "phonetic",
    "example1", "example1_cn", "example2", "example2_cn",
    "example3", "example3_cn", "example4", "example4_cn",
    "example5", "example5_cn", "example6", "example6_cn",
    "example7", "example7_cn", "example8", "example8_cn",
)


def _wordbank_csv_row_minimal(row: dict) -> dict:
    out = {k: str(row.get(k, "") or "") for k in _WORDBANK_CSV_MINIMAL_FIELDS}
    csl = row.get("chinese_sense_lines")
    if isinstance(csl, list) and csl:
        out["chinese_sense_lines"] = [str(x).strip() for x in csl if str(x).strip()]
    return out


def _empty_community_doc() -> dict:
    return {
        "schema": _COMMUNITY_SCHEMA,
        "phase": "community",
        "label": "社区词库（待审核）",
        "description": "家长贡献的候选词条；通过补全和校验后提升到 words_v2.json",
        "version": 2,
        "count": 0,
        "words": [],
    }


def _community_word_key(value: object) -> str:
    return wordbank_v2.normalize_english_key(str(value or ""))


def _normalize_community_entry(raw: dict) -> dict:
    """兼容 v1 社区词条，并补齐可重入提升所需的状态字段。"""
    entry = dict(raw)
    status = str(entry.get("status") or "pending").strip().lower()
    if status not in _COMMUNITY_PROMOTION_STATUSES:
        status = "pending"
    try:
        attempts = max(0, int(entry.get("promotion_attempts") or 0))
    except (TypeError, ValueError):
        attempts = 0
    entry["status"] = status
    entry["promotion_attempts"] = attempts
    entry["last_attempt_at"] = entry.get("last_attempt_at") or None
    entry["last_error"] = entry.get("last_error") or None
    entry["promoted_at"] = entry.get("promoted_at") or None
    entry["promoted_word_key"] = entry.get("promoted_word_key") or None
    return entry


@contextmanager
def _community_wb_interprocess_lock() -> Generator[None, None, None]:
    """跨进程保护社区词库；与线程锁同时使用时必须先获取本锁。"""
    lock_path = COMMUNITY_WB_FILE.parent / ".community_wordbank.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    f = open(lock_path, "a+b", buffering=0)
    try:
        if sys.platform == "win32":
            import msvcrt

            f.seek(0, os.SEEK_END)
            if f.tell() == 0:
                f.write(b"\0")
                f.flush()
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if sys.platform == "win32":
            import msvcrt

            try:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        else:
            import fcntl

            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        try:
            f.close()
        except OSError:
            pass


@contextmanager
def _community_wb_guard() -> Generator[None, None, None]:
    with _community_wb_interprocess_lock():
        with _community_wb_lock:
            yield


def _read_community_file_unlocked() -> dict:
    """读取共享词库（调用方需已持锁或保证无并发写）。"""
    COMMUNITY_WB_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not COMMUNITY_WB_FILE.exists():
        data = _empty_community_doc()
        _write_community_file_atomic(data)
        return data
    try:
        with open(COMMUNITY_WB_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error("社区词库无法读取，已拒绝覆盖原文件: %s", e)
        raise RuntimeError("社区词库无法读取，已保留原文件，请先修复或恢复备份") from e
    if not isinstance(raw, dict):
        raise ValueError("社区词库根节点必须是 JSON 对象，已拒绝覆盖原文件")
    words = raw.get("words")
    if not isinstance(words, list):
        raise ValueError("社区词库 words 字段必须是数组，已拒绝覆盖原文件")
    raw["words"] = [_normalize_community_entry(w) for w in words if isinstance(w, dict)]
    raw["schema"] = _COMMUNITY_SCHEMA
    raw["version"] = 2
    raw["phase"] = "community"
    raw["label"] = "社区词库（待审核）"
    raw["description"] = "家长贡献的候选词条；通过补全和校验后提升到 words_v2.json"
    raw["count"] = len(raw["words"])
    return raw


def _write_community_file_atomic(data: dict) -> None:
    path = COMMUNITY_WB_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dict(data)
    words = data.get("words")
    if not isinstance(words, list):
        words = []
    data["words"] = words
    data["count"] = len(words)
    data["schema"] = _COMMUNITY_SCHEMA
    data["version"] = 2
    fd, tmp_name = tempfile.mkstemp(suffix=".json", dir=str(path.parent), text=True)
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


def read_community_wordbank_snapshot() -> dict:
    """读取社区待审区快照；返回值可在锁外用于耗时 AI 调用。"""
    with _community_wb_guard():
        return _read_community_file_unlocked()


def merge_community_promotion_updates(updates: Dict[str, dict]) -> int:
    """按规范化英文键合并提升状态，保留 AI 调用期间新导入的词条。"""
    normalized_updates = {
        _community_word_key(key): dict(update)
        for key, update in updates.items()
        if _community_word_key(key) and isinstance(update, dict)
    }
    if not normalized_updates:
        return 0

    changed = 0
    with _community_wb_guard():
        data = _read_community_file_unlocked()
        words = list(data.get("words") or [])
        for index, raw in enumerate(words):
            entry = _normalize_community_entry(raw)
            key = _community_word_key(entry.get("english"))
            update = normalized_updates.get(key)
            if not update:
                words[index] = entry
                continue

            next_status = str(update.get("status") or entry["status"]).strip().lower()
            if next_status not in _COMMUNITY_PROMOTION_STATUSES:
                next_status = entry["status"]
            # 两个提升进程竞态时，较晚的失败结果不得覆盖已成功的状态。
            if entry["status"] == "promoted" and next_status != "promoted":
                words[index] = entry
                continue

            try:
                increment = max(0, int(update.get("attempt_increment") or 0))
            except (TypeError, ValueError):
                increment = 0
            entry["promotion_attempts"] += increment
            entry["status"] = next_status
            for field in ("last_attempt_at", "last_error", "promoted_at", "promoted_word_key"):
                if field in update:
                    entry[field] = update[field] or None
            words[index] = entry
            changed += 1

        if changed:
            data["words"] = words
            _write_community_file_atomic(data)
    return changed


# 疑难词（AI 导入失败）与管理员维护的词形映射（表面形 -> 词汇原形）
WORDBANK_TROUBLES_FILE = SHARED_DATA_DIR / "wordbank_troubles.json"
_TROUBLES_LOCK = threading.Lock()
_TROUBLES_SCHEMA = "english_reciter.wordbank.troubles/v1"


def _empty_troubles_doc() -> dict:
    return {"schema": _TROUBLES_SCHEMA, "difficult": {}, "mappings": {}}


def _read_troubles_unlocked() -> dict:
    SHARED_DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not WORDBANK_TROUBLES_FILE.exists():
        data = _empty_troubles_doc()
        _write_troubles_file_atomic(data)
        return data
    try:
        with open(WORDBANK_TROUBLES_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error("疑难词文件损坏，将重建: %s", e)
        data = _empty_troubles_doc()
        _write_troubles_file_atomic(data)
        return data
    if not isinstance(raw, dict):
        raw = _empty_troubles_doc()
    diff = raw.get("difficult")
    maps = raw.get("mappings")
    if not isinstance(diff, dict):
        diff = {}
    if not isinstance(maps, dict):
        maps = {}
    raw["difficult"] = {str(k).strip().lower(): v for k, v in diff.items() if str(k).strip()}
    raw["mappings"] = {
        str(k).strip().lower(): str(v).strip().lower()
        for k, v in maps.items()
        if str(k).strip() and str(v).strip()
    }
    raw.setdefault("schema", _TROUBLES_SCHEMA)
    return raw


def _write_troubles_file_atomic(data: dict) -> None:
    SHARED_DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = WORDBANK_TROUBLES_FILE
    data = dict(data)
    diff = data.get("difficult")
    maps = data.get("mappings")
    if not isinstance(diff, dict):
        diff = {}
    if not isinstance(maps, dict):
        maps = {}
    data["difficult"] = diff
    data["mappings"] = maps
    data.setdefault("schema", _TROUBLES_SCHEMA)
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


def _sanitize_broadcast_message(text: str) -> str:
    max_len = SYSTEM_BROADCAST_MAX_LEN
    s = (text or "")[:max_len]
    return "".join(ch for ch in s if ch.isprintable() or ch in "\n\r\t").strip()[:max_len]


def _empty_system_broadcast_doc() -> dict:
    return {"schema": _SYSTEM_BROADCAST_SCHEMA, "id": "", "message": "", "created_at": None}


def _read_system_broadcast_unlocked() -> dict:
    SHARED_DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not SYSTEM_BROADCAST_FILE.exists():
        return _empty_system_broadcast_doc()
    try:
        with open(SYSTEM_BROADCAST_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error("系统广播文件损坏，将重置: %s", e)
        return _empty_system_broadcast_doc()
    if not isinstance(raw, dict):
        return _empty_system_broadcast_doc()
    mid = str(raw.get("id") or "").strip()
    msg = str(raw.get("message") or "")
    if msg:
        msg = _sanitize_broadcast_message(msg)
    cre = raw.get("created_at")
    return {
        "schema": _SYSTEM_BROADCAST_SCHEMA,
        "id": mid,
        "message": msg,
        "created_at": cre if isinstance(cre, str) else None,
    }


def _write_system_broadcast_atomic(data: dict) -> None:
    SHARED_DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = SYSTEM_BROADCAST_FILE
    data = dict(data)
    data.setdefault("schema", _SYSTEM_BROADCAST_SCHEMA)
    mid = str(data.get("id") or "").strip()
    msg = _sanitize_broadcast_message(str(data.get("message") or ""))
    data["id"] = mid
    data["message"] = msg
    cre = data.get("created_at")
    data["created_at"] = cre if isinstance(cre, str) else None
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


def get_system_broadcast() -> dict:
    with _SYSTEM_BROADCAST_LOCK:
        return dict(_read_system_broadcast_unlocked())


def _user_broadcast_ack_path(login_username: str) -> Path:
    return DATA_DIR / login_username / "system_broadcast_ack.json"


def _read_user_broadcast_dismissed_id(login_username: str) -> str:
    path = _user_broadcast_ack_path(login_username)
    if not path.exists():
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return ""
    if not isinstance(raw, dict):
        return ""
    return str(raw.get("dismissed_id") or "").strip()


def _write_user_broadcast_ack(login_username: str, broadcast_id: str) -> None:
    user_dir = DATA_DIR / login_username
    user_dir.mkdir(parents=True, exist_ok=True)
    path = _user_broadcast_ack_path(login_username)
    data = {"dismissed_id": broadcast_id}
    fd, tmp_name = tempfile.mkstemp(suffix=".json", dir=str(user_dir), text=True)
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


def pending_system_broadcast_for_login(login_username: str) -> Optional[dict]:
    """若当前用户尚未确认最新广播，返回 {id, message}，否则 None。"""
    with _SYSTEM_BROADCAST_LOCK:
        doc = _read_system_broadcast_unlocked()
    bid = (doc.get("id") or "").strip()
    msg = (doc.get("message") or "").strip()
    if not bid or not msg:
        return None
    dismissed = _read_user_broadcast_dismissed_id(login_username)
    if dismissed == bid:
        return None
    return {"id": bid, "message": msg}


def get_wordbank_lemma_mappings() -> dict:
    """表面形 -> 词汇原形（小写），供查词与导入解析。"""
    with _TROUBLES_LOCK:
        doc = _read_troubles_unlocked()
    return dict(doc.get("mappings") or {})


def record_surfaces_to_difficult(surfaces: List[str]) -> None:
    """将 AI 未能写入词库的表面形记入疑难词（已有映射的跳过）。"""
    if not surfaces:
        return
    now = china_now_iso(timespec="seconds")
    with _TROUBLES_LOCK:
        data = _read_troubles_unlocked()
        diff = data.setdefault("difficult", {})
        maps = data.setdefault("mappings", {})
        for raw_s in surfaces:
            s = str(raw_s or "").strip().lower()
            if not s or s in maps:
                continue
            prev = diff.get(s)
            if isinstance(prev, dict):
                entry = dict(prev)
            else:
                entry = {}
            entry["attempts"] = int(entry.get("attempts") or 0) + 1
            entry["last_attempt"] = now
            if not entry.get("added_at"):
                entry["added_at"] = now
            diff[s] = entry
        _write_troubles_file_atomic(data)
    logger.info("疑难词记录: %s", surfaces)


def set_wordbank_surface_mapping(surface: str, lemma: str) -> None:
    """管理员设置映射：写入 mapping，并从疑难词中移除该表面形。"""
    s = str(surface or "").strip().lower()
    lem = str(lemma or "").strip().lower()
    if not s or not lem:
        raise ValueError("surface 与 lemma 不能为空")
    with _TROUBLES_LOCK:
        data = _read_troubles_unlocked()
        data.setdefault("mappings", {})[s] = lem
        data.setdefault("difficult", {}).pop(s, None)
        _write_troubles_file_atomic(data)


def delete_wordbank_mapping(surface: str) -> bool:
    s = str(surface or "").strip().lower()
    if not s:
        return False
    with _TROUBLES_LOCK:
        data = _read_troubles_unlocked()
        maps = data.setdefault("mappings", {})
        if s not in maps:
            return False
        del maps[s]
        _write_troubles_file_atomic(data)
    return True


def delete_wordbank_difficult(surface: str) -> bool:
    s = str(surface or "").strip().lower()
    if not s:
        return False
    with _TROUBLES_LOCK:
        data = _read_troubles_unlocked()
        diff = data.setdefault("difficult", {})
        if s not in diff:
            return False
        del diff[s]
        _write_troubles_file_atomic(data)
    return True


def get_wordbank_english_set() -> set:
    """主词库（新 ``words_v2.json`` + 旧 ``words.csv``）英文键集合（规范化小写）。"""
    return get_csv_english_set() | wordbank_v2.get_v2_english_key_set()


def load_system_wordbank_english_lower() -> set:
    """主词库中的英文键，用于「共享词库」与家长导入去重（含 v2 + legacy）。"""
    return get_wordbank_english_set()


def _wordbank_source_mtimes() -> Tuple[float, float]:
    try:
        csv_mtime = WORDS_CSV_FILE.stat().st_mtime if WORDS_CSV_FILE.exists() else 0.0
    except OSError:
        csv_mtime = 0.0
    try:
        v2_mtime = wordbank_v2.WORDS_V2_FILE.stat().st_mtime if wordbank_v2.WORDS_V2_FILE.exists() else 0.0
    except OSError:
        v2_mtime = 0.0
    return (csv_mtime, v2_mtime)


def invalidate_merge_wordbank_rows_cache() -> None:
    """words.csv / words_v2 变更后清空合并缓存（与 _wordbank_source_mtimes 失效一致）。"""
    global _merge_wordbank_rows_cache
    global _merge_wordbank_rows_cache_rev
    with _merge_wordbank_rows_lock:
        _merge_wordbank_rows_cache.clear()
        _merge_wordbank_rows_cache_rev = (-1.0, -1.0)


def merge_wordbank_rows_for_search(level_filter: str = "") -> Tuple[List[dict], Set[str]]:
    """
    合并词库：``words_v2.json`` 为默认数据源，同键覆盖 ``words.csv``；仅 v2 或仅 CSV 的键都会保留。
    用于搜索 API、GET /wordbank/csv 等对外统一词库。
    返回 (扁平行列表, 所有 english 规范化键集合)。

    同进程内按 (words.csv mtime, words_v2.json mtime) + 难度键缓存；文件未变则复用，避免重复合并与 materialize。
    """
    global _merge_wordbank_rows_cache
    global _merge_wordbank_rows_cache_rev
    lv_key = level_filter.strip()

    with _merge_wordbank_rows_lock:
        rev = _wordbank_source_mtimes()
        if rev != _merge_wordbank_rows_cache_rev:
            _merge_wordbank_rows_cache.clear()
            _merge_wordbank_rows_cache_rev = rev
        hit = _merge_wordbank_rows_cache.get(lv_key)
        if hit is not None:
            return hit[0], hit[1]

        csv_rows = load_words_csv()
        if level_filter:
            csv_rows = [r for r in csv_rows if (r.get("level", "") or "").strip() == level_filter]
        v2_list = wordbank_v2.load_words_v2_list()
        if level_filter:
            v2_list = [e for e in v2_list if (e.get("level", "") or "").strip() == level_filter]
        v2_by: Dict[str, dict] = {}
        for e in v2_list:
            k = wordbank_v2.normalize_english_key(e.get("english", ""))
            if k:
                v2_by[k] = e
        out: List[dict] = []
        seen_csv: Set[str] = set()
        for row in csv_rows:
            k = wordbank_v2.normalize_english_key(row.get("english", ""))
            if not k:
                continue
            if k in v2_by:
                out.append(wordbank_v2.v2_entry_to_flat_csv_row(v2_by[k]))
            else:
                out.append(row)
            seen_csv.add(k)
        for k, ent in v2_by.items():
            if k not in seen_csv:
                out.append(wordbank_v2.v2_entry_to_flat_csv_row(ent))
        keys = {
            wordbank_v2.normalize_english_key(str(r.get("english", "") or ""))
            for r in out
            if (r.get("english") or "").strip()
        }
        _merge_wordbank_rows_cache[lv_key] = (out, keys)
        return out, keys


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

def _normalize_words_csv_row(row: dict) -> dict:
    return {k: str(row.get(k, "") or "").strip() for k in _CSV_FIELDS}


def _is_valid_vocab_csv_english_key(en_low: str) -> bool:
    """
    词库 CSV 中 english 键：单段词形，或空格分隔的多词短语（与导入词形校验一致）。
    """
    if not en_low or len(en_low) < 1:
        return False
    if re.match(r"^[a-z][a-z'\-]*$", en_low):
        return True
    return bool(re.match(r"^[a-z][a-z'\-]*(?: [a-z][a-z'\-]*)+$", en_low))


def _is_valid_deepseek_word_entry(entry: object) -> bool:
    """DeepSeek 返回的单条：需含合法 english、非空 chinese，与词库 CSV 字段兼容。"""
    if not isinstance(entry, dict):
        return False
    en = " ".join(str(entry.get("english", "")).strip().lower().split())
    if not en:
        return False
    if not _is_valid_vocab_csv_english_key(en):
        return False
    if not str(entry.get("chinese", "")).strip():
        return False
    return True


def accumulate_valid_deepseek_word_rows(
    entries: Optional[List],
    *,
    level_hint: str,
    csv_so_far: set,
    batch_lower: set,
) -> Tuple[List[dict], set]:
    """
    从 DeepSeek 返回的 JSON 数组中采纳所有格式合法的词条，与 csv_so_far 去重后写入新行。
    模型多返回的其它合法词也会一并纳入（尽量填充词库）。
    返回 (新行列表, 本批请求词 batch_lower 中已得到词条的 english 小写集合)。
    """
    new_rows: List[dict] = []
    success_for_batch: set = set()
    if not entries:
        return new_rows, success_for_batch
    extra_words: List[str] = []
    for entry in entries:
        if not _is_valid_deepseek_word_entry(entry):
            continue
        row = _normalize_words_csv_row(entry)
        en = " ".join(row["english"].strip().lower().split())
        if not en:
            continue
        if en in csv_so_far:
            continue
        row["english"] = en
        if level_hint:
            row["level"] = level_hint
        new_rows.append(row)
        csv_so_far.add(en)
        if en in batch_lower:
            success_for_batch.add(en)
        else:
            extra_words.append(en)
    if extra_words:
        logger.info(
            "DeepSeek 本批除请求列表外另写入词库 %s 条: %s",
            len(extra_words),
            extra_words[:40],
        )
    return new_rows, success_for_batch


def _read_words_csv_from_path(path: Path) -> List[dict]:
    """从磁盘读取 CSV（不经过模块缓存；供 load_words_csv 与需持锁合并时使用）。"""
    if not path.exists():
        return []
    rows: List[dict] = []
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(dict(row))
    except Exception as e:
        logger.error("读取词汇CSV失败: %s", e)
        return []
    return rows


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

    with _words_csv_interprocess_lock():
        with _words_csv_lock:
            try:
                mtime2 = WORDS_CSV_FILE.stat().st_mtime if WORDS_CSV_FILE.exists() else 0.0
            except OSError:
                mtime2 = 0.0
            if _words_csv_cache is not None and mtime2 == _words_csv_cache_mtime:
                return _words_csv_cache
            rows = _read_words_csv_from_path(WORDS_CSV_FILE)
            _words_csv_cache = rows
            _words_csv_cache_mtime = mtime2
            return rows


def load_words_csv_by_key() -> Dict[str, dict]:
    """返回规范化 english -> CSV 行的索引，避免每次查词线性扫描全表。"""
    global _words_csv_by_key_cache, _words_csv_by_key_cache_mtime
    rows = load_words_csv()
    with _words_csv_lock:
        mtime = _words_csv_cache_mtime
        if (
            _words_csv_by_key_cache is not None
            and _words_csv_by_key_cache_mtime == mtime
        ):
            return _words_csv_by_key_cache
        by_key: Dict[str, dict] = {}
        for row in rows:
            k = wordbank_v2.normalize_english_key(row.get("english", ""))
            if k:
                by_key[k] = row
        _words_csv_by_key_cache = by_key
        _words_csv_by_key_cache_mtime = mtime
        return by_key


def _merge_incremental_words_csv(
    server_rows: List[dict], upload_rows: List[dict]
) -> Tuple[List[dict], Dict[str, int]]:
    """
    增量合并：不删除仅存在于服务端的词；上传与服务器同键（english 小写）时以上传行为准；
    上传中多出的新词按上传文件顺序追加在末尾。
    """
    upload_by_key: Dict[str, dict] = {}
    for row in upload_rows:
        clean = _normalize_words_csv_row(row)
        en = clean.get("english", "").strip()
        if not en:
            continue
        upload_by_key[en.lower()] = clean

    server_ordered_keys: List[str] = []
    server_by_key: Dict[str, dict] = {}
    for row in server_rows:
        clean = _normalize_words_csv_row(row)
        en = clean.get("english", "").strip()
        if not en:
            continue
        k = en.lower()
        if k not in server_by_key:
            server_ordered_keys.append(k)
        server_by_key[k] = clean

    server_key_set = set(server_by_key.keys())
    upload_key_set = set(upload_by_key.keys())
    new_keys = upload_key_set - server_key_set

    merged: List[dict] = []
    for k in server_ordered_keys:
        if k in upload_by_key:
            merged.append(upload_by_key[k])
        else:
            merged.append(server_by_key[k])

    new_keys_ordered: List[str] = []
    seen_new: set = set()
    for row in upload_rows:
        clean = _normalize_words_csv_row(row)
        en = clean.get("english", "").strip()
        if not en:
            continue
        k = en.lower()
        if k in new_keys and k not in seen_new:
            new_keys_ordered.append(k)
            seen_new.add(k)

    for k in new_keys_ordered:
        merged.append(upload_by_key[k])

    stats = {
        "server_distinct": len(server_ordered_keys),
        "upload_distinct": len(upload_by_key),
        "added": len(new_keys_ordered),
        "replaced": len(server_key_set & upload_key_set),
        "unchanged_server_only": len(server_key_set - upload_key_set),
        "final_count": len(merged),
    }
    return merged, stats


def _write_words_csv_rows_atomic_under_lock(rows: List[dict]) -> None:
    """在已持有 _words_csv_interprocess_lock 与 _words_csv_lock 时原子写入全表并失效缓存（勿调用会再次加锁的函数）。"""
    global _words_csv_cache, _words_csv_cache_mtime
    global _words_csv_by_key_cache, _words_csv_by_key_cache_mtime
    WORDS_CSV_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".csv", dir=str(WORDS_CSV_FILE.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({k: str(row.get(k, "") or "").strip() for k in _CSV_FIELDS})
        os.replace(tmp, WORDS_CSV_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _words_csv_cache = None
    _words_csv_by_key_cache = None
    _words_csv_by_key_cache_mtime = -1.0
    try:
        _words_csv_cache_mtime = WORDS_CSV_FILE.stat().st_mtime if WORDS_CSV_FILE.exists() else 0.0
    except OSError:
        _words_csv_cache_mtime = 0.0


def invalidate_words_csv_cache() -> None:
    global _words_csv_cache, _words_csv_by_key_cache, _words_csv_by_key_cache_mtime
    with _words_csv_lock:
        _words_csv_cache = None
        _words_csv_by_key_cache = None
        _words_csv_by_key_cache_mtime = -1.0
    invalidate_merge_wordbank_rows_cache()


def get_csv_english_set() -> set:
    """返回 CSV 中所有英文单词的小写集合。"""
    return {r.get("english", "").strip().lower() for r in load_words_csv() if r.get("english", "").strip()}


def append_words_to_csv(new_rows: List[dict]) -> int:
    """将新词条 append 到 CSV 文件，返回实际写入数量。"""
    if not new_rows:
        return 0
    with _words_csv_interprocess_lock():
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
            global _words_csv_by_key_cache, _words_csv_by_key_cache_mtime
            _words_csv_cache = None
            _words_csv_by_key_cache = None
            _words_csv_by_key_cache_mtime = -1.0
    invalidate_merge_wordbank_rows_cache()
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


def _example_slots_present(row: dict) -> List[str]:
    """返回有条目内容的例句槽位编号列表，如 ['1','2','3']（支持 v2 多例句）。"""
    slots: List[str] = []
    for k in range(1, 9):
        if (row.get(f"example{k}") or "").strip() or (row.get(f"example{k}_cn") or "").strip():
            slots.append(str(k))
    return slots


def _pick_example_slot_key(row: dict, english: str) -> str:
    """在已有例句槽位中按词条 + 当日日期确定性选一槽（与复习列表、练习判分一致）。"""
    slots = _example_slots_present(row)
    if not slots:
        return "1"
    if len(slots) == 1:
        return slots[0]
    key = (english or row.get("english") or "").strip().lower()
    h = hashlib.sha256(f"{key}:{china_today().isoformat()}".encode("utf-8")).hexdigest()
    idx = int(h[:8], 16) % len(slots)
    return slots[idx]


def pick_example_for_word(row: dict, english: str = "") -> dict:
    """从词条的例句槽位中选 1 个返回复习条目（多例句时按词条与日期确定性选择）。"""
    slots = _example_slots_present(row)
    if not slots:
        return csv_word_to_review_item(row, "1")
    k = _pick_example_slot_key(row, english)
    return csv_word_to_review_item(row, k)


def lookup_csv_word(english: str) -> Optional[dict]:
    """词库查询：优先 ``words_v2.json``，否则 ``words.csv``；返回与 CSV 兼容的扁平行。"""
    key = wordbank_v2.normalize_english_key(english)
    if not key:
        return None
    v2 = wordbank_v2.load_words_v2_by_key().get(key)
    if v2:
        return wordbank_v2.v2_entry_to_flat_csv_row(v2)
    return load_words_csv_by_key().get(key)


def examples_from_csv_row(row: Optional[dict]) -> List[dict]:
    """从 CSV 行或 v2 扁平行提取全部例句（英/中分行），供单词学习等展示。"""
    if not row:
        return []
    out: List[dict] = []
    for key in ("1", "2", "3", "4", "5", "6", "7", "8"):
        ex_en = (row.get(f"example{key}") or "").strip()
        ex_cn = (row.get(f"example{key}_cn") or "").strip()
        if ex_en or ex_cn:
            out.append({"en": ex_en, "cn": ex_cn})
    return out


def apply_review_display_from_wordbank(
    item: dict,
    csv_row: dict,
    w_english: str,
) -> None:
    """
    复习/加练卡片：例句槽位仍用 pick 与练习判分一致；多义项 v2 只展示当前槽对应的一条释义与一条例句；
    legacy 多例句时只展示当前槽对应的一行例句（释义保持合并行）。
    """
    item["review_polyseme"] = False
    key = wordbank_v2.normalize_english_key(w_english)
    v2 = wordbank_v2.load_words_v2_by_key().get(key)
    picked = pick_example_for_word(csv_row, w_english)
    slot = _pick_example_slot_key(csv_row, w_english)
    if picked.get("example"):
        item["example"] = picked["example"]
    item["example_form"] = picked.get("example_form", "")
    item["phonetic"] = csv_row.get("phonetic", "")
    item["level"] = csv_row.get("level", "")

    ex_en = (csv_row.get(f"example{slot}") or "").strip()
    ex_cn = (csv_row.get(f"example{slot}_cn") or "").strip()
    one_ex = [{"en": ex_en, "cn": ex_cn}] if (ex_en or ex_cn) else []

    senses = v2.get("senses") if v2 and isinstance(v2.get("senses"), list) else []
    if len(senses) > 1:
        si = int(slot) - 1
        if 0 <= si < len(senses):
            item["chinese"] = wordbank_v2.format_single_sense_chinese(senses[si])
        item["examples"] = one_ex
        item["review_polyseme"] = True
        return

    exs = examples_from_csv_row(csv_row)
    if len(exs) > 1:
        item["examples"] = one_ex
        item["review_polyseme"] = True
    else:
        item["examples"] = exs


def other_v2_sense_chinese_lines_for_review_slot(english: str) -> List[str]:
    """
    多义项 v2：与复习卡片同一槽位（词条 + 当日日期，与 _pick_example_slot_key 一致），
    返回**其余**义项的中文行（各一行）。非多义项或无法解析时返回空列表。
    """
    key = wordbank_v2.normalize_english_key(english)
    v2 = wordbank_v2.load_words_v2_by_key().get(key)
    if not v2 or not isinstance(v2.get("senses"), list) or len(v2["senses"]) <= 1:
        return []
    csv_row = lookup_csv_word(english)
    if not csv_row:
        return []
    slot = _pick_example_slot_key(csv_row, english)
    try:
        si = int(slot) - 1
    except ValueError:
        si = 0
    out: List[str] = []
    for i, s in enumerate(v2["senses"]):
        if i == si:
            continue
        line = wordbank_v2.format_single_sense_chinese(s)
        if line:
            out.append(line)
    return out


def other_v2_sense_extra_for_review_slot(english: str) -> List[dict]:
    """
    多义项 v2：与复习卡片同一槽位，返回**其余**义项的中文行及对应例句槽（example{i+1}）。
    非多义项或无法解析时返回空列表。
    """
    key = wordbank_v2.normalize_english_key(english)
    v2 = wordbank_v2.load_words_v2_by_key().get(key)
    if not v2 or not isinstance(v2.get("senses"), list) or len(v2["senses"]) <= 1:
        return []
    csv_row = lookup_csv_word(english)
    if not csv_row:
        return []
    slot = _pick_example_slot_key(csv_row, english)
    try:
        si = int(slot) - 1
    except ValueError:
        si = 0
    out: List[dict] = []
    for i, s in enumerate(v2["senses"]):
        if i == si:
            continue
        line = wordbank_v2.format_single_sense_chinese(s)
        if not line:
            continue
        k = str(i + 1)
        ex_en = (csv_row.get(f"example{k}") or "").strip()
        ex_cn = (csv_row.get(f"example{k}_cn") or "").strip()
        out.append(
            {
                "zh": line,
                "example_en": ex_en,
                "example_cn": ex_cn,
            }
        )
    return out


def merged_example_from_pair(en: str, cn: str) -> str:
    """与 csv_word_to_review_item 一致的合并串，供兼容旧字段 example。"""
    if en and cn:
        return f"{en}_{cn}"
    return en or cn


# ==================== 用户权限 ====================

def get_user_plan(username: str) -> str:
    """返回用户套餐: 'free' 或 'paid'（paid 对应 VIP）。默认 free。"""
    users = load_users()
    u = users.get(username)
    if isinstance(u, dict):
        return u.get("plan", "free")
    return "free"


def set_user_plan(username: str, plan: str) -> bool:
    """设置用户套餐。plan 必须为 'free' 或 'paid'（paid 即 VIP）。"""
    if plan not in ("free", "paid"):
        return False

    def _set_plan(users: Dict[str, Any]) -> bool:
        if username not in users:
            return False
        users[username]["plan"] = plan
        return True

    return bool(mutate_users(_set_plan))


def is_paid_user(username: str) -> bool:
    return get_user_plan(username) == "paid"


# ==================== DeepSeek API ====================

def _ssl_context_for_https() -> ssl.SSLContext:
    """
    urllib 默认在部分环境（尤其 macOS 官方 Python 安装）下缺少根证书，会导致
    CERTIFICATE_VERIFY_FAILED。优先使用 certifi 的 CA 包；未安装 certifi 时退回系统默认。
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _deepseek_error_is_retryable(err: BaseException) -> bool:
    """超时、限流、网关错误时可重试。"""
    msg = str(err).lower()
    if "timed out" in msg or "timeout" in msg:
        return True
    if isinstance(err, urllib.error.HTTPError):
        return err.code in (429, 502, 503, 504)
    if isinstance(err, urllib.error.URLError) and isinstance(err.reason, TimeoutError):
        return True
    return False


def _deepseek_http_error_body_for_log(e: urllib.error.HTTPError) -> str:
    """读取 DeepSeek/OpenAI 风格 HTTP 错误响应体，便于日志排查（仅调用一次 read）。"""
    try:
        raw_bytes = e.read()
    except Exception as ex:
        return f"<读取响应体失败: {ex}>"
    body = raw_bytes.decode("utf-8", errors="replace").strip()
    if not body:
        return f"<空响应体 reason={e.reason!r}>"
    if len(body) > 8000:
        body = body[:8000] + "…[截断]"
    try:
        j = json.loads(body)
        if isinstance(j, dict):
            err = j.get("error")
            if isinstance(err, dict):
                msg = err.get("message") or err.get("code") or err.get("type")
                typ = err.get("type", "")
                if msg:
                    return f"type={typ!r} message={msg!r} full_json={body[:4000]}"
            if "message" in j:
                return f"message={j.get('message')!r} full_json={body[:4000]}"
    except json.JSONDecodeError:
        pass
    return body


def _deepseek_chat(messages: List[dict], model: Optional[str] = None,
                   max_tokens: int = 4096, temperature: float = 0.7) -> Optional[str]:
    """调用 DeepSeek Chat API，返回助手回复文本；失败返回 None。"""
    api_key = get_deepseek_api_key()
    if not api_key:
        logger.warning("DEEPSEEK_API_KEY 未配置，无法调用 DeepSeek API")
        return None
    model = (model or DEEPSEEK_CHAT_MODEL).strip() or DEEPSEEK_CHAT_MODEL
    user_text = ""
    if messages and isinstance(messages[-1], dict) and messages[-1].get("role") == "user":
        user_text = str(messages[-1].get("content") or "")
    prompt_chars = len(user_text)
    preview_in = user_text[:2000] + ("…[截断]" if len(user_text) > 2000 else "")
    logger.info(
        "DeepSeek 请求: model=%s max_tokens=%s temperature=%s timeout_sec=%s prompt_chars=%s 输入预览=%s",
        model,
        max_tokens,
        temperature,
        DEEPSEEK_HTTP_TIMEOUT_SEC,
        prompt_chars,
        preview_in,
    )
    # v4 系列默认开启思考；本应用仅需最终回复，显式关闭以匹配旧 deepseek-chat 并降低延迟/费用。
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "thinking": {"type": "disabled"},
    }).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    last_err: Optional[BaseException] = None
    for attempt in range(1, DEEPSEEK_HTTP_RETRIES + 1):
        req = urllib.request.Request(DEEPSEEK_API_URL, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(
                req,
                timeout=DEEPSEEK_HTTP_TIMEOUT_SEC,
                context=_ssl_context_for_https(),
            ) as resp:
                raw = resp.read().decode("utf-8")
            try:
                result = json.loads(raw)
            except json.JSONDecodeError as je:
                last_err = je
                logger.error(
                    "DeepSeek 响应非合法 JSON: %s 原始前 2500 字: %s",
                    je,
                    raw[:2500],
                )
                break
            if isinstance(result, dict) and result.get("error"):
                err_obj = result["error"]
                last_err = ValueError(str(err_obj))
                logger.error(
                    "DeepSeek API 返回 error 字段: %s 完整响应前 4000 字: %s",
                    err_obj,
                    raw[:4000],
                )
                break
            try:
                content = result["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as ke:
                last_err = ke
                logger.error(
                    "DeepSeek 响应缺少 choices[0].message.content: %s result 前 4000 字: %s",
                    ke,
                    raw[:4000],
                )
                break
            preview_out = content[:3000] + ("…[截断]" if len(content) > 3000 else "")
            logger.info(
                "DeepSeek 响应: attempt=%s/%s response_chars=%s 输出预览=%s",
                attempt,
                DEEPSEEK_HTTP_RETRIES,
                len(content),
                preview_out,
            )
            return content
        except urllib.error.HTTPError as e:
            last_err = e
            detail = _deepseek_http_error_body_for_log(e)
            logger.warning(
                "DeepSeek HTTP 错误: attempt=%s/%s code=%s %s",
                attempt,
                DEEPSEEK_HTTP_RETRIES,
                e.code,
                detail,
            )
            if attempt < DEEPSEEK_HTTP_RETRIES and _deepseek_error_is_retryable(e):
                wait = DEEPSEEK_RETRY_BACKOFF_SEC * (2 ** (attempt - 1))
                logger.info("DeepSeek 可重试错误，%.1f 秒后第 %s 次重试", wait, attempt + 1)
                sleep(wait)
                continue
            logger.error(
                "DeepSeek API 调用失败(HTTP): code=%s reason=%r 详情=%s",
                e.code,
                e.reason,
                detail,
            )
            break
        except Exception as e:
            last_err = e
            logger.warning(
                "DeepSeek 请求失败: attempt=%s/%s err=%s",
                attempt,
                DEEPSEEK_HTTP_RETRIES,
                e,
            )
            if attempt < DEEPSEEK_HTTP_RETRIES and _deepseek_error_is_retryable(e):
                wait = DEEPSEEK_RETRY_BACKOFF_SEC * (2 ** (attempt - 1))
                logger.info("DeepSeek 可重试错误，%.1f 秒后第 %s 次重试", wait, attempt + 1)
                sleep(wait)
                continue
            break
    if last_err is not None and not isinstance(last_err, urllib.error.HTTPError):
        logger.error("DeepSeek API 调用失败: %s", last_err)
    return None


# 词汇导入：每批发给 DeepSeek 的词数（与 max_tokens 估算、JSON 输出上限一致）。切勿与外层 range 步长错配。
DEEPSEEK_VOCAB_BATCH_WORDS = 30


def deepseek_extract_lemmas(text: str) -> Optional[List[str]]:
    """用 DeepSeek 从文章中提取单词原形列表。"""
    prompt = (
        "请从以下英文文章中提取所有实义词（名词、动词、形容词、副词），"
        "还原为原形（lemma），去重，用英文逗号分隔，只返回单词列表，不要其他说明。\n\n"
        f"{text[:3000]}"
    )
    reply = _deepseek_chat([{"role": "user", "content": prompt}], max_tokens=2500)
    if not reply:
        return None
    words = [w.strip().lower() for w in re.split(r'[,，\s]+', reply) if w.strip() and re.match(r'^[a-zA-Z]+$', w.strip())]
    return words if words else None


def deepseek_generate_word_entries(words: List[str], level: str = "") -> Optional[List[dict]]:
    """
    用 DeepSeek 为单词列表生成词汇表条目（chinese, level, phonetic, examples）。
    返回 list of dict，每个 dict 含 CSV 字段。
    单次调用词数不应超过 DEEPSEEK_VOCAB_BATCH_WORDS（由 import_vocab 分批保证）。
    下游 ``accumulate_valid_deepseek_word_rows`` 会采纳所有格式合法且与词库去重后的行（含模型多返回的词）。
    """
    level_hint = f"，这批词汇难度级别为：{level}" if level else ""
    words = list(words)[:DEEPSEEK_VOCAB_BATCH_WORDS]
    if not words:
        return None
    words_str = "、".join(words)
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
- 若列表中某项为多个英文单词组成的短语（如 anything else），english 字段必须与该项原文完全一致（含空格），不要只写其中一个词
- 例句难度要与level相符，小学/初中例句要简单易懂
- example1_form 和 example2_form：只写在句子中实际出现的变形形式，如与原形完全相同则写空字符串
"""
    wc = max(1, len(words))
    # 多词时每条 JSON 较长，固定 3000 易截断导致解析失败；按词数放大，上限与 DeepSeek 输出上限对齐
    max_out = min(8192, max(2500, 700 + wc * 260))
    logger.info(
        "DeepSeek 词汇生成批次: word_count=%s level=%r max_tokens=%s 单词列表=%s",
        len(words),
        level or "",
        max_out,
        words_str[:800] + ("…[截断]" if len(words_str) > 800 else ""),
    )
    try:
        reply = _deepseek_chat([{"role": "user", "content": prompt}], max_tokens=max_out)
        if not reply:
            logger.warning(
                "DeepSeek 词汇生成: 无有效回复（原因见上方 DeepSeek HTTP/API 错误日志）",
            )
            return None
        # 提取JSON
        json_match = re.search(r'\[[\s\S]*\]', reply)
        if not json_match:
            logger.error(
                "DeepSeek 返回格式不含JSON数组，reply 前 800 字: %s",
                reply[:800],
            )
            return None
        try:
            data = json.loads(json_match.group(0))
        except json.JSONDecodeError as e:
            logger.error(
                "DeepSeek 返回JSON解析失败: %s 片段预览: %s",
                e,
                json_match.group(0)[:800],
            )
            return None
        if isinstance(data, list):
            logger.info("DeepSeek 词汇生成解析成功: entries=%s", len(data))
            return data
        logger.warning("DeepSeek 词汇生成: JSON 根类型非数组")
        return None
    finally:
        if DEEPSEEK_BATCH_PAUSE_SEC > 0:
            sleep(DEEPSEEK_BATCH_PAUSE_SEC)


def deepseek_generate_word_entries_v2(
    words: List[str],
    level: str = "",
    *,
    include_gaokao_candidate: bool = False,
) -> Optional[List[dict]]:
    """
    为新词库 ``words_v2.json`` 生成条目（senses 多义项 + 例句）。
    单次调用词数不应超过 DEEPSEEK_VOCAB_BATCH_WORDS。
    """
    level_hint = f"，这批词汇难度级别为：{level}" if level else ""
    max_words = _GAOKAO_IMPORT_BATCH_WORDS if include_gaokao_candidate else DEEPSEEK_VOCAB_BATCH_WORDS
    words = list(words)[:max_words]
    if not words:
        return None
    words_str = "、".join(words)
    level_rule = "，与上文难度一致" if level_hint else "；未给难度时按词自选"
    # JSON 示例勿放在 f-string 内：花括号会与 f-string 插值冲突
    gaokao_example = """
,"gaokao_question":{"recognition_distractors":["羽毛","洞穴","翅膀"],"recognition_explanation_zh":"辨析该词的核心义项。","context_sentence":"The cave survey recorded one bat flying above the researchers after sunset.","context_translation_zh":"洞穴调查记录到日落后一只蝙蝠从研究人员上方飞过。","context_distractors":["cat","owl","bee"],"context_explanation_zh":"洞穴、飞行和日落共同限定此处应为蝙蝠。"}""" if include_gaokao_candidate else ""
    _v2_json_shape_example = f"""[
  {{"english":"bat","senses":[{{"pos":"noun","definition_zh":"蝙蝠","example_en":"Bats fly at night.","example_cn":"蝙蝠在夜间飞行。","example_form":""}},{{"pos":"noun","definition_zh":"球棒","example_en":"He held a wooden bat.","example_cn":"他握着一根木球棒。","example_form":""}},{{"pos":"verb","definition_zh":"击打","example_en":"He bats the ball.","example_cn":"他击球。","example_form":"bats"}}],"level":"初中","phonetic":"/bæt/"{gaokao_example}}}
]"""
    gaokao_rules = """
- 每个对象还必须包含 gaokao_question，和词条在同一次输出中生成：
  - recognition_distractors：3 个中文错误释义；词性和难度接近，但不能是 senses 中任何义项或其同义表达。
  - recognition_explanation_zh：一句中文辨析。
  - context_sentence：围绕 senses[0] 重写的至少 12 词英文完整语境，必须包含正确词形且只出现一次。正确词形为 senses[0].example_form，若为空则为 english。
  - context_translation_zh：context_sentence 的准确中文翻译。
  - context_distractors：3 个能放入同一语法位置、词性和词形匹配但语义不成立的英文选项。
  - context_explanation_zh：指出唯一限定线索，并说明干扰项为何不成立。
- gaokao_question 不得依赖读者看不到的背景；逐项代入后只能有一个自然、合理答案。
""" if include_gaokao_candidate else ""
    prompt = f"""为下列每项各生成 1 个对象，输出**仅**合法 JSON 数组（从 [ 到 ]），无 Markdown、无说明。

列表：{words_str}{level_hint}

规则：
- senses：每条含 pos、definition_zh（2～12 字）；senses[0] 为最常见义；pos 用 noun|verb|adjective|adverb|phrase。
- **例句与义项一一对应**：每条 sense 必须同时含 example_en、example_cn、example_form（句中词形与 lemma 相同则填 ""）。**有几条 sense 就要有几条例句**，禁止 3 个义项只写 2 条例句。
- **字段语言不能颠倒**：example_en 必须是英文句子，不能包含中文；example_cn 必须是对应的中文翻译，不能为空。严禁把中文例句写入 example_en。
- 多义词覆盖主要高频义（如 key：钥匙/关键/键/键入）；勿合并义项。
- english 与列表一致；phonetic 一条；level 为 小学/初中/高中/GRE 之一{level_rule}。
{gaokao_rules}

结构示例（3 义则 senses 内 3 组例句）：
{_v2_json_shape_example}
"""
    wc = max(1, len(words))
    # 组合生成还包含两类题目，限制为更小批次并预留更长 JSON 输出。
    per_word_tokens = 1250 if include_gaokao_candidate else 550
    max_out = min(8192, max(3200, 900 + wc * per_word_tokens))
    # 调试：设置 ENGLISH_RECITER_DEEPSEEK_V2_DEBUG=1 时输出完整 prompt / 原始回复（日志量大）
    _v2_dbg = os.getenv("ENGLISH_RECITER_DEEPSEEK_V2_DEBUG", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    preview_words = words_str if len(words_str) <= 240 else words_str[:240] + "…"
    logger.info(
        "DeepSeek v2 请求: words_preview=%r count=%s level=%r max_tokens=%s prompt_chars=%s debug_full=%s",
        preview_words,
        len(words),
        level or "",
        max_out,
        len(prompt),
        _v2_dbg,
    )
    if _v2_dbg:
        logger.info("DeepSeek v2 prompt 全文:\n%s", prompt)
    try:
        reply = _deepseek_chat([{"role": "user", "content": prompt}], max_tokens=max_out)
        if not reply:
            logger.warning("DeepSeek v2 词汇生成: 无有效回复")
            return None
        _rp_prev = reply if len(reply) <= 600 else reply[:600] + "…[截断]"
        logger.info("DeepSeek v2 原始回复: chars=%s preview=%s", len(reply), _rp_prev)
        if _v2_dbg:
            logger.info("DeepSeek v2 原始回复全文:\n%s", reply)
        json_match = re.search(r'\[[\s\S]*\]', reply)
        if not json_match:
            logger.error(
                "DeepSeek v2 返回格式不含JSON数组，reply 前 800 字: %s",
                reply[:800],
            )
            return None
        _json_slice = json_match.group(0)
        logger.info("DeepSeek v2 提取JSON片段: chars=%s", len(_json_slice))
        try:
            data = json.loads(_json_slice)
        except json.JSONDecodeError as e:
            logger.error(
                "DeepSeek v2 返回JSON解析失败: %s 片段预览: %s",
                e,
                _json_slice[:800],
            )
            return None
        if isinstance(data, list):
            _ens = [
                str(e.get("english", "")).strip()
                for e in data
                if isinstance(e, dict) and str(e.get("english", "")).strip()
            ]
            logger.info(
                "DeepSeek v2 解析成功: entries=%s english=%s",
                len(data),
                _ens[:40],
            )
            return data
        logger.warning("DeepSeek v2 词汇生成: JSON 根类型非数组 type=%s", type(data).__name__)
        return None
    finally:
        if DEEPSEEK_BATCH_PAUSE_SEC > 0:
            sleep(DEEPSEEK_BATCH_PAUSE_SEC)


def accumulate_valid_deepseek_v2_entries(
    entries: Optional[List],
    *,
    level_hint: str,
    v2_so_far: set,
    batch_lower: set,
) -> Tuple[List[dict], set]:
    """
    从 DeepSeek v2 返回的 JSON 数组中采纳合法词条，与 v2_so_far 去重。
    返回 (新条目列表, 本批请求词中已成功写入的 english 小写集合)。
    """
    new_entries: List[dict] = []
    success_for_batch: set = set()
    if not entries:
        return new_entries, success_for_batch
    extra_words: List[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        fin = wordbank_v2.finalize_v2_entry_from_deepseek(entry)
        if not fin:
            continue
        en = fin["english"]
        if en in v2_so_far:
            continue
        if level_hint:
            fin["level"] = level_hint
        new_entries.append(fin)
        v2_so_far.add(en)
        if en in batch_lower:
            success_for_batch.add(en)
        else:
            extra_words.append(en)
    if extra_words:
        logger.info(
            "DeepSeek v2 本批除请求列表外另写入词库 %s 条: %s",
            len(extra_words),
            extra_words[:40],
        )
    return new_entries, success_for_batch


def finalize_combined_gaokao_candidates(
    raw_entries: Optional[List],
    finalized_entries: List[dict],
) -> Tuple[Dict[str, dict], Dict[str, str]]:
    """Validate question candidates carried by the combined word-entry response."""
    raw_by_key = {
        wordbank_v2.normalize_english_key(raw.get("english", "")): raw
        for raw in raw_entries or []
        if isinstance(raw, dict)
        and wordbank_v2.normalize_english_key(raw.get("english", ""))
    }
    records: Dict[str, dict] = {}
    errors: Dict[str, str] = {}
    for entry in finalized_entries:
        flat = wordbank_v2.v2_entry_to_flat_csv_row(entry)
        source = gaokao_questions.source_from_wordbank_row(flat)
        key = gaokao_questions.normalize_word(flat.get("english"))
        if not key:
            continue
        if not source:
            errors[key] = "combined generation produced no usable source example"
            continue
        if (
            gaokao_questions.has_complete_questions(
                key,
                source_hash=str(source.get("source_hash") or ""),
            )
            or gaokao_questions.has_pending_candidate(
                key,
                source_hash=str(source.get("source_hash") or ""),
            )
        ):
            continue
        raw = raw_by_key.get(key)
        question_raw = raw.get("gaokao_question") if isinstance(raw, dict) else None
        if not isinstance(question_raw, dict):
            errors[key] = "combined generation is missing gaokao_question"
            continue
        question_raw = {**question_raw, "english": key}
        record, error = gaokao_questions.finalize_generated_questions(
            source,
            question_raw,
        )
        if record:
            records[key] = record
        else:
            errors[key] = f"combined question validation failed: {error}"
    return records, errors


# 登录/注册简单限流（按 IP，内存存储）
_rate_buckets: Dict[str, List[float]] = defaultdict(list)
_rate_buckets_lock = threading.Lock()
_RATE_WINDOW_SEC = 60
_RATE_MAX_LOGIN = 20
_RATE_MAX_REGISTER = 10
_RATE_MAX_FORGOT_PASSWORD = 5
_RATE_MAX_RESET_PASSWORD = 20
_RATE_MAX_ADMIN_LOGIN = 10
_RATE_MAX_ADMIN_DELETE_USER = 8
_RATE_MAX_CHAT_POST = 30
_RATE_MAX_CHAT_GET = 240
_RATE_MAX_CHAT_STREAM_TOKEN = 60
_RATE_MAX_PERF_REPORT = 120
_RATE_MAX_TTS_AUDIO = 90
_RATE_MAX_WORDBANK_SEARCH = 180
_RATE_MAX_ARTICLE_IMPORT = 20
_RATE_MAX_OCR = 20
_RATE_MAX_VOCAB_IMPORT = 12
_RATE_MAX_IMPORT_JSON = 60
_RATE_MAX_CHALLENGE_CREATE = 30
_RATE_MAX_CHALLENGE_RESPOND = 60

_CHAT_MENTION_RE = re.compile(r"@([a-zA-Z0-9_]{3,32})")

INVITES_FILE = DATA_DIR / "invites.json"
INVITES_LOCK_FILE = SHARED_DATA_DIR / ".invites.lock"
INVITE_CODE_KEY_FILE = SHARED_DATA_DIR / ".invite_code_fernet.key"
_invites_lock = threading.Lock()
_invite_crypto_lock = threading.Lock()
_invite_fernet_cache = None

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
    with _rate_buckets_lock:
        window: List[float] = _rate_buckets[bucket_key]
        window[:] = [t for t in window if now - t < _RATE_WINDOW_SEC]
        if len(window) >= max_events:
            return False
        window.append(now)
        return True


def is_valid_username(username: str) -> bool:
    return bool(username and USERNAME_PATTERN.fullmatch(username))


def normalize_email(value: Any) -> Optional[str]:
    """返回可存储的规范邮箱；空值表示未填写，格式错误抛出 ValueError。"""
    raw = str(value or "").strip()
    if not raw:
        return None
    if len(raw) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", raw):
        raise ValueError("邮箱格式不正确")
    return raw.lower()


def _email_owner(users: Dict[str, Any], email: str, *, exclude: str = "") -> Optional[str]:
    target = email.casefold()
    for uname, row in users.items():
        if uname == exclude or not isinstance(row, dict):
            continue
        existing = str(row.get("email") or "").strip()
        if existing and existing.casefold() == target:
            return str(uname)
    return None


def is_reserved_parent_username(username: str) -> bool:
    """注册名不可为 *_parent，与家长登录名冲突。"""
    return bool(username) and username.lower().endswith(PARENT_LOGIN_SUFFIX)


def parent_login_username_for_child(child: str) -> Optional[str]:
    """学生用户名 → 家长登录名（child_parent）；过长则无法创建。"""
    if not is_valid_username(child):
        return None
    p = f"{child}{PARENT_LOGIN_SUFFIX}"
    return p if USERNAME_PATTERN.fullmatch(p) else None


def is_parent_user_record(user_dict: dict) -> bool:
    return isinstance(user_dict, dict) and user_dict.get("role") == USER_ROLE_PARENT


def student_has_enabled_parent_account(child: str) -> bool:
    """学生账号是否已开通家长登录（存在对应 *_parent 账号）。"""
    users = load_users()
    pname = parent_login_username_for_child(child)
    if not pname or pname not in users:
        return False
    return is_parent_user_record(users.get(pname))


def user_avatar_disk_path(username: str) -> Optional[Path]:
    if not is_valid_username(username):
        return None
    d = DATA_DIR / username
    if not d.is_dir():
        return None
    try:
        names = set(os.listdir(d))
    except OSError:
        return None
    for name in ("avatar.webp", "avatar.jpg", "avatar.jpeg", "avatar.png"):
        if name in names:
            return d / name
    return None


def _leaderboard_avatar_url(username: str, cache: Dict[str, Optional[str]]) -> Optional[str]:
    u = str(username or "")
    if u in cache:
        return cache[u]
    url = f"/api/user/avatar/{u}" if user_avatar_disk_path(u) else None
    cache[u] = url
    return url


def _avatar_pil_to_rgb(im: "PILImage.Image") -> "PILImage.Image":
    if im.mode == "RGBA":
        bg = PILImage.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[3])
        return bg
    if im.mode == "P" and "transparency" in im.info:
        return _avatar_pil_to_rgb(im.convert("RGBA"))
    return im.convert("RGB")


def _save_user_avatar_webp(src_stream, dst: Path) -> None:
    """将上传图像规范为 RGB、限制长边、保存为单个 WebP 文件。"""
    assert PILImage is not None
    try:
        src_stream.seek(0)
    except (OSError, AttributeError, TypeError):
        pass
    im = PILImage.open(src_stream)
    im = _avatar_pil_to_rgb(im)
    w, h = im.size
    m = max(w, h)
    if m > AVATAR_MAX_SIDE:
        s = AVATAR_MAX_SIDE / m
        im = im.resize(
            (max(1, int(w * s)), max(1, int(h * s))),
            PILImage.LANCZOS,
        )
    dst.parent.mkdir(parents=True, exist_ok=True)
    im.save(dst, "WEBP", quality=AVATAR_WEBP_QUALITY, method=6)


def enrich_monthly_pool_with_avatars(pool: dict) -> dict:
    """为奖池赛跑参与者补充 avatar_url（供前端展示）。"""
    for r in pool.get("runners") or []:
        u = str(r.get("username") or "")
        if u:
            r["avatar_url"] = f"/api/user/avatar/{u}" if user_avatar_disk_path(u) else None
        else:
            r["avatar_url"] = None
    return pool


def list_challenge_opponent_usernames(viewer: str) -> List[str]:
    """可发起 1v1 的用户名（与排行榜同源：已启用且参与排行展示的用户，不含自己）。"""
    users = load_users()
    enabled = [
        u for u in users
        if _user_row_is_enabled(users.get(u))
        and not is_parent_user_record(users[u])
    ]
    rows = gamification_mod.build_leaderboard(DATA_DIR, enabled, viewer=viewer)
    return [str(r["username"]) for r in rows if r.get("username") and r["username"] != viewer]


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


_INVITE_CODE_ENC_PREFIX = "er-invite:v1:"


def _fernet_for_invite_storage():
    """Return a stable Fernet instance for recoverable, unused invite codes."""
    global _invite_fernet_cache
    if _invite_fernet_cache is not None:
        return _invite_fernet_cache
    with _invite_crypto_lock:
        if _invite_fernet_cache is not None:
            return _invite_fernet_cache
        try:
            from cryptography.fernet import Fernet
        except ImportError:
            logger.error("未安装 cryptography，无法安全保存可复用邀请码")
            return None

        try:
            key = INVITE_CODE_KEY_FILE.read_bytes().strip()
        except FileNotFoundError:
            master = os.getenv("INVITE_CODE_ENCRYPTION_SECRET", "").strip()
            if master:
                key = base64.urlsafe_b64encode(
                    hashlib.sha256(
                        (master + "|english_reciter.invites.v1").encode("utf-8")
                    ).digest()
                )
            else:
                try:
                    INVITE_CODE_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
                    key = Fernet.generate_key()
                    try:
                        fd = os.open(
                            str(INVITE_CODE_KEY_FILE),
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                            0o600,
                        )
                    except FileExistsError:
                        key = INVITE_CODE_KEY_FILE.read_bytes().strip()
                    else:
                        with os.fdopen(fd, "wb") as f:
                            f.write(key)
                            f.flush()
                            os.fsync(f.fileno())
                except OSError as exc:
                    logger.error("邀请码加密密钥文件不可用: %s", exc)
                    return None
        except OSError as exc:
            logger.error("邀请码加密密钥文件不可用: %s", exc)
            return None
        try:
            os.chmod(INVITE_CODE_KEY_FILE, 0o600)
        except OSError:
            pass
        try:
            _invite_fernet_cache = Fernet(key)
        except Exception as exc:
            logger.error("邀请码加密密钥无效: %s", exc)
            return None
        return _invite_fernet_cache


def _encrypt_invite_code_for_storage(plain: str) -> str:
    fernet = _fernet_for_invite_storage()
    if fernet is None:
        raise RuntimeError("邀请码安全存储不可用")
    normalized = plain.strip().upper()
    token = fernet.encrypt(normalized.encode("utf-8")).decode("ascii")
    return _INVITE_CODE_ENC_PREFIX + token


def _legacy_invite_fernets_for_recovery() -> List[Any]:
    """Support ciphertext produced by the short-lived SECRET_KEY-based implementation."""
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return []
    candidates = []
    seen_keys = set()
    for env_name in ("INVITE_CODE_ENCRYPTION_SECRET", "SECRET_KEY"):
        master = os.getenv(env_name, "").strip()
        if not master:
            continue
        key = base64.urlsafe_b64encode(
            hashlib.sha256((master + "|english_reciter.invites.v1").encode("utf-8")).digest()
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        candidates.append(Fernet(key))
    return candidates


def _recover_invite_code(inv: dict) -> str:
    """Recover an unused code owned by its creator; never return used codes."""
    if not isinstance(inv, dict) or inv.get("used_at"):
        return ""
    expected_hash = str(inv.get("code_hash") or "")
    ciphertext = str(inv.get("code_ciphertext") or "").strip()
    code = ""
    if ciphertext.startswith(_INVITE_CODE_ENC_PREFIX):
        token = ciphertext[len(_INVITE_CODE_ENC_PREFIX) :].encode("ascii")
        candidates = []
        primary = _fernet_for_invite_storage()
        if primary is not None:
            candidates.append(primary)
        candidates.extend(_legacy_invite_fernets_for_recovery())
        for fernet in candidates:
            try:
                code = fernet.decrypt(token).decode("utf-8").strip().upper()
                break
            except Exception:
                continue
        if not code:
            logger.error("邀请码密文无法解密: id=%s", inv.get("id"))
            return ""
    else:
        # Compatibility with any short-lived development builds that stored this field directly.
        code = str(inv.get("invite_code") or "").strip().upper()
    if not code or not expected_hash:
        return ""
    return code if secrets.compare_digest(_hash_invite_code(code), expected_hash) else ""


def _invite_code_hash_candidates(plain: str) -> List[str]:
    code = plain.strip()
    candidates = [_hash_invite_code(code)]
    upper = code.upper()
    if upper != code:
        candidates.append(_hash_invite_code(upper))
    return candidates


def _new_invite_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(10))


def _fresh_invite_code(existing_invites: List[dict]) -> str:
    used_hashes = {
        str(inv.get("code_hash") or "")
        for inv in existing_invites
        if isinstance(inv, dict)
    }
    for _ in range(128):
        plain = _new_invite_code()
        if _hash_invite_code(plain) not in used_hashes:
            return plain
    raise RuntimeError("无法生成唯一邀请码")


def invite_quota_used(user_dict: dict) -> int:
    try:
        return max(0, int(user_dict.get("invite_quota_used", 0) or 0))
    except (TypeError, ValueError):
        return 0


def invite_quota_limit(user_dict: dict) -> int:
    raw_val = user_dict.get("invite_quota_limit", USER_INVITE_QUOTA_DEFAULT)
    if raw_val is None or raw_val == "":
        return USER_INVITE_QUOTA_DEFAULT
    try:
        raw = int(raw_val)
    except (TypeError, ValueError):
        raw = USER_INVITE_QUOTA_DEFAULT
    return max(0, raw)


def invite_quota_payload(user_dict: dict) -> dict:
    limit = invite_quota_limit(user_dict)
    used = min(invite_quota_used(user_dict), limit)
    return {
        "invite_quota_limit": limit,
        "invite_quota_used": used,
        "invite_quota_remaining": max(0, limit - used),
    }


def invite_public_row(inv: dict) -> dict:
    used = bool(inv.get("used_at"))
    row = {
        "id": inv.get("id"),
        "created_at": inv.get("created_at"),
        "created_by": inv.get("created_by"),
        "created_by_kind": inv.get("created_by_kind") or "admin",
        "used_at": inv.get("used_at"),
        "used_by": inv.get("used_by"),
        "status": "used" if used else "unused",
    }
    if not used and inv.get("created_by_kind") == "user":
        row["selectable"] = bool(_recover_invite_code(inv))
    return row


def _list_invites_created_by(username: str) -> List[dict]:
    data = load_invites()
    rows = []
    for inv in data.get("invites", []):
        if not isinstance(inv, dict):
            continue
        if inv.get("created_by_kind") == "user" and inv.get("created_by") == username:
            rows.append(invite_public_row(inv))
    rows.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return rows


def _list_unused_invites_created_by(username: str) -> List[dict]:
    data = load_invites()
    rows = []
    for inv in data.get("invites", []):
        if not isinstance(inv, dict):
            continue
        if inv.get("created_by_kind") != "user" or inv.get("created_by") != username:
            continue
        if inv.get("used_at"):
            continue
        code = _recover_invite_code(inv)
        row = {
            "id": inv.get("id"),
            "created_at": inv.get("created_at"),
            "status": "unused",
            "selectable": bool(code),
        }
        if not code:
            row["unavailable_reason"] = (
                "decrypt_failed" if inv.get("code_ciphertext") else "legacy_hash_only"
            )
        if code:
            row["invite_code"] = code
        rows.append(row)
    rows.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return rows


def _public_origin() -> str:
    configured = (os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if configured:
        return configured
    return request.url_root.rstrip("/")


@contextmanager
def _locked_invite_storage() -> Generator[None, None, None]:
    """Serialize invite reads and writes across threads and Gunicorn workers."""
    INVITES_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(INVITES_LOCK_FILE, "a+b", buffering=0)
    try:
        if sys.platform == "win32":
            import msvcrt

            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        with _invites_lock:
            yield
    finally:
        if sys.platform == "win32":
            import msvcrt

            try:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        else:
            import fcntl

            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        try:
            lock_file.close()
        except OSError:
            pass


def _env_flag(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} 必须是整数") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} 必须在 {minimum} 到 {maximum} 之间")
    return value


def _password_reset_policy() -> Tuple[int, int]:
    return (
        _env_int(
            "PASSWORD_RESET_TTL_MINUTES",
            PASSWORD_RESET_TTL_MINUTES_DEFAULT,
            1,
            1440,
        ),
        _env_int(
            "PASSWORD_RESET_COOLDOWN_SECONDS",
            PASSWORD_RESET_COOLDOWN_SECONDS_DEFAULT,
            0,
            86400,
        ),
    )


def _smtp_config() -> Dict[str, Any]:
    host = (os.getenv("SMTP_HOST") or "").strip()
    username = (os.getenv("SMTP_USERNAME") or "").strip()
    password = os.getenv("SMTP_PASSWORD") or ""
    raw_from_email = (os.getenv("SMTP_FROM_EMAIL") or username).strip()
    parsed_from_name, from_email = parseaddr(raw_from_email)
    use_ssl = _env_flag("SMTP_USE_SSL", True)
    starttls = _env_flag("SMTP_STARTTLS", not use_ssl)
    try:
        port = int((os.getenv("SMTP_PORT") or ("465" if use_ssl else "587")).strip())
        timeout = float((os.getenv("SMTP_TIMEOUT") or "15").strip())
    except ValueError as exc:
        raise RuntimeError("SMTP_PORT 或 SMTP_TIMEOUT 配置不正确") from exc
    if not host or not raw_from_email or not from_email:
        raise RuntimeError("邮件服务未配置")
    try:
        from_email = normalize_email(from_email) or ""
    except ValueError as exc:
        raise RuntimeError("SMTP_FROM_EMAIL 配置不正确") from exc
    return {
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "from_email": from_email,
        "from_name": (os.getenv("SMTP_FROM_NAME") or parsed_from_name or "智能英语背诵").strip(),
        "use_ssl": use_ssl,
        "starttls": starttls,
        "timeout": max(3.0, min(timeout, 60.0)),
    }


def _send_password_reset_email(to_email: str, reset_url: str, valid_minutes: int) -> None:
    cfg = _smtp_config()
    msg = EmailMessage()
    msg["Subject"] = "重置智能英语背诵账号密码"
    msg["From"] = formataddr((cfg["from_name"], cfg["from_email"]))
    msg["To"] = to_email
    msg.set_content(
        "你正在重置智能英语背诵账号的密码。\n\n"
        f"请在 {valid_minutes} 分钟内打开以下链接设置新密码：\n{reset_url}\n\n"
        "此链接只能使用一次。如果不是你本人操作，请忽略本邮件。"
    )
    context = ssl.create_default_context()
    if cfg["use_ssl"]:
        smtp_cls = smtplib.SMTP_SSL
        smtp = smtp_cls(cfg["host"], cfg["port"], timeout=cfg["timeout"], context=context)
    else:
        smtp = smtplib.SMTP(cfg["host"], cfg["port"], timeout=cfg["timeout"])
    with smtp:
        if cfg["starttls"] and not cfg["use_ssl"]:
            smtp.starttls(context=context)
        if cfg["username"]:
            smtp.login(cfg["username"], cfg["password"])
        smtp.send_message(msg)


def load_invites() -> dict:
    """加载邀请码列表。"""
    try:
        with open(INVITES_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {"invites": []}
    except Exception as e:
        logger.error(f"加载邀请码失败: {e}")
        raise


def save_invites(data: dict) -> None:
    """Atomically save invites so a failed write cannot truncate the live file."""
    INVITES_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{INVITES_FILE.name}.",
        suffix=".tmp",
        dir=str(INVITES_FILE.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, INVITES_FILE)
    except Exception as e:
        logger.error(f"保存邀请码失败: {e}")
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


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

    code_hashes = set(_invite_code_hash_candidates(invite_code))
    with _locked_invite_storage():
        data = load_invites()
        invites = data.get('invites', [])
        matched = None
        for inv in invites:
            if inv.get('code_hash') in code_hashes and inv.get('used_at') is None:
                matched = inv
                break
        if not matched:
            return False, '邀请码无效或已使用'

        def _add_user(users: Dict[str, Any]) -> Tuple[bool, str]:
            if matched.get('created_by_kind') == 'user':
                creator = users.get(str(matched.get('created_by') or ''))
                if not _user_row_is_enabled(creator):
                    return False, '邀请码无效或已使用'
            if username in users:
                return False, '用户名已存在'
            if is_reserved_parent_username(username):
                return False, '该用户名保留给家长账户使用，请更换'
            if (DATA_DIR / username).exists():
                return False, '用户名数据尚未清理，请稍后重试'
            if email and _email_owner(users, email):
                return False, '该邮箱已被其他账号使用'
            users[username] = {
                'password_hash': hash_password(password),
                'email': email,
                'created_at': china_now_iso(timespec="seconds"),
                'enabled': True,
            }
            matched['used_at'] = china_now_iso(timespec="seconds")
            matched['used_by'] = username
            matched.pop('code_ciphertext', None)
            matched.pop('invite_code', None)
            (DATA_DIR / username).mkdir(exist_ok=True)
            save_invites(data)
            return True, ''

        original_invite = dict(matched)
        try:
            ok, err = mutate_users(_add_user)
        except Exception:
            matched.clear()
            matched.update(original_invite)
            try:
                save_invites(data)
            except Exception:
                logger.exception("注册失败后恢复邀请码状态失败: invite_id=%s", matched.get('id'))
            raise
        if not ok:
            return False, err
        logger.info("新用户注册: %s (invite_id=%s)", username, matched.get('id'))
        return True, ''


def _user_row_is_enabled(row: Any) -> bool:
    return isinstance(row, dict) and row.get('enabled', True) is not False


def is_user_enabled(username: str) -> bool:
    return _user_row_is_enabled(get_user(username))


def _revoke_user_tokens(username: str) -> None:
    revoke_principal(SESSION_KIND_USER, username)


def _invalidate_user_reciter_cache(username: str) -> None:
    with _reciter_registry_lock:
        _user_reciter_cache.pop(username, None)


def _purge_student_account_completely(username: str) -> None:
    """
    删除学生账号：用户表、家长账号条目、用户目录、挑战相关引用、会话。
    仅用于学生主账号；调用前须已校验非家长记录且用户名有效。
    """
    pname = parent_login_username_for_child(username)
    trashed_user_dir: Optional[Path] = None
    user_dir = DATA_DIR / username

    with _locked_invite_storage():
        current_user = get_user(username)
        if not isinstance(current_user, dict) or is_parent_user_record(current_user):
            return
        invite_data = load_invites()
        invite_rows = invite_data.setdefault("invites", [])
        original_invite_rows = list(invite_rows)
        if user_dir.exists():
            trash_root = DATA_DIR / "_shared" / "deleted_users"
            trash_root.mkdir(parents=True, exist_ok=True)
            trashed_user_dir = trash_root / f"{username}-{uuid.uuid4().hex}"
            user_dir.replace(trashed_user_dir)

        def _delete_users(users: Dict[str, Any]) -> bool:
            u = users.get(username)
            if not isinstance(u, dict) or is_parent_user_record(u):
                return False
            if pname and pname in users and is_parent_user_record(users.get(pname)):
                del users[pname]
            del users[username]
            remaining = [
                inv
                for inv in invite_rows
                if not (
                    isinstance(inv, dict)
                    and inv.get("created_by_kind") == "user"
                    and inv.get("created_by") == username
                )
            ]
            if len(remaining) != len(invite_rows):
                invite_data["invites"] = remaining
                save_invites(invite_data)
            return True

        try:
            deleted = mutate_users(_delete_users)
        except Exception:
            invite_data["invites"] = original_invite_rows
            try:
                save_invites(invite_data)
            except Exception:
                logger.exception("删除用户失败后恢复邀请码列表失败: user=%s", username)
            if trashed_user_dir is not None and trashed_user_dir.exists() and not user_dir.exists():
                try:
                    trashed_user_dir.replace(user_dir)
                    trashed_user_dir = None
                except OSError:
                    logger.exception("删除用户失败后恢复用户目录失败: user=%s", username)
            raise
        if not deleted:
            if trashed_user_dir is not None and trashed_user_dir.exists() and not user_dir.exists():
                trashed_user_dir.replace(user_dir)
                trashed_user_dir = None
            return
        try:
            if pname:
                _revoke_user_tokens(pname)
            _revoke_user_tokens(username)
        except Exception:
            logger.exception("删除用户后撤销会话失败: user=%s", username)
        with _reciter_registry_lock:
            _user_reciter_cache.pop(username, None)
            _user_reciter_locks.pop(username, None)
        try:
            challenges_mod.purge_user_challenges_refs(DATA_DIR, username)
        except Exception:
            logger.exception("删除用户后清理挑战记录失败: user=%s", username)

    if trashed_user_dir is not None:
        if trashed_user_dir.is_dir():
            shutil.rmtree(trashed_user_dir, ignore_errors=True)
        else:
            try:
                trashed_user_dir.unlink()
            except OSError:
                pass


def verify_user(username: str, password: str) -> bool:
    """验证用户；若仍为旧版哈希则自动升级为 Werkzeug 哈希。"""
    row = get_user(username)
    if not isinstance(row, dict):
        return False
    stored = str(row.get("password_hash") or "")
    if not verify_password(password, stored):
        return False
    if _is_legacy_sha256_hex(stored):
        upgraded = update_password_hash(username, stored, hash_password(password))
        if upgraded:
            logger.info("用户 %s 的密码哈希已升级为安全格式", username)
        else:
            current = get_user(username)
            return isinstance(current, dict) and verify_password(
                password,
                str(current.get("password_hash") or ""),
            )
    return True

def create_token(username: str) -> str:
    """创建访问令牌（SQLite 持久化，多 worker 共享）。"""
    return _db_create_auth_session(SESSION_KIND_USER, username, USER_SESSION_TTL)


def verify_token(token: str) -> Optional[str]:
    """验证令牌，返回登录用户名（家长或学生）。"""
    if not token:
        return None
    return verify_session(token, SESSION_KIND_USER)


def create_admin_token() -> str:
    """签发管理员会话 token（与学生 token 隔离）。"""
    admin_name = os.getenv("ADMIN_USERNAME", "").strip() or "admin"
    return _db_create_auth_session(SESSION_KIND_ADMIN, admin_name, ADMIN_SESSION_TTL)


def create_chat_stream_token(login_username: str) -> str:
    """签发只用于聊天室 SSE 的短期 token，避免把登录 token 放进 URL。"""
    return _db_create_auth_session(SESSION_KIND_CHAT_STREAM, login_username, CHAT_STREAM_SESSION_TTL)


def verify_admin_token(token: str) -> bool:
    if not token:
        return False
    return verify_session(token, SESSION_KIND_ADMIN) is not None


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
    """要求token认证的装饰器；家长登录时使用关联学生的数据目录。"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # 从Authorization头获取token
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({'error': '需要认证'}), 401

        token = auth_header[7:].strip()
        login_username = verify_token(token)

        if not login_username:
            return jsonify({'error': '无效或过期的token'}), 401

        urow = get_user(login_username)
        if not isinstance(urow, dict) or urow.get('enabled', True) is False:
            _revoke_user_tokens(login_username)
            return jsonify({'error': '账号已停用'}), 403

        g.login_username = login_username
        g.user_row = urow
        if isinstance(urow, dict) and is_parent_user_record(urow):
            child = (urow.get("child_username") or "").strip()
            if not child or not is_valid_username(child):
                return jsonify({'error': '家长账户配置错误'}), 403
            ch = get_user(child)
            if not isinstance(ch, dict) or is_parent_user_record(ch):
                return jsonify({'error': '关联学生不存在'}), 403
            if ch.get('enabled', True) is False:
                return jsonify({'error': '学生账号已停用'}), 403
            g.is_parent = True
            g.effective_username = child
            g.effective_user_row = ch
            return f(child, *args, **kwargs)

        g.is_parent = False
        g.effective_username = login_username
        g.effective_user_row = urow
        return f(login_username, *args, **kwargs)
    return decorated_function


def parent_forbidden(f):
    """家长账户仅可查看进度、导入、排行榜等，禁止练习/挑战等操作。"""
    @wraps(f)
    def decorated_function(username, *args, **kwargs):
        if getattr(g, "is_parent", False):
            return jsonify({'error': '家长账户仅可查看学习数据与导入，无法执行此操作'}), 403
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


def _user_learning_settings_path(username: str) -> Path:
    return DATA_DIR / username / "learning_settings.json"


def _read_user_learning_settings(username: str) -> dict:
    path = _user_learning_settings_path(username)
    if not path.is_file():
        return {}
    try:
        with path.open('r', encoding='utf-8') as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("读取用户学习设置失败: user=%s error=%s", username, exc)
        return {}
    return dict(raw) if isinstance(raw, dict) else {}


def _write_user_learning_settings(username: str, settings: dict) -> None:
    path = _user_learning_settings_path(username)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(suffix='.json', dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _build_user_config(username: str) -> Config:
    user_dir = DATA_DIR / username
    config_file = user_dir / "config.json"
    config = Config(str(config_file)) if config_file.exists() else Config()
    settings = _read_user_learning_settings(username)
    try:
        daily_review_limit = int(settings.get('daily_review_limit'))
    except (TypeError, ValueError, OverflowError):
        daily_review_limit = config.DAILY_REVIEW_LIMIT
    config.DAILY_REVIEW_LIMIT = max(
        1,
        min(MAX_DAILY_REVIEW_LIMIT, daily_review_limit),
    )
    config.DATA_FILE = str(user_dir / "learning_data.json")
    config.EXAMPLE_DB = str(user_dir / "word_examples.json")
    return config


def _build_user_reciter(username: str) -> WordReciter:
    config = _build_user_config(username)
    return WordReciter(config)


@contextmanager
def _user_learning_interprocess_lock(username: str) -> Generator[None, None, None]:
    """Serialize one user's learning JSON across Gunicorn workers."""
    lock_path = DATA_DIR / username / '.learning_data.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(lock_path, 'a+b', buffering=0)
    try:
        if sys.platform == 'win32':
            import msvcrt

            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b'\0')
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if sys.platform == 'win32':
            import msvcrt

            try:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        else:
            import fcntl

            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        try:
            lock_file.close()
        except OSError:
            pass


def _learning_data_mtime_ns(username: str) -> int:
    path = DATA_DIR / username / 'learning_data.json'
    try:
        stat = path.stat()
        return int(getattr(stat, 'st_mtime_ns', stat.st_mtime * 1_000_000_000))
    except OSError:
        return 0


def _user_reciter_source_signature(username: str) -> Tuple[int, int]:
    try:
        settings_mtime = _user_learning_settings_path(username).stat().st_mtime_ns
    except OSError:
        settings_mtime = 0
    return _learning_data_mtime_ns(username), settings_mtime


@contextmanager
def user_reciter_session(username: str) -> Generator[WordReciter, None, None]:
    """Serialize learning data and refresh stale per-worker caches."""
    lock = _user_mutex(username)
    with lock:
        with _user_learning_interprocess_lock(username):
            source_signature = _user_reciter_source_signature(username)
            reciter = _user_reciter_cache.get(username)
            if (
                reciter is None
                or getattr(reciter, '_source_signature', None) != source_signature
            ):
                reciter = _build_user_reciter(username)
                reciter._source_signature = source_signature
                _user_reciter_cache[username] = reciter
            reciter.refresh_for_new_day()
            try:
                yield reciter
            finally:
                reciter._source_signature = _user_reciter_source_signature(username)


def _summary_payload_from_reciter(reciter: WordReciter) -> dict:
    """首屏/导航所需轻量统计，不补查词库例句。"""
    total_pending = len(reciter.all_words)
    due_count = reciter.count_due_words()
    task_progress = reciter.daily_task_progress()
    task_remaining = int(task_progress.get('remaining') or 0)
    review_states = reciter.learning_state_v2.get('review_states')
    state_rows = review_states if isinstance(review_states, dict) else {}
    reinforcement_count = sum(
        1
        for word in reciter.mastered_words
        if isinstance(state_rows.get(reciter.word_state_key(word)), dict)
        and state_rows[reciter.word_state_key(word)].get('memory_status') == 'reinforcement'
    )
    stable_count = max(0, len(reciter.mastered_words) - reinforcement_count)
    avg_review_count = (
        sum(w.review_count for w in reciter.all_words) / total_pending
        if total_pending
        else 0
    )
    stats = {
        "total_words": total_pending,
        "mastered_words": len(reciter.mastered_words),
        "mastered_stable": stable_count,
        "mastered_reinforcement": reinforcement_count,
        "current_round": reciter.current_review_round,
        "avg_review_count": avg_review_count,
        "due_count": due_count,
        "today_task": {
            "total": int(task_progress.get('total') or 0),
            "completed": int(task_progress.get('completed') or 0),
            "remaining": task_remaining,
            "estimated_minutes": (
                max(1, round(task_remaining * 0.6)) if task_remaining else 0
            ),
        },
    }
    return {"due_count": due_count, "stats": stats}


def _review_words_payload(
    reciter: WordReciter,
    review_list: List[Any],
    task_bundle: Optional[dict] = None,
    *,
    listening_available: bool = False,
) -> dict:
    """复习列表序列化；供 /words/review 与 /bootstrap 复用。"""
    words = []
    today_d = china_today()
    task_items = {}
    plan = None
    if isinstance(task_bundle, dict):
        plan = task_bundle.get('plan') if isinstance(task_bundle.get('plan'), dict) else None
        for task_item in task_bundle.get('items') or []:
            if isinstance(task_item, dict) and task_item.get('word_key'):
                task_items[str(task_item['word_key'])] = task_item
    for w in review_list:
        nd = w.next_review_date
        is_carryover = nd < today_d
        if listening_available:
            state_payload = reciter.review_state_payload(
                w,
                listening_available=True,
            )
        else:
            state_payload = reciter.review_state_payload(w)
        task_item = task_items.get(reciter.word_state_key(w), {})
        item = {
            'english': w.english,
            'chinese': w.chinese,
            'success_count': w.success_count,
            'max_success_count': reciter.config.MAX_SUCCESS_COUNT,
            'review_count': w.review_count,
            'example': w.example,
            'example_form': '',
            'scheduled_due_date': nd.isoformat(),
            'is_carryover': is_carryover,
            'carryover_days': (today_d - nd).days if is_carryover else 0,
            'examples': [],
            'task_id': str((plan or {}).get('task_id') or ''),
            'task_item_id': str(task_item.get('item_id') or ''),
            'task_reason': str(task_item.get('reason') or ('overdue' if is_carryover else 'due')),
            'task_calibration': str(task_item.get('calibration_reason') or ''),
            'task_attempts': reciter.task_attempt_count(task_item),
            'task_phase': str(task_item.get('phase') or 'main'),
            'task_remedial': bool(
                task_item.get('phase') == 'remedial'
                or reciter.task_attempt_count(task_item) >= exercise_attempt_limit(
                    str(task_item.get('exercise_type') or state_payload['exercise_type'])
                )
            ),
            'exercise_type': str(
                task_item.get('exercise_type') or state_payload['exercise_type']
            ),
            'mastery': state_payload['mastery'],
            'scheduler': state_payload['scheduler'],
            'memory_status': str(state_payload.get('memory_status') or 'learning'),
        }
        # 优先词库（v2 或 CSV）释义与例句；多义项只展示当前槽对应的一条
        csv_row = lookup_csv_word(w.english)
        if csv_row:
            if (csv_row.get("chinese") or "").strip():
                item["chinese"] = (csv_row.get("chinese") or "").strip()
            apply_review_display_from_wordbank(item, csv_row, w.english)
            csl = csv_row.get("chinese_sense_lines")
            if isinstance(csl, list) and csl:
                item["chinese_sense_lines"] = [str(x).strip() for x in csl if str(x).strip()]
        if not item['examples'] and (getattr(w, 'example', None) or '').strip():
            raw = (w.example or '').strip()
            if '_' in raw:
                a, b = raw.split('_', 1)
                item['examples'] = [{'en': a.strip(), 'cn': b.strip()}]
            else:
                item['examples'] = [{'en': raw, 'cn': ''}]
        if item['task_reason'] == 'new' and item['task_attempts'] == 0:
            item['study'] = {
                'english': w.english,
                'chinese': item['chinese'],
                'phonetic': item.get('phonetic', ''),
                'examples': list(item.get('examples') or []),
            }
        if item['exercise_type'] in gaokao_questions.QUESTION_TYPES:
            question = gaokao_questions.get_question(w.english, item['exercise_type'])
            if question:
                item['question'] = gaokao_questions.public_question(question)
                item['question_required'] = False
                task_item['question_id'] = question['question_id']
            else:
                item['question_required'] = True
            item['chinese'] = ''
            item['example'] = ''
            item['example_form'] = ''
            item['examples'] = []
            item.pop('chinese_sense_lines', None)
            if item['exercise_type'] == 'context':
                item['english'] = f"question:{item['task_item_id']}"
        words.append(item)
    payload = {'words': words, 'count': len(words)}
    if plan is not None:
        payload['plan'] = plan
    return payload


def _reliable_listening_available() -> bool:
    if piper_runtime_ready is None:
        return False
    try:
        return bool(piper_runtime_ready())
    except Exception:
        return False


def sanitize_tts_text(text: str, max_len: int = 500) -> str:
    """去除控制字符并限制长度，避免异常输入与命令注入面。"""
    text = (text or "").strip()[:max_len]
    return "".join(ch for ch in text if ch.isprintable() or ch.isspace()).strip()[:max_len]

# ==================== 路由 ====================

@app.route('/')
def index():
    """主页"""
    return send_file(Path(app.root_path) / 'static' / 'index.html')

@app.route('/static/<path:path>')
def send_static(path):
    """静态文件服务"""
    resp = send_from_directory(Path(app.root_path) / 'static', path)
    ext = path.rsplit('.', 1)[-1].lower() if '.' in path else ''
    if ext in ('css', 'js'):
        resp.cache_control.no_cache = None
        resp.cache_control.max_age = 3600
        resp.cache_control.public = True
    elif ext in ('png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'ico', 'woff', 'woff2'):
        resp.cache_control.no_cache = None
        resp.cache_control.max_age = 86400
        resp.cache_control.public = True
    return resp


@app.route('/api/performance/config', methods=['GET'])
def performance_config():
    """浏览器端性能采样配置。"""
    return jsonify(
        {
            "enabled": performance_enabled(),
            "sample_rate": browser_sample_rate(),
            "slow_api_ms": slow_request_threshold_ms(),
            "max_events": max_report_events(),
        }
    ), 200


@app.route('/api/performance/report', methods=['POST'])
def performance_report():
    """接收浏览器端性能事件，写入本地 JSONL。"""
    if not performance_enabled():
        return jsonify({"ok": True, "stored": 0, "disabled": True}), 200
    if not _rate_allow(f"perf:{_client_ip()}", _RATE_MAX_PERF_REPORT):
        return jsonify({"error": "请求过于频繁，请稍后再试"}), 429

    clen = request.content_length
    if clen is not None and clen > max_report_bytes():
        return jsonify({"error": "性能上报内容过大"}), 413
    data = request.get_json(silent=True) or {}
    raw_events = data.get("events")
    if isinstance(raw_events, dict):
        raw_events = [raw_events]
    if not isinstance(raw_events, list):
        return jsonify({"error": "events 须为数组"}), 400

    limit = max_report_events()
    events = []
    login_username = None
    effective_username = None
    is_parent = None
    auth_header = request.headers.get("Authorization") or ""
    if auth_header.startswith("Bearer "):
        login_username = verify_token(auth_header[7:].strip())
        if login_username:
            urow = get_user(login_username)
        else:
            urow = None
        if _user_row_is_enabled(urow):
            if isinstance(urow, dict) and is_parent_user_record(urow):
                child = (urow.get("child_username") or "").strip()
                effective_username = child if child and is_valid_username(child) else None
                is_parent = True
            else:
                effective_username = login_username
                is_parent = False

    for ev in raw_events[:limit]:
        if not isinstance(ev, dict):
            continue
        row = dict(ev)
        row["source"] = "browser"
        row.setdefault("request_id", getattr(g, "request_id", ""))
        row["remote_addr"] = _client_ip()
        row["server_received_user_agent"] = _safe_user_agent()
        if login_username:
            row["user"] = {
                "login_username": login_username,
                "effective_username": effective_username,
                "is_parent": is_parent,
            }
        events.append(row)
    stored = write_performance_events(DATA_DIR, events)
    return jsonify({"ok": True, "stored": stored}), 202


@app.route('/api/auth/register', methods=['POST'])
def register():
    """用户注册"""
    try:
        if not _rate_allow(f"reg:{_client_ip()}", _RATE_MAX_REGISTER):
            return jsonify({'error': '请求过于频繁，请稍后再试'}), 429

        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({'error': '无效的JSON数据'}), 400
        
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        try:
            email = normalize_email(data.get('email'))
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        
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
                'created_at': china_now_iso(timespec="seconds"),
                'access_token': token,
                'token_type': 'bearer',
                **_auth_session_payload(username),
            }), 201
        return jsonify({'error': err or '注册失败'}), 400
    except Exception as e:
        logger.error(f"注册失败: {e}")
        return jsonify({'error': '服务器内部错误'}), 500


@app.route('/api/auth/forgot-password', methods=['POST'])
def forgot_password():
    """按邮箱发送一次性重置链接；无论邮箱是否存在均返回统一结果。"""
    if not _rate_allow(f"forgot_password:{_client_ip()}", _RATE_MAX_FORGOT_PASSWORD):
        return jsonify({'error': '请求过于频繁，请稍后再试'}), 429
    data = request.get_json(silent=True) or {}
    try:
        email = normalize_email(data.get('email'))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    if not email:
        return jsonify({'error': '请填写邮箱'}), 400

    generic = {'message': '如果该邮箱已绑定账号，重置邮件将在几分钟内发送'}
    try:
        _smtp_config()
        ttl_minutes, cooldown_seconds = _password_reset_policy()
    except RuntimeError as exc:
        logger.error("无法发送密码重置邮件: %s", exc)
        return jsonify({'error': '邮件服务暂不可用，请联系管理员'}), 503

    users = load_users()
    matches = [
        uname
        for uname, row in users.items()
        if isinstance(row, dict)
        and str(row.get('email') or '').strip().casefold() == email.casefold()
        and row.get('enabled', True) is not False
    ]
    if len(matches) != 1:
        if len(matches) > 1:
            logger.error("邮箱绑定到多个账号，拒绝发送重置邮件: %s", email)
        return jsonify(generic), 200

    login_username = matches[0]
    cooldown_token = None
    if cooldown_seconds > 0:
        cooldown_token = create_session_if_absent(
            SESSION_KIND_PASSWORD_RESET_COOLDOWN,
            login_username,
            timedelta(seconds=cooldown_seconds),
        )
        if not cooldown_token:
            return jsonify(generic), 200
    revoke_principal(SESSION_KIND_PASSWORD_RESET, login_username)
    reset_token = _db_create_auth_session(
        SESSION_KIND_PASSWORD_RESET,
        login_username,
        timedelta(minutes=ttl_minutes),
    )
    reset_url = f"{_public_origin()}/?reset_token={quote(reset_token, safe='')}"
    try:
        _send_password_reset_email(email, reset_url, ttl_minutes)
    except Exception as exc:
        revoke_token(SESSION_KIND_PASSWORD_RESET, reset_token)
        if cooldown_token:
            revoke_token(SESSION_KIND_PASSWORD_RESET_COOLDOWN, cooldown_token)
        logger.exception("发送密码重置邮件失败: %s", exc)
    return jsonify(generic), 200


@app.route('/api/auth/reset-password', methods=['POST'])
def reset_password():
    if not _rate_allow(f"reset_password:{_client_ip()}", _RATE_MAX_RESET_PASSWORD):
        return jsonify({'error': '请求过于频繁，请稍后再试'}), 429
    data = request.get_json(silent=True) or {}
    reset_token = str(data.get('token') or '').strip()
    password = str(data.get('password') or '').strip()
    password_confirm = str(data.get('password_confirm') or '').strip()
    if len(password) < 6:
        return jsonify({'error': '密码至少6个字符'}), 400
    if password != password_confirm:
        return jsonify({'error': '两次输入的密码不一致'}), 400

    login_username = consume_session(reset_token, SESSION_KIND_PASSWORD_RESET)
    if not login_username:
        return jsonify({'error': '重置链接无效或已过期，请重新申请'}), 400

    def _set_password(users: Dict[str, Any]) -> bool:
        row = users.get(login_username)
        if not isinstance(row, dict):
            return False
        row['password_hash'] = hash_password(password)
        return True

    if not mutate_users(_set_password):
        return jsonify({'error': '重置链接无效或已过期，请重新申请'}), 400
    revoke_principal(SESSION_KIND_PASSWORD_RESET, login_username)
    _revoke_user_tokens(login_username)
    logger.info("用户通过邮箱重置密码: %s", login_username)
    return jsonify({'message': '密码已重置，请使用新密码登录'}), 200

def _auth_session_payload(login_username: str) -> dict:
    """供登录与 /api/auth/session 返回家长/学生标识。"""
    u = get_user(login_username)
    out = {
        'login_username': login_username,
        'is_parent': False,
        'child_username': None,
        'system_broadcast': pending_system_broadcast_for_login(login_username),
    }
    if isinstance(u, dict) and is_parent_user_record(u):
        out['is_parent'] = True
        out['child_username'] = u.get('child_username')
    return out


@app.route('/api/auth/login', methods=['POST'])
def login():
    """用户登录"""
    try:
        if not _rate_allow(f"login:{_client_ip()}", _RATE_MAX_LOGIN):
            return jsonify({'error': '登录尝试过多，请稍后再试'}), 429

        # 支持表单数据和JSON数据
        if request.is_json:
            data = request.get_json(silent=True)
            if not isinstance(data, dict):
                return jsonify({'error': '无效的JSON数据'}), 400
            username = data.get('username', '').strip()
            password = data.get('password', '').strip()
        else:
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '').strip()
        
        if not username or not password:
            return jsonify({'error': '用户名和密码不能为空'}), 400

        if verify_user(username, password):
            urow = get_user(username)
            if not isinstance(urow, dict) or urow.get('enabled', True) is False:
                return jsonify({'error': '账号已停用，请联系管理员'}), 403
            if isinstance(urow, dict) and is_parent_user_record(urow):
                child = (urow.get('child_username') or '').strip()
                if not child or not is_valid_username(child):
                    return jsonify({'error': '家长账户配置错误'}), 403
                child_row = get_user(child)
                if not isinstance(child_row, dict) or child_row.get('enabled', True) is False:
                    return jsonify({'error': '学生账号已停用，无法以家长身份登录'}), 403
            token = create_token(username)
            body = {
                'access_token': token,
                'token_type': 'bearer',
                'username': username,
                **_auth_session_payload(username),
            }
            return jsonify(body), 200
        return jsonify({'error': '用户名或密码错误'}), 401
    except Exception as e:
        logger.error(f"登录失败: {e}")
        return jsonify({'error': '服务器内部错误'}), 500

@app.route('/api/auth/logout', methods=['POST'])
@token_required
def logout(username):
    """用户退出"""
    # 清除所有该登录身份的 token（家长与学生登录名不同）
    login = getattr(g, "login_username", username)
    _revoke_user_tokens(login)
    return jsonify({'message': '已退出登录'}), 200


@app.route('/api/auth/session', methods=['GET'])
@token_required
def auth_session(username):
    """刷新页后恢复 is_parent / child_username。"""
    login = getattr(g, 'login_username', username)
    return jsonify(_auth_session_payload(login)), 200


@app.route('/api/bootstrap', methods=['GET'])
@token_required
def bootstrap(username):
    """首屏聚合数据：用一条请求替代多条关键请求，降低首次打开失败概率。"""
    try:
        login = getattr(g, 'login_username', username)
        session_payload = _auth_session_payload(login)
        listening_available = _reliable_listening_available()
        with user_reciter_session(username) as reciter:
            mastered_n = len(reciter.mastered_words)
            if getattr(g, "is_parent", False):
                review = {'words': [], 'count': 0}
            else:
                task_bundle = reciter.get_today_learning_plan(
                    listening_available=listening_available,
                )
                review = _review_words_payload(
                    reciter,
                    task_bundle['words'],
                    task_bundle,
                    listening_available=listening_available,
                )
                reciter.save_learning_data(backup=False)
            summary = _summary_payload_from_reciter(reciter)

        pkw, pkm = _pk_stats_for_gamification(username)
        gam_payload = gamification_mod.public_profile(
            DATA_DIR,
            username,
            mastered_words=mastered_n,
            pk_wins=pkw,
            pk_matches=pkm,
        )

        av = user_avatar_disk_path(username)
        return jsonify({
            "session": session_payload,
            "summary": summary,
            "review": review,
            "gamification": gam_payload,
            "avatar_url": f"/api/user/avatar/{username}" if av else None,
            "plan": get_user_plan(username),
            "article_ai_extract_available": bool(get_deepseek_api_key()),
            "article_ai_extract_enabled": _article_ai_extract_enabled(),
            "tts_capabilities": {
                "piper": listening_available,
            },
        }), 200
    except Exception as e:
        logger.error("首屏 bootstrap 失败: %s", e)
        return jsonify({'error': '服务器内部错误'}), 500


@app.route('/api/auth/broadcast/ack', methods=['POST'])
@token_required
def ack_system_broadcast(username):
    """用户确认已读当前系统广播，之后不再展示同一条。"""
    login = getattr(g, 'login_username', username)
    data = request.get_json(silent=True) or {}
    bid = (data.get('id') or '').strip()
    if not bid:
        return jsonify({'error': '缺少广播 id'}), 400
    with _SYSTEM_BROADCAST_LOCK:
        doc = _read_system_broadcast_unlocked()
    cur = (doc.get('id') or '').strip()
    if bid != cur:
        return jsonify({'error': '无效或过期的广播'}), 400
    lock = _user_mutex(login)
    with lock:
        _write_user_broadcast_ack(login, bid)
    return jsonify({'ok': True}), 200


@app.route('/api/auth/parent-password', methods=['PATCH'])
@token_required
def patch_parent_password(username):
    """家长修改自己的登录密码（不影响学生账号）。"""
    if not getattr(g, 'is_parent', False):
        return jsonify({'error': '仅家长账户可修改'}), 403
    data = request.get_json(silent=True) or {}
    p1 = (data.get('password') or '').strip()
    p2 = (data.get('password_confirm') or '').strip()
    if len(p1) < 6:
        return jsonify({'error': '密码至少6个字符'}), 400
    if p1 != p2:
        return jsonify({'error': '两次输入的密码不一致'}), 400
    login = getattr(g, 'login_username', username)
    def _set_password(users: Dict[str, Any]) -> bool:
        if login not in users:
            return False
        users[login]['password_hash'] = hash_password(p1)
        return True

    if not mutate_users(_set_password):
        return jsonify({'error': '用户不存在'}), 404
    logger.info("家长账户修改密码: login=%s", login)
    return jsonify({'message': '密码已更新'}), 200


@app.route('/api/user/learning-settings', methods=['GET', 'PATCH'])
@token_required
def user_learning_settings(username):
    """家长读取或调整关联学生的每日学习任务上限。"""
    if not getattr(g, 'is_parent', False):
        return jsonify({'error': '仅家长账户可调整学习任务上限'}), 403

    if request.method == 'PATCH':
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({'error': '无效的JSON数据'}), 400
        try:
            daily_review_limit = data['daily_review_limit']
        except KeyError:
            return jsonify({'error': '请填写每日任务上限'}), 400
        if (
            not isinstance(daily_review_limit, int)
            or isinstance(daily_review_limit, bool)
        ):
            return jsonify({'error': '学习任务上限须为整数'}), 400
        if not 1 <= daily_review_limit <= MAX_DAILY_REVIEW_LIMIT:
            return jsonify({
                'error': f'每日任务上限须在 1–{MAX_DAILY_REVIEW_LIMIT} 之间',
            }), 400
        with _user_mutex(username):
            with _user_learning_interprocess_lock(username):
                settings = _read_user_learning_settings(username)
                settings['daily_review_limit'] = daily_review_limit
                settings.pop('daily_new_word_limit', None)
                _write_user_learning_settings(username, settings)
        _invalidate_user_reciter_cache(username)
        logger.info(
            "家长更新学生学习任务上限: student=%s daily=%s",
            username,
            daily_review_limit,
        )

    config = _build_user_config(username)
    return jsonify({
        'daily_review_limit': config.DAILY_REVIEW_LIMIT,
        'default_daily_review_limit': DEFAULT_DAILY_REVIEW_LIMIT,
        'max_daily_review_limit': MAX_DAILY_REVIEW_LIMIT,
        'new_words_are_automatic': True,
        'minimum_review_share_percent': 60,
        'applies_from_next_task': True,
    }), 200


def _pk_stats_for_gamification(username: str) -> Tuple[int, int]:
    st = challenges_mod.pk_user_stats_from_duels(DATA_DIR, username)
    return int(st.get("pk_wins") or 0), int(st.get("pk_matches") or 0)


@app.route('/api/gamification', methods=['GET'])
@token_required
def get_gamification(username):
    """XP、等级、连续打卡、成就列表"""
    try:
        with user_reciter_session(username) as reciter:
            mastered_n = len(reciter.mastered_words)
        pkw, pkm = _pk_stats_for_gamification(username)
        profile = gamification_mod.public_profile(
            DATA_DIR,
            username,
            mastered_words=mastered_n,
            pk_wins=pkw,
            pk_matches=pkm,
        )
        return jsonify(profile), 200
    except Exception as e:
        logger.error(f"获取游戏化数据失败: {e}")
        return jsonify({'error': '服务器内部错误'}), 500


@app.route('/api/gamification/xp-history', methods=['GET'])
@token_required
def get_xp_history(username):
    """最近 2 个月 XP 收支历史"""
    try:
        return jsonify(gamification_mod.xp_history_recent(DATA_DIR, username)), 200
    except Exception as e:
        logger.error(f"获取 XP 收支历史失败: {e}")
        return jsonify({'error': '服务器内部错误'}), 500


@app.route('/api/gamification', methods=['PATCH'])
@token_required
@parent_forbidden
def patch_gamification_settings(username):
    """更新排行榜展示、本月打卡目标等"""
    try:
        data = request.get_json()
        if data is None:
            return jsonify({'error': '无效的JSON数据'}), 400
        opt_in = data.get('leaderboard_opt_in')
        if opt_in is not None and not isinstance(opt_in, bool):
            return jsonify({'error': 'leaderboard_opt_in 须为布尔值'}), 400
        monthly_goal = None
        clear_monthly_goal = False
        if 'monthly_checkin_goal' in data:
            mg = data.get('monthly_checkin_goal')
            if mg is None or mg == '':
                clear_monthly_goal = True
            else:
                try:
                    monthly_goal = int(mg)
                except (TypeError, ValueError):
                    return jsonify({'error': 'monthly_checkin_goal 须为整数'}), 400
        try:
            out = gamification_mod.patch_settings(
                DATA_DIR,
                username,
                leaderboard_opt_in=opt_in,
                monthly_checkin_goal=monthly_goal,
                clear_monthly_goal=clear_monthly_goal,
            )
        except ValueError as ve:
            return jsonify({'error': str(ve)}), 400
        with user_reciter_session(username) as reciter:
            mastered_n = len(reciter.mastered_words)
        pkw, pkm = _pk_stats_for_gamification(username)
        profile = gamification_mod.public_profile(
            DATA_DIR,
            username,
            mastered_words=mastered_n,
            pk_wins=pkw,
            pk_matches=pkm,
        )
        return jsonify({**out, **{k: profile[k] for k in (
            'month_key', 'month_valid_checkin_days', 'month_days_in_month',
            'monthly_checkin_goal', 'monthly_checkin_goal_month',
            'monthly_checkin_goal_suggested_days', 'monthly_checkin_goal_max_days',
            'monthly_checkin_goal_progress_days', 'monthly_checkin_goal_can_edit',
            'today_correct_count', 'check_in_done_today', 'check_in_min_correct',
            'daily_xp_soft_cap', 'daily_xp_hard_cap',
            'checkin_completion_xp', 'checkin_streak_bonus_xp_per_day',
            'checkin_streak_bonus_cap_days',
            'monthly_goal_completion_bonus_xp',
            'monthly_goal_bonus_awarded_this_month', 'checkin_goal_xp_per_day',
            'total_xp', 'lifetime_xp', 'xp_balance', 'level', 'xp_to_next_level',
            'makeup_checkin', 'makeup_checkin_days_this_month',
        ) if k in profile}}), 200
    except Exception as e:
        logger.error(f"更新游戏化设置失败: {e}")
        return jsonify({'error': '服务器内部错误'}), 500


@app.route('/api/gamification/makeup-checkin', methods=['POST'])
@token_required
@parent_forbidden
def post_makeup_checkin(username):
    """用 XP 补救昨天的连续打卡火苗；不计入真实打卡天数。"""
    try:
        with user_reciter_session(username) as reciter:
            mastered_n = len(reciter.mastered_words)
        pkw, pkm = _pk_stats_for_gamification(username)
        try:
            purchase = gamification_mod.purchase_makeup_checkin(
                DATA_DIR,
                username,
                mastered_words=mastered_n,
                pk_wins=pkw,
                pk_matches=pkm,
            )
        except ValueError as ve:
            return jsonify({'error': str(ve)}), 400

        profile = gamification_mod.public_profile(
            DATA_DIR,
            username,
            mastered_words=mastered_n,
            pk_wins=pkw,
            pk_matches=pkm,
        )
        return jsonify({**profile, "makeup_checkin_purchase": purchase}), 200
    except Exception as e:
        logger.error(f"补打卡失败: {e}")
        return jsonify({'error': '服务器内部错误'}), 500


@app.route('/api/leaderboard', methods=['GET'])
@token_required
def get_leaderboard(username):
    """排行榜：scope=total 为累计 XP；week=本周（周一至周日 ISO 周）；month=本月。周/月榜仅统计 daily_xp 汇总。"""
    try:
        users = load_users()
        enabled = [
            u for u in users
            if _user_row_is_enabled(users.get(u))
            and not is_parent_user_record(users[u])
        ]
        scope = (request.args.get("scope") or "total").strip().lower()
        if scope not in ("total", "week", "month"):
            return jsonify({"error": "scope 须为 total、week 或 month"}), 400

        states = gamification_mod.load_states_batch(DATA_DIR, enabled)
        if scope in ("week", "month"):
            leaderboard_periods_mod.settle_periods_if_needed(DATA_DIR, enabled, states)

        avatar_cache: Dict[str, Optional[str]] = {}
        if scope == "total":
            rows = gamification_mod.build_leaderboard_from_states(
                states, enabled, viewer=username
            )
            for r in rows:
                r["avatar_url"] = _leaderboard_avatar_url(r.get("username") or "", avatar_cache)
            return jsonify({"scope": "total", "leaderboard": rows}), 200

        if scope == "week":
            payload = leaderboard_periods_mod.build_week_leaderboard_payload(
                DATA_DIR, enabled, viewer=username, states=states
            )
        else:
            payload = leaderboard_periods_mod.build_month_leaderboard_payload(
                DATA_DIR, enabled, viewer=username, states=states
            )
        for r in payload.get("leaderboard") or []:
            r["avatar_url"] = _leaderboard_avatar_url(r.get("username") or "", avatar_cache)
        pl = payload.get("podium_last_period")
        if pl and isinstance(pl.get("top"), list):
            for t in pl["top"]:
                t["avatar_url"] = _leaderboard_avatar_url(t.get("username") or "", avatar_cache)
        return jsonify(payload), 200
    except Exception as e:
        logger.error(f"获取排行榜失败: {e}")
        return jsonify({'error': '服务器内部错误'}), 500


@app.route('/api/user/settings', methods=['GET'])
@token_required
def get_user_settings(username):
    """设置页汇总：游戏化、月度奖池、挑战列表、头像。"""
    try:
        with user_reciter_session(username) as reciter:
            mastered_n = len(reciter.mastered_words)
        pkw, pkm = _pk_stats_for_gamification(username)
        prof = gamification_mod.public_profile(
            DATA_DIR,
            username,
            mastered_words=mastered_n,
            pk_wins=pkw,
            pk_matches=pkm,
        )
        pool = enrich_monthly_pool_with_avatars(
            challenges_mod.get_monthly_pool_state(DATA_DIR, username)
        )
        duels = challenges_mod.list_duels_for_user(DATA_DIR, username)
        av = user_avatar_disk_path(username)
        prof["avatar_url"] = f"/api/user/avatar/{username}" if av else None
        prof.update(invite_quota_payload(load_users().get(username) or {}))
        with _locked_invite_storage():
            prof["invites"] = _list_invites_created_by(username)
        prof["invite_register_url"] = f"{_public_origin()}/"
        prof["monthly_pool"] = pool
        prof["duels"] = duels
        prof["wager_tiers"] = list(challenges_mod.WAGER_TIERS)
        prof["stake_safety_reserve_xp"] = challenges_mod.STAKE_SAFETY_RESERVE_XP
        prof["duel_opponents"] = list_challenge_opponent_usernames(username)
        return jsonify(prof), 200
    except Exception as e:
        logger.error(f"获取用户设置失败: {e}")
        return jsonify({'error': '服务器内部错误'}), 500


@app.route('/api/user/email', methods=['GET', 'PATCH'])
@token_required
def user_email(username):
    """读取或更新当前登录身份的邮箱；家长账号不会写到关联学生上。"""
    login_username = getattr(g, 'login_username', username)
    if request.method == 'GET':
        row = load_users().get(login_username)
        if not isinstance(row, dict):
            return jsonify({'error': '用户不存在'}), 404
        return jsonify({'email': row.get('email') or ''}), 200

    data = request.get_json(silent=True) or {}
    try:
        email = normalize_email(data.get('email'))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    def _set_email(users: Dict[str, Any]) -> Tuple[bool, str]:
        row = users.get(login_username)
        if not isinstance(row, dict):
            return False, '用户不存在'
        if email and _email_owner(users, email, exclude=login_username):
            return False, '该邮箱已被其他账号使用'
        if email:
            row['email'] = email
        else:
            row.pop('email', None)
        return True, ''

    ok, error = mutate_users(_set_email)
    if not ok:
        return jsonify({'error': error}), 404 if error == '用户不存在' else 409
    return jsonify({
        'email': email or '',
        'message': '邮箱已保存' if email else '邮箱已移除',
    }), 200


@app.route('/api/user/avatar-meta', methods=['GET'])
@token_required
def get_user_avatar_meta(username):
    """轻量头像元信息；避免顶栏头像刷新调用完整 settings。"""
    av = user_avatar_disk_path(username)
    return jsonify({
        'avatar_url': f"/api/user/avatar/{username}" if av else None,
    }), 200


@app.route('/api/user/avatar/<uname>', methods=['GET'])
def get_user_avatar_file(uname):
    """公开读取头像（供 img src）。可选 ?w=64 等生成小尺寸 WebP，减轻传输。"""
    path = user_avatar_disk_path(uname)
    if not path:
        return '', 404
    wq = request.args.get("w", type=int)
    if (
        wq is not None
        and PILImage is not None
        and AVATAR_THUMB_MIN <= wq <= AVATAR_THUMB_MAX
    ):
        try:
            im = PILImage.open(path)
            im = _avatar_pil_to_rgb(im)
            im = im.resize((wq, wq), PILImage.LANCZOS)
            buf = BytesIO()
            im.save(
                buf,
                "WEBP",
                quality=AVATAR_THUMB_WEBP_QUALITY,
                method=4,
            )
            buf.seek(0)
            return send_file(buf, mimetype="image/webp", max_age=86400)
        except Exception as e:
            logger.warning("头像缩略图生成失败，回退原文件: %s", e)
    return send_file(path, max_age=3600)


@app.route('/api/user/avatar', methods=['POST'])
@token_required
@parent_forbidden
def post_user_avatar(username):
    """上传头像：有 Pillow 时统一为压缩 WebP；否则原样保存为单个 avatar.<ext>（覆盖旧文件）。"""
    if 'file' not in request.files:
        return jsonify({'error': '缺少 file 字段'}), 400
    f = request.files['file']
    if not f or not f.filename:
        return jsonify({'error': '未选择文件'}), 400
    ct = (f.mimetype or '').lower()
    if ct not in ('image/jpeg', 'image/png', 'image/webp'):
        return jsonify({'error': '仅支持 JPEG、PNG、WebP'}), 400
    ext_map = {
        'image/jpeg': '.jpg',
        'image/png': '.png',
        'image/webp': '.webp',
    }
    user_dir = DATA_DIR / username
    user_dir.mkdir(parents=True, exist_ok=True)
    for old in user_dir.glob('avatar.*'):
        try:
            old.unlink()
        except OSError:
            pass
    if PILImage is not None:
        dst = user_dir / "avatar.webp"
        try:
            _save_user_avatar_webp(f.stream, dst)
        except Exception as e:
            logger.error(f"保存头像失败: {e}")
            try:
                if dst.exists():
                    dst.unlink()
            except OSError:
                pass
            return jsonify({'error': '无法解析或保存图片'}), 400
    else:
        dst = user_dir / f"avatar{ext_map[ct]}"
        logger.warning(
            "Pillow 未安装，头像以原图保存；建议执行 pip install -r requirements-simple.txt"
        )
        try:
            f.stream.seek(0)
        except (OSError, AttributeError, TypeError):
            pass
        try:
            f.save(dst)
        except OSError as e:
            logger.error(f"保存头像失败: {e}")
            return jsonify({'error': '保存失败'}), 500
    return jsonify({
        'ok': True,
        'avatar_url': f'/api/user/avatar/{username}',
    }), 200


@app.route('/api/user/avatar', methods=['DELETE'])
@token_required
@parent_forbidden
def delete_user_avatar(username):
    path = user_avatar_disk_path(username)
    if path:
        try:
            path.unlink()
        except OSError:
            pass
    return jsonify({'ok': True, 'avatar_url': None}), 200


@app.route('/api/user/invites', methods=['GET'])
@token_required
@parent_forbidden
def get_user_invites(username):
    """当前用户创建的邀请码列表与剩余额度（不含明文）。"""
    users = load_users()
    u = users.get(username)
    if not isinstance(u, dict) or is_parent_user_record(u):
        return jsonify({'error': '用户不存在'}), 404
    with _locked_invite_storage():
        invites = _list_invites_created_by(username)
    return jsonify({
        **invite_quota_payload(u),
        "invites": invites,
        "invite_register_url": f"{_public_origin()}/",
    }), 200


@app.route('/api/user/invites/unused', methods=['GET'])
@token_required
@parent_forbidden
def get_unused_user_invites(username):
    """Return only this user's unused invite codes, without allowing intermediary caching."""
    users = load_users()
    u = users.get(username)
    if not isinstance(u, dict) or is_parent_user_record(u):
        return jsonify({'error': '用户不存在'}), 404
    with _locked_invite_storage():
        invites = _list_unused_invites_created_by(username)
    response = jsonify({
        **invite_quota_payload(u),
        "invites": invites,
        "invite_register_url": f"{_public_origin()}/",
    })
    response.headers["Cache-Control"] = "private, no-store"
    return response, 200


@app.route('/api/user/invites', methods=['POST'])
@token_required
@parent_forbidden
def create_user_invite(username):
    """当前用户生成一次性邀请码（每个用户默认最多 15 个）。"""
    with _locked_invite_storage():
        data = load_invites()
        invites = data.setdefault("invites", [])
        plain = _fresh_invite_code(invites).strip().upper()
        try:
            ciphertext = _encrypt_invite_code_for_storage(plain)
        except RuntimeError:
            return jsonify({"error": "邀请码安全存储暂不可用，请稍后重试"}), 503
        inv_id = str(uuid.uuid4())
        quota_after: Optional[dict] = None
        entry = {
            "id": inv_id,
            "code_hash": _hash_invite_code(plain),
            "code_ciphertext": ciphertext,
            "created_at": china_now_iso(timespec="seconds"),
            "created_by": username,
            "created_by_kind": "user",
            "used_at": None,
            "used_by": None,
        }

        def _consume_quota(users: Dict[str, Any]) -> Tuple[bool, int, str, Optional[dict]]:
            u = users.get(username)
            if not isinstance(u, dict):
                return False, 404, "用户不存在", None
            if is_parent_user_record(u):
                return False, 403, "家长账户不能生成邀请码", None
            limit = invite_quota_limit(u)
            used = invite_quota_used(u)
            if used >= limit:
                return False, 400, "邀请码次数已用完，请联系管理员重置", invite_quota_payload(u)
            u["invite_quota_limit"] = limit
            u["invite_quota_used"] = used + 1
            invites.append(entry)
            try:
                save_invites(data)
            except Exception:
                invites.remove(entry)
                raise
            return True, 201, "", invite_quota_payload(u)

        try:
            ok, status_code, err, quota_after = mutate_users(_consume_quota)
        except Exception:
            if entry in invites:
                invites.remove(entry)
                try:
                    save_invites(data)
                except Exception:
                    logger.exception("生成失败后恢复邀请码列表失败: invite_id=%s", inv_id)
            raise
        if not ok:
            return jsonify({"error": err, **(quota_after or {})}), status_code

    logger.info("用户生成邀请码: user=%s id=%s", username, inv_id)
    return jsonify({
        "id": inv_id,
        "invite_code": plain,
        "invite_register_url": f"{_public_origin()}/",
        "hint": "未使用前可在邀请窗口中再次选择",
        **(quota_after or {}),
    }), 201


@app.route('/api/monthly-pool', methods=['GET'])
@token_required
def api_monthly_pool_get(username):
    pool = enrich_monthly_pool_with_avatars(
        challenges_mod.get_monthly_pool_state(DATA_DIR, username)
    )
    return jsonify(pool), 200


@app.route('/api/monthly-pool/join', methods=['POST'])
@token_required
@parent_forbidden
def api_monthly_pool_join(username):
    ok, msg, state = challenges_mod.join_monthly_pool(DATA_DIR, username)
    if not ok:
        return jsonify({'error': msg}), 400
    return jsonify(state), 200


@app.route('/api/challenges/opponents', methods=['GET'])
@token_required
@parent_forbidden
def api_challenges_opponents(username):
    """1v1 可选择的对手（排行榜中展示的用户，不含自己）。"""
    return jsonify({'opponents': list_challenge_opponent_usernames(username)}), 200


@app.route('/api/challenges', methods=['GET'])
@token_required
@parent_forbidden
def api_challenges_list(username):
    return jsonify({'challenges': challenges_mod.list_duels_for_user(DATA_DIR, username)}), 200


@app.route('/api/challenges', methods=['POST'])
@token_required
@parent_forbidden
def api_challenges_create(username):
    if not _rate_allow(f"challenge_create:{username}", _RATE_MAX_CHALLENGE_CREATE):
        return jsonify({'error': '发起挑战过于频繁，请稍后再试'}), 429
    data = request.get_json() or {}
    target = (data.get('target_username') or '').strip()
    if not is_valid_username(target):
        return jsonify({'error': '无效的目标用户名'}), 400
    users = load_users()
    if target not in users:
        return jsonify({'error': '用户不存在'}), 400
    target_row = users.get(target)
    if (
        not _user_row_is_enabled(target_row)
        or is_parent_user_record(target_row)
    ):
        return jsonify({'error': '只能挑战已启用的学生账号'}), 400
    if target == username:
        return jsonify({'error': '不能挑战自己'}), 400
    try:
        wager = int(data.get('wager_xp', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'wager_xp 须为整数'}), 400
    ok, msg, row = challenges_mod.create_duel(DATA_DIR, username, target, wager_xp=wager)
    if not ok or not row:
        return jsonify({'error': msg or '创建失败'}), 400
    return jsonify(row), 201


@app.route('/api/challenges/<duel_id>/respond', methods=['POST'])
@token_required
@parent_forbidden
def api_challenges_respond(username, duel_id):
    if not _rate_allow(f"challenge_respond:{username}", _RATE_MAX_CHALLENGE_RESPOND):
        return jsonify({'error': 'PK 操作过于频繁，请稍后再试'}), 429
    data = request.get_json() or {}
    accept = bool(data.get('accept'))
    ok, msg, row = challenges_mod.respond_duel(DATA_DIR, duel_id, username, accept)
    if not ok or not row:
        return jsonify({'error': msg or '操作失败'}), 400
    return jsonify(row), 200


@app.route('/api/challenges/monthly-pk-board', methods=['GET'])
@token_required
def api_monthly_pk_board(username):
    """上月 PK 结算榜 + 本月进行中（全站）；用于排行榜页展示。"""
    board = challenges_mod.monthly_pk_board(DATA_DIR)
    board["viewer"] = username
    return jsonify(board), 200


@app.route('/api/words/status', methods=['GET'])
@token_required
def get_status(username):
    """获取学习状态"""
    try:
        with user_reciter_session(username) as reciter:
            all_words = []
            today_d = china_today()
            for w in reciter.all_words:
                csv_row = lookup_csv_word(w.english)
                nd = w.next_review_date
                is_co = nd < today_d
                examples_list: List[dict] = []
                if csv_row:
                    examples_list = examples_from_csv_row(csv_row)
                if not examples_list and getattr(w, 'example', None):
                    raw = (w.example or '').strip()
                    if raw:
                        if '_' in raw:
                            a, b = raw.split('_', 1)
                            examples_list = [{'en': a.strip(), 'cn': b.strip()}]
                        else:
                            examples_list = [{'en': raw, 'cn': ''}]
                ex_text = ''
                if examples_list:
                    fe = examples_list[0]
                    ex_text = merged_example_from_pair(fe.get('en', ''), fe.get('cn', ''))
                display_zh = w.chinese
                if csv_row and (csv_row.get("chinese") or "").strip():
                    display_zh = (csv_row.get("chinese") or "").strip()
                row_payload = {
                    'english': w.english,
                    'chinese': display_zh,
                    'phonetic': csv_row.get('phonetic', '') if csv_row else '',
                    'level': (csv_row.get('level') or '').strip() if csv_row else '',
                    'example': ex_text,
                    'examples': examples_list,
                    'success_count': w.success_count,
                    'max_success_count': reciter.config.MAX_SUCCESS_COUNT,
                    'review_round': w.review_round,
                    'review_count': w.review_count,
                    'next_review_date': nd.isoformat(),
                    'remaining_days': (nd - today_d).days,
                    'is_carryover': is_co,
                    'carryover_days': (today_d - nd).days if is_co else 0,
                    **reciter.review_state_payload(w),
                }
                if csv_row:
                    csl = csv_row.get("chinese_sense_lines")
                    if isinstance(csl, list) and csl:
                        row_payload["chinese_sense_lines"] = [str(x).strip() for x in csl if str(x).strip()]
                all_words.append(row_payload)

            overview_stats = _summary_payload_from_reciter(reciter)['stats']
            stats = {
                **overview_stats,
                'total_words': len(all_words),
                'mastered_words': len(reciter.mastered_words),
                'avg_review_count': sum(w['review_count'] for w in all_words) / len(all_words) if all_words else 0,
                'avg_mastery_percent': (
                    sum(w['mastery']['overall_percent'] for w in all_words) / len(all_words)
                    if all_words
                    else 0
                ),
            }

            return jsonify({'words': all_words, 'stats': stats}), 200
    except Exception as e:
        logger.error(f"获取状态失败: {e}")
        return jsonify({'error': '服务器内部错误'}), 500


@app.route('/api/words/summary', methods=['GET'])
@token_required
def get_words_summary(username):
    """获取导航与首屏所需轻量统计，避免为统计拉取完整单词列表。"""
    try:
        with user_reciter_session(username) as reciter:
            return jsonify(_summary_payload_from_reciter(reciter)), 200
    except Exception as e:
        logger.error(f"获取轻量统计失败: {e}")
        return jsonify({'error': '服务器内部错误'}), 500

@app.route('/api/words/review', methods=['GET'])
@token_required
def get_review_list(username):
    """获取今日复习列表（从CSV中补充 example_form、随机选择例句）"""
    try:
        listening_available = _reliable_listening_available()
        with user_reciter_session(username) as reciter:
            task_bundle = reciter.get_today_learning_plan(
                listening_available=listening_available,
            )
            payload = _review_words_payload(
                reciter,
                task_bundle['words'],
                task_bundle,
                listening_available=listening_available,
            )
            reciter.save_learning_data(backup=False)
            return jsonify(payload), 200
    except Exception as e:
        logger.error(f"获取复习列表失败: {e}")
        return jsonify({'error': '服务器内部错误'}), 500

@app.route('/api/words/extra-review', methods=['GET'])
@token_required
def get_extra_review_list(username):
    """今日无待复习时：从全词库按复习次数最少优先、同层随机抽取加练词（默认 5 个）。"""
    try:
        with user_reciter_session(username) as reciter:
            task_bundle = reciter.get_today_learning_plan(
                listening_available=_reliable_listening_available(),
            )
            if int((task_bundle.get('plan') or {}).get('remaining') or 0) > 0:
                return jsonify({'error': '请先完成今日学习任务，再开始随机加练'}), 409
            bonus_session_id, picked = reciter.create_bonus_practice_session(5)
            words = []
            for w in picked:
                nd = w.next_review_date
                state_payload = reciter.review_state_payload(w)
                item = {
                    'english': w.english,
                    'chinese': w.chinese,
                    'success_count': w.success_count,
                    'max_success_count': reciter.config.MAX_SUCCESS_COUNT,
                    'review_count': w.review_count,
                    'example': w.example,
                    'example_form': '',
                    'scheduled_due_date': nd.isoformat(),
                    'is_carryover': False,
                    'carryover_days': 0,
                    'examples': [],
                    'task_id': '',
                    'task_item_id': '',
                    'task_reason': 'bonus',
                    'bonus_session_id': bonus_session_id,
                    'exercise_type': 'spelling',
                    'mastery': state_payload['mastery'],
                    'scheduler': state_payload['scheduler'],
                }
                csv_row = lookup_csv_word(w.english)
                if csv_row:
                    if (csv_row.get("chinese") or "").strip():
                        item["chinese"] = (csv_row.get("chinese") or "").strip()
                    apply_review_display_from_wordbank(item, csv_row, w.english)
                    csl = csv_row.get("chinese_sense_lines")
                    if isinstance(csl, list) and csl:
                        item["chinese_sense_lines"] = [str(x).strip() for x in csl if str(x).strip()]
                words.append(item)
            reciter.save_learning_data(backup=False)
            return jsonify({
                'words': words,
                'count': len(words),
                'bonus_session_id': bonus_session_id,
            }), 200
    except Exception as e:
        logger.error(f"获取加练列表失败: {e}")
        return jsonify({'error': '服务器内部错误'}), 500

def _spelling_fallback_word_payload(word: Any) -> dict:
    item = {
        'english': word.english,
        'chinese': word.chinese,
        'example': word.example,
        'example_form': '',
        'examples': [],
    }
    row = lookup_csv_word(word.english)
    if row:
        if (row.get('chinese') or '').strip():
            item['chinese'] = (row.get('chinese') or '').strip()
        apply_review_display_from_wordbank(item, row, word.english)
    if not item['examples'] and (getattr(word, 'example', None) or '').strip():
        raw = (word.example or '').strip()
        if '_' in raw:
            english, chinese = raw.split('_', 1)
            item['examples'] = [{'en': english.strip(), 'cn': chinese.strip()}]
        else:
            item['examples'] = [{'en': raw, 'cn': ''}]
    return item


@app.route('/api/words/question', methods=['POST'])
@token_required
@parent_forbidden
def get_or_generate_word_question(username):
    """Load an approved private-bank question or fall back without blocking."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': '无效的JSON数据'}), 400
    word_id = str(data.get('word_id') or '').strip()
    task_id = str(data.get('task_id') or '').strip()
    task_item_id = str(data.get('task_item_id') or '').strip()
    requested_type = str(data.get('exercise_type') or '').strip()
    if not word_id or not task_id or not task_item_id:
        return jsonify({'error': '今日任务参数不完整，请刷新后重试'}), 400
    if requested_type not in gaokao_questions.QUESTION_TYPES:
        return jsonify({'error': '当前题型不需要加载选择题'}), 400

    try:
        with user_reciter_session(username) as reciter:
            task_item = reciter.resolve_daily_task_item(task_id, task_item_id, '')
            if not task_item:
                return jsonify({'error': '今日任务已更新，请刷新后继续'}), 409
            exercise_type = str(task_item.get('exercise_type') or '')
            if exercise_type != requested_type:
                return jsonify({'error': '当前任务题型已更新，请刷新后继续'}), 409
            word = reciter.find_word(task_item.get('word_key', ''), include_mastered=True)
            if not word:
                return jsonify({'error': '单词未找到'}), 404

            question = gaokao_questions.get_question(word.english, exercise_type)
            if not question:
                task_item['exercise_type'] = 'spelling'
                task_item.pop('question_id', None)
                task_item['question_fallback_reason'] = 'question_not_approved'
                reciter.save_learning_data(backup=False)
                return jsonify({
                    'fallback': True,
                    'exercise_type': 'spelling',
                    'message': '选择题尚未通过质检，已自动切换为拼写练习',
                    'word': _spelling_fallback_word_payload(word),
                }), 200

            task_item['question_id'] = question['question_id']
            task_item.pop('question_fallback_reason', None)
            reciter.save_learning_data(backup=False)
            return jsonify({
                'fallback': False,
                'generated': False,
                'exercise_type': exercise_type,
                'question': gaokao_questions.public_question(question),
            }), 200
    except Exception as exc:
        _invalidate_user_reciter_cache(username)
        logger.error(
            "加载高考选择题失败: user=%s word=%s error=%s",
            username,
            word_id,
            exc,
        )
        return jsonify({'error': '题目加载失败，请稍后重试'}), 500


@app.route('/api/words/practice', methods=['POST'])
@token_required
@parent_forbidden
def practice_word(username):
    """练习单词"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': '无效的JSON数据'}), 400
        
        word_id = str(data.get('word_id') or '').strip()
        answer = str(data.get('answer') or '').strip()
        selected_option_id = str(data.get('selected_option_id') or '').strip()
        question_id = str(data.get('question_id') or '').strip()
        # 当日错题巩固轮次：答对不计入掌握进度（success_count）与排期，见前端 wrongRoundNumber>0
        remedial = data.get('remedial') is True
        # 无今日待复习时的加练：仅计复习次数，不改变掌握进度与排期
        bonus_practice = data.get('bonus_practice') is True
        bonus_session_id = str(data.get('bonus_session_id') or '').strip()[:96]
        # True：要求拼写与例句中形式一致（如复数、时态）；False：仅词库原形（lemma）
        test_inflection = data.get('test_inflection') is True
        task_id = str(data.get('task_id') or '').strip()
        task_item_id = str(data.get('task_item_id') or '').strip()
        requested_exercise_type = str(data.get('exercise_type') or 'spelling').strip()
        review_event_id = str(data.get('review_event_id') or '').strip()[:96]
        audio_available = data.get('audio_available') is True
        try:
            attempt_number = max(1, min(1000, int(data.get('attempt_number') or 1)))
        except (TypeError, ValueError, OverflowError):
            attempt_number = 1
        try:
            hint_count = max(0, min(100, int(data.get('hint_count') or 0)))
        except (TypeError, ValueError, OverflowError):
            hint_count = 0
        try:
            elapsed_ms = max(0, min(600000, int(data.get('elapsed_ms') or 0)))
        except (TypeError, ValueError, OverflowError):
            elapsed_ms = 0

        if not word_id:
            return jsonify({'error': '单词ID不能为空'}), 400
        if bool(task_id) != bool(task_item_id):
            return jsonify({'error': '今日任务参数不完整，请刷新后重试'}), 400
        if not review_event_id:
            return jsonify({'error': '缺少作答事件标识，请刷新后重试'}), 400
        if not task_id and not bonus_practice:
            return jsonify({'error': '请从今日任务进入练习'}), 409
        if bonus_practice and not bonus_session_id:
            return jsonify({'error': '加练会话已失效，请重新获取随机加练'}), 409

        with user_reciter_session(username) as reciter:
            task_item = None
            if task_id and not bonus_practice:
                task_item = reciter.resolve_daily_task_item(
                    task_id,
                    task_item_id,
                    '' if requested_exercise_type in gaokao_questions.QUESTION_TYPES else word_id,
                    review_event_id,
                )
                if not task_item:
                    return jsonify({'error': '今日任务已更新，请刷新后继续'}), 409

            if bonus_practice:
                word = reciter.resolve_bonus_practice_word(
                    bonus_session_id,
                    word_id,
                    review_event_id,
                )
                if not word:
                    return jsonify({'error': '加练题目不属于当前会话，请重新获取随机加练'}), 409
            else:
                word = reciter.find_word(
                    task_item.get('word_key', '') if task_item else word_id,
                    include_mastered=True,
                )
            if word in reciter.mastered_words and not (bonus_practice or task_item or remedial):
                word = None

            if not word:
                return jsonify({'error': '单词未找到'}), 404

            exercise_type = (
                str(task_item.get('exercise_type') or 'spelling')
                if task_item
                else requested_exercise_type
            )
            if exercise_type == 'listening' and not audio_available:
                exercise_type = 'spelling'
            if exercise_type not in EXERCISE_TYPES:
                exercise_type = 'spelling'
            if (
                task_item
                and exercise_type not in gaokao_questions.QUESTION_TYPES
                and reciter.word_state_key(word_id) != reciter.word_state_key(word)
            ):
                return jsonify({'error': '当前任务单词已更新，请刷新后继续'}), 409
            if exercise_type == 'listening':
                test_inflection = False

            answer_feedback = None
            if exercise_type in gaokao_questions.QUESTION_TYPES:
                if not task_item or not question_id or not selected_option_id:
                    return jsonify({'error': '选择题作答参数不完整，请重新加载题目'}), 400
                question = gaokao_questions.get_question(word.english, exercise_type)
                bound_question_id = str(task_item.get('question_id') or '')
                if (
                    not question
                    or question.get('question_id') != question_id
                    or bound_question_id != question_id
                ):
                    return jsonify({'error': '题目版本已更新，请重新加载题目'}), 409
                valid_option_ids = {
                    str(option.get('id') or '')
                    for option in question.get('options') or []
                    if isinstance(option, dict)
                }
                if selected_option_id not in valid_option_ids:
                    return jsonify({'error': '所选答案无效，请重新选择'}), 400
                is_correct = gaokao_questions.check_answer(question, selected_option_id)
                submission_fingerprint = hashlib.sha256(
                    f'{question_id}\0{selected_option_id}'.encode('utf-8')
                ).hexdigest()
                test_inflection = False
            else:
                if not answer:
                    return jsonify({'error': '答案不能为空'}), 400
                submitted = answer.strip().lower()
                lemma = word.english.strip().lower()
                if test_inflection:
                    csv_row = lookup_csv_word(word.english)
                    if csv_row:
                        picked = pick_example_for_word(csv_row, word.english)
                        eff = (picked.get('example_form') or '').strip().lower()
                        expected = eff if eff else lemma
                    else:
                        expected = lemma
                    is_correct = submitted == expected
                else:
                    is_correct = submitted == lemma
                submission_fingerprint = hashlib.sha256(
                    submitted.encode('utf-8')
                ).hexdigest()
            applied = reciter.apply_scored_review_attempt(
                word,
                exercise_type=exercise_type,
                correct=is_correct,
                task_item=task_item,
                event_id=review_event_id,
                elapsed_ms=elapsed_ms,
                hint_count=hint_count,
                attempt_number=attempt_number,
                remedial=remedial,
                bonus_practice=bonus_practice,
                audio_available=audio_available,
                submission_fingerprint=submission_fingerprint,
            )
            if task_item and applied['recorded'] and not audio_available:
                task_item['exercise_type'] = exercise_type
            if bonus_practice and is_correct:
                if not reciter.complete_bonus_practice_word(
                    bonus_session_id,
                    word_id,
                    review_event_id,
                ):
                    return jsonify({'error': '加练题目已完成，请重新获取随机加练'}), 409
            reciter.save_learning_data(backup=False)

            recorded = applied['recorded']
            message = applied['message']
            mastered_now = applied['mastered_now']
            if exercise_type in gaokao_questions.QUESTION_TYPES:
                if not is_correct and not applied['final_attempt']:
                    message = '答案不正确'
                if is_correct or applied['final_attempt']:
                    answer_feedback = gaokao_questions.answer_explanation(question)
            gam_payload = None
            if is_correct and (recorded or review_event_id):
                pkw, pkm = _pk_stats_for_gamification(username)
                gam_payload = gamification_mod.award_correct_answer(
                    DATA_DIR,
                    username,
                    bonus_practice=bonus_practice,
                    remedial=applied['remedial'],
                    old_success_count=applied['old_success_count'],
                    new_success_count=applied['new_success_count'],
                    mastered_now=mastered_now,
                    mastered_words=len(reciter.mastered_words),
                    pk_wins=pkw,
                    pk_matches=pkm,
                    event_id=hashlib.sha256(
                        f'{reciter.word_state_key(word)}\0{review_event_id}'.encode('utf-8')
                    ).hexdigest(),
                    event_scope=reciter.word_state_key(word),
                )

            body = {
                'correct': is_correct,
                'message': message,
                'recorded': recorded,
                'mastered_now': mastered_now,
                'remedial': applied['remedial'],
                'attempt_number': applied['attempt_number'],
                'attempt_limit': applied['attempt_limit'],
                'final_attempt': applied['final_attempt'],
                'task_remedial': bool(
                    task_item
                    and (
                        task_item.get('phase') == 'remedial'
                        or reciter.task_attempt_count(task_item) >= applied['attempt_limit']
                    )
                ),
                'exercise_type': exercise_type,
                'task_progress': reciter.daily_task_progress(),
                'word': {
                    'english': word.english,
                    'chinese': word.chinese,
                    'success_count': word.success_count,
                    'next_review_date': word.next_review_date.isoformat(),
                    **reciter.review_state_payload(word),
                }
            }
            if gam_payload is not None:
                body['gamification'] = gam_payload
            if answer_feedback is not None:
                body['answer_feedback'] = answer_feedback
            if is_correct:
                extra_rows = other_v2_sense_extra_for_review_slot(word.english)
                if extra_rows:
                    body['other_senses_extra'] = extra_rows
            return jsonify(body), 200
    except ReviewEventConflict:
        return jsonify({'error': '作答事件与原请求不一致，请重新提交'}), 409
    except Exception as e:
        _invalidate_user_reciter_cache(username)
        logger.error(f"练习单词失败: {e}")
        return jsonify({'error': '服务器内部错误'}), 500

@app.route('/api/words/speak', methods=['POST'])
@token_required
@parent_forbidden
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


@app.route('/api/tts/capabilities')
def tts_capabilities():
    """前端是否可优先使用 Piper：需设置 PIPER_MODEL 且 piper 在 PATH 中。"""
    if piper_runtime_ready is None:
        return jsonify({'piper': False}), 200
    return jsonify({'piper': bool(piper_runtime_ready())}), 200


@app.route('/api/words/speak-audio', methods=['POST'])
@token_required
@parent_forbidden
def speak_text_audio(username):
    """使用 Piper 合成英文 WAV 并返回，供浏览器播放（远程可用）。"""
    if not _rate_allow(f"tts_audio:{username}", _RATE_MAX_TTS_AUDIO):
        return jsonify({'error': '朗读请求过于频繁，请稍后再试'}), 429
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

        if piper_runtime_ready is None or not piper_runtime_ready():
            return jsonify({'error': 'Piper 未配置'}), 503

        if piper_synthesize_wav is None:
            return jsonify({'error': 'Piper 不可用'}), 503

        wav = piper_synthesize_wav(safe_text)
        if not wav:
            return jsonify({'error': '语音合成失败'}), 503

        return Response(wav, mimetype='audio/wav')
    except Exception as e:
        logger.error(f"Piper 朗读失败: {e}")
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
    if not _rate_allow(f"import_json:{username}", _RATE_MAX_IMPORT_JSON):
        return jsonify({'error': '导入过于频繁，请稍后再试'}), 429
    items, err = _parse_import_json_body(request)
    if err:
        return jsonify({'error': err}), 400
    if not items:
        return jsonify({'error': '单词列表为空'}), 400
    if len(items) > 5000:
        return jsonify({'error': '单次最多导入 5000 条'}), 400
    norm_items: List[dict] = []
    for it in items:
        if not isinstance(it, dict):
            norm_items.append(it)
            continue
        row = dict(it)
        en = str(row.get("english", "")).strip()
        if en:
            row["english"] = _normalize_import_english_surface(en)[:500]
        norm_items.append(row)
    try:
        with user_reciter_session(username) as reciter:
            result = reciter.add_words_from_dicts(norm_items)
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
    return jsonify({
        'plan': get_user_plan(username),
        'article_ai_extract_available': bool(get_deepseek_api_key()),
        'article_ai_extract_enabled': _article_ai_extract_enabled(),
    }), 200


def _textbooks_load_index() -> dict:
    if not TEXTBOOKS_INDEX_PATH.is_file():
        return {"schema": "", "corpora": []}
    try:
        with open(TEXTBOOKS_INDEX_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("读取 textbooks/index.json 失败: %s", e)
        return {"schema": "", "corpora": []}


def _corpus_root_from_manifest_relative(manifest_rel: str) -> Optional[Path]:
    rel = manifest_rel.strip().replace("\\", "/")
    if not rel or ".." in rel or rel.startswith("/"):
        return None
    full = (STATIC_WB_DIR / rel).resolve()
    root = STATIC_WB_DIR.resolve()
    if not str(full).startswith(str(root)):
        return None
    if not full.is_file():
        return None
    return full.parent


# 课文学习：普通用户每册仅可打开前 N 篇（与前端列表一致）；VIP（paid）不限
TEXTBOOK_FREE_UNITS_PER_BOOK = 10


def _textbooks_unit_index_for_path(manifest: dict, rel_path: str) -> Optional[int]:
    """在 manifest 的 books[].units 中查找 json 路径，返回该册内 units 的下标；未找到返回 None。"""
    rel_norm = rel_path.strip().replace("\\", "/")
    for b in manifest.get("books") or []:
        units = b.get("units") or []
        for ui, u in enumerate(units):
            jp = str(u.get("json", "")).strip().replace("\\", "/")
            if jp == rel_norm:
                return ui
    return None


def _textbooks_resolve_lesson_file(corpus_root: Path, rel_path: str) -> Optional[Path]:
    rel = rel_path.strip().replace("\\", "/")
    if not rel or ".." in rel or rel.startswith("/"):
        return None
    full = (corpus_root / rel).resolve()
    if not str(full).startswith(str(corpus_root.resolve())):
        return None
    if not full.is_file() or full.suffix.lower() != ".json":
        return None
    return full


@app.route('/api/textbooks/catalog', methods=['GET'])
@token_required
def textbooks_catalog(username):
    """课文学习：返回教材索引及各套 manifest（如 nce/manifest.json）。"""
    idx = _textbooks_load_index()
    corpora_out: List[dict] = []
    for c in idx.get("corpora") or []:
        cid = str(c.get("id", "")).strip()
        title = str(c.get("title", "")).strip()
        manifest_rel = str(c.get("manifestRelativePath", "")).strip()
        if not cid or not manifest_rel:
            continue
        mp = (STATIC_WB_DIR / manifest_rel).resolve()
        if not str(mp).startswith(str(STATIC_WB_DIR.resolve())) or not mp.is_file():
            continue
        try:
            with open(mp, "r", encoding="utf-8") as f:
                mdata = json.load(f)
        except Exception as e:
            logger.warning("读取教材 manifest %s 失败: %s", manifest_rel, e)
            mdata = {}
        corpora_out.append(
            {
                "id": cid,
                "title": title or cid,
                "manifest": mdata,
            }
        )
    return jsonify({"schema": idx.get("schema"), "corpora": corpora_out}), 200


@app.route('/api/textbooks/lesson', methods=['GET'])
@token_required
def textbooks_lesson(username):
    """课文学习：按 corpus id + 相对于该教材根目录的 json 路径返回课文。"""
    corpus_id = request.args.get("corpus", "").strip()
    rel_path = request.args.get("path", "").strip()
    if not corpus_id or not rel_path:
        return jsonify({"error": "缺少 corpus 或 path 参数"}), 400

    idx = _textbooks_load_index()
    corpus_root: Optional[Path] = None
    manifest_rel: str = ""
    for c in idx.get("corpora") or []:
        if str(c.get("id", "")).strip() != corpus_id:
            continue
        manifest_rel = str(c.get("manifestRelativePath", "")).strip()
        if manifest_rel:
            corpus_root = _corpus_root_from_manifest_relative(manifest_rel)
        break

    if corpus_root is None:
        return jsonify({"error": "无效的教材"}), 400

    lesson_path = _textbooks_resolve_lesson_file(corpus_root, rel_path)
    if lesson_path is None:
        return jsonify({"error": "无效的课文路径"}), 400

    if not is_paid_user(username) and manifest_rel:
        mp = (STATIC_WB_DIR / manifest_rel).resolve()
        if str(mp).startswith(str(STATIC_WB_DIR.resolve())) and mp.is_file():
            try:
                with open(mp, "r", encoding="utf-8") as f:
                    manifest_data = json.load(f)
                uidx = _textbooks_unit_index_for_path(manifest_data, rel_path)
                if uidx is not None and uidx >= TEXTBOOK_FREE_UNITS_PER_BOOK:
                    return jsonify(
                        {
                            "error": f"普通用户每册仅可学习前 {TEXTBOOK_FREE_UNITS_PER_BOOK} 篇课文，升级 VIP 后可查看全部",
                        }
                    ), 403
            except Exception as e:
                logger.warning("课文权限校验读取 manifest 失败: %s", e)

    try:
        with open(lesson_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("读取课文 JSON 失败: %s", e)
        return jsonify({"error": "课文读取失败"}), 500

    return jsonify(data), 200


@app.route('/api/wordbank/csv', methods=['GET'])
@token_required
def get_wordbank_csv(username):
    """返回系统词库扁平行（与 ``merge_wordbank_rows_for_search`` 一致：v2 优先，同键覆盖 CSV）。

    Query:
    - level: 可选，按难度过滤
    - fields: ``full``（默认）或 ``minimal``（省略 example*_form 等，供单词学习等场景）
    支持 If-None-Match / ETag，内容未变时返回 304（ETag 含 words.csv 与 words_v2.json 的 mtime）。
    """
    level = request.args.get('level', '').strip()
    fields_mode = request.args.get('fields', 'full').strip().lower()
    if fields_mode not in ('full', 'minimal'):
        fields_mode = 'full'

    rows, _ = merge_wordbank_rows_for_search(level)
    count = len(rows)
    try:
        csv_mtime = WORDS_CSV_FILE.stat().st_mtime if WORDS_CSV_FILE.exists() else 0.0
    except OSError:
        csv_mtime = 0.0
    try:
        v2_mtime = wordbank_v2.WORDS_V2_FILE.stat().st_mtime if wordbank_v2.WORDS_V2_FILE.exists() else 0.0
    except OSError:
        v2_mtime = 0.0
    # ETag 必须为 ASCII；level 可能含中文（小学、初中等），不可直接拼进响应头
    _etag_seed = f"{csv_mtime:.9f}\0{v2_mtime:.9f}\0{level}\0{fields_mode}\0{count}".encode("utf-8")
    etag_digest = hashlib.sha256(_etag_seed).hexdigest()[:32]
    etag = f'W/"wbcsv-{etag_digest}"'
    inm = (request.headers.get('If-None-Match') or '').strip()
    if inm == etag:
        resp = Response(status=304)
        resp.headers['ETag'] = etag
        return resp

    if fields_mode == 'minimal':
        out_rows = [_wordbank_csv_row_minimal(r) for r in rows]
    else:
        out_rows = rows
    resp = jsonify({'words': out_rows, 'count': count})
    resp.headers['ETag'] = etag
    return resp, 200


def _dedupe_preserve_order(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _plural_stem_variants(term: str) -> List[str]:
    """启发式去复数：-ies→-y、-es、-s（非完备形态学；ss 结尾不剥 s）。"""
    if len(term) < 4 or not re.match(r'^[a-z]+$', term):
        return []
    stems: List[str] = []
    # flies→fly, cities→city（元音+ies 如 pies/dies 不处理）
    if (
        term.endswith('ies')
        and len(term) >= 5
        and term[-4] not in 'aeiou'
    ):
        stems.append(term[:-3] + 'y')
    elif term.endswith('es') and len(term) > 3:
        stem = term[:-2]
        if len(stem) >= 2:
            stems.append(stem)
    # 不以 -s 当英语复数：拉丁/希腊借词常以 -us 结尾（eucalyptus、cactus、bonus），剥 s 会得到错误词干
    if (
        term.endswith('s')
        and not term.endswith('ss')
        and not term.endswith('us')
        and len(term) > 3
    ):
        stem = term[:-1]
        if len(stem) >= 2:
            stems.append(stem)
    return _dedupe_preserve_order(stems)


def _past_tense_stem_variants(term: str) -> List[str]:
    """启发式由 -ed 过去式还原原形（双写辅音等；非完备形态学）。"""
    if len(term) < 5 or not re.match(r'^[a-z]+$', term):
        return []
    if not term.endswith('ed'):
        return []
    stem = term[:-2]
    if len(stem) < 3:
        return []
    if len(stem) >= 2 and stem[-1] == stem[-2] and stem[-1] not in 'aeiou':
        return [stem[:-1]]
    return [stem]


def _ing_stem_variants(term: str) -> List[str]:
    """启发式由 -ing 还原原形（双写辅音等；非完备形态学）。"""
    if len(term) < 5 or not re.match(r'^[a-z]+$', term):
        return []
    if not term.endswith('ing'):
        return []
    stem = term[:-3]
    if len(stem) < 2:
        return []
    if len(stem) >= 2 and stem[-1] == stem[-2] and stem[-1] not in 'aeiou':
        return [stem[:-1]]
    return [stem]


# 派生后缀：长优先，避免 information 被误拆成 informa+tion
_DERIV_SUFFIXES: Tuple[str, ...] = tuple(
    sorted(
        {
            'ification',
            'ization',
            'isation',
            'ness',
            'ation',
            'ition',
            'tion',
            'sion',
            'ment',
            'less',
            'able',
            'ible',
            'eous',
            'ious',
            'ous',
            'ive',
            'ful',
            'ity',
            'ify',
            'ize',
            'ise',
        },
        key=len,
        reverse=True,
    ),
)


def _extra_morph_stem_variants(term: str) -> List[str]:
    """
    在常规 contraction / 复数 / -ed / -ing 之后、spaCy 之前的快速词形推断：
    -ly 副词、比较级/最高级 -er/-est、-iest→-y，以及常见派生后缀剥离。
    """
    if len(term) < 4 or not re.match(r'^[a-z]+$', term):
        return []
    out: List[str] = []
    # 形容词/副词：happily → happy
    if term.endswith('ily') and len(term) >= 6:
        out.append(term[:-3] + 'y')
    # quickly → quick；likely/probably 等不拆（长度与 -abl 排除）
    if (
        term.endswith('ly')
        and not term.endswith('ily')
        and len(term) >= 7
    ):
        stem = term[:-2]
        if len(stem) >= 4 and not stem.endswith('abl'):
            out.append(stem)
    # happiest → happy
    if term.endswith('iest') and len(term) >= 6:
        stem = term[:-4] + 'y'
        if len(stem) >= 3:
            out.append(stem)
    # fastest → fast（词干至少 4 字母，减少 water→wat）
    if term.endswith('est') and len(term) >= 7:
        stem = term[:-3]
        if len(stem) >= 4:
            out.append(stem)
    # faster → fast（同上）
    if term.endswith('er') and not term.endswith('est') and len(term) >= 6:
        stem = term[:-2]
        if len(stem) >= 4:
            out.append(stem)
    for suf in _DERIV_SUFFIXES:
        if len(term) <= len(suf) + 2:
            continue
        if term.endswith(suf):
            stem = term[: -len(suf)]
            if len(stem) >= 3:
                out.append(stem)
    return _dedupe_preserve_order(out)


def _normalize_apostrophe_token(term: str) -> str:
    """统一弯引号为 ASCII 撇号，便于匹配 's / 've。"""
    return (
        term.replace('\u2019', "'")
        .replace('\u2018', "'")
        .replace('\u201b', "'")
        .lower()
    )


_spacy_nlp = None
_spacy_nlp_lock = threading.Lock()
_spacy_nlp_load_failed = False
_spacy_download_attempted = False


def _try_download_en_core_web_sm() -> bool:
    """在模型缺失时执行 `python -m spacy download en_core_web_sm`（需外网；多进程用文件锁）。"""
    lock_path = Path(tempfile.gettempdir()) / "english_reciter_spacy_en_sm_download.lock"
    try:
        import fcntl
    except ImportError:
        fcntl = None
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lf = open(lock_path, "a+")
        try:
            if fcntl is not None:
                fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            logger.info("正在下载 spaCy 模型 en_core_web_sm（首次或缺失时）…")
            subprocess.run(
                [sys.executable, "-m", "spacy", "download", "en_core_web_sm"],
                check=True,
                timeout=600,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
            return True
        finally:
            if fcntl is not None:
                try:
                    fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            lf.close()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, FileNotFoundError) as e:
        logger.warning("spacy download en_core_web_sm 失败: %s", e)
        return False


def _wordbank_lemma_spacy_enabled() -> bool:
    """config.json `wordbank_lemma_spacy` 或环境变量 WORDBANK_LEMMA_SPACY（0/false 关闭）。"""
    v = os.getenv("WORDBANK_LEMMA_SPACY", "").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    if v in ("1", "true", "yes", "on"):
        return True
    return bool(_load_app_config().get("wordbank_lemma_spacy", True))


def _get_spacy_nlp():
    """懒加载 en_core_web_sm；缺失时尝试 spacy download 一次；再失败则仅用启发式。"""
    global _spacy_nlp, _spacy_nlp_load_failed, _spacy_download_attempted
    if spacy is None or _spacy_nlp_load_failed:
        return None
    if _spacy_nlp is not None:
        return _spacy_nlp
    with _spacy_nlp_lock:
        if _spacy_nlp_load_failed:
            return None
        if _spacy_nlp is not None:
            return _spacy_nlp
        try:
            # 句法分析与 NER 不参与英语 lemma；禁用可明显加速（尤其批量分词）
            _spacy_nlp = spacy.load("en_core_web_sm", disable=["parser", "ner"])
            return _spacy_nlp
        except OSError:
            pass
        except Exception as e:
            logger.warning("spaCy load en_core_web_sm 异常，将尝试下载或降级: %s", e)

        if not _spacy_download_attempted:
            _spacy_download_attempted = True
            if _try_download_en_core_web_sm():
                try:
                    _spacy_nlp = spacy.load("en_core_web_sm", disable=["parser", "ner"])
                    return _spacy_nlp
                except Exception as e:
                    logger.warning(
                        "下载后仍无法加载 en_core_web_sm: %s。"
                        "可手动执行: %s -m spacy download en_core_web_sm",
                        e,
                        sys.executable,
                    )
            else:
                logger.warning(
                    "无法安装 en_core_web_sm，词形还原将使用启发式。"
                    "请在同一 venv 执行: pip install -r requirements-simple.txt "
                    "或 %s -m spacy download en_core_web_sm",
                    sys.executable,
                )
        _spacy_nlp_load_failed = True
        return None


def _spacy_lemma_for_surface(surface: str) -> Optional[str]:
    """单 token 英文表面形 → lemma（小写）；无法处理则返回 None。"""
    if not _wordbank_lemma_spacy_enabled():
        return None
    nlp = _get_spacy_nlp()
    if nlp is None:
        return None
    if not re.match(r"^[a-z][a-z'\-]*$", surface):
        return None
    doc = nlp(surface)
    if len(doc) != 1:
        return None
    lem = doc[0].lemma_.lower()
    if not re.match(r"^[a-z][a-z'\-]*$", lem):
        return None
    return lem


def _regular_plural_surfaces_from_lemma(lemma: str) -> set:
    """
    由名词原形生成「简单复数」常见拼写形式（小写），用于与表面形比对。
    不含不规则复数（feet、children 等）；与 spaCy lemma 搭配使用。
    """
    if not lemma or len(lemma) < 2:
        return set()
    if not re.match(r"^[a-z][a-z'\-]*$", lemma):
        return set()
    L = lemma
    cand: set = set()
    if L.endswith("y") and len(L) >= 2 and L[-2] not in "aeiou":
        cand.add(L[:-1] + "ies")
    elif L.endswith("y"):
        cand.add(L + "s")
    if L.endswith("fe"):
        cand.add(L[:-2] + "ves")
    elif L.endswith("f") and not L.endswith("ff"):
        cand.add(L[:-1] + "ves")
    if L.endswith(("s", "x", "z")):
        cand.add(L + "es")
    elif L.endswith("ch") or L.endswith("sh"):
        cand.add(L + "es")
    elif L.endswith("o"):
        cand.add(L + "es")
        cand.add(L + "s")
    else:
        cand.add(L + "s")
    return cand


def _normalize_import_english_surface(surface: str) -> str:
    """
    导入用词：若为名词且 spaCy 标为复数，且表面形属于该原形的「简单复数」拼写，则改为原形。
    不规则复数（feet、children 等）保持表面形；非名词、单数、spaCy 不可用时保持原样。
    管理员映射应在调用本函数之前处理，使映射目标不被本函数改写。
    """
    surf = _normalize_apostrophe_token(surface.strip().lower())
    if not re.match(r"^[a-z][a-z'\-]*$", surf):
        return surf
    if not _wordbank_lemma_spacy_enabled():
        return surf
    nlp = _get_spacy_nlp()
    if nlp is None:
        return surf
    doc = nlp(surf)
    if len(doc) != 1:
        return surf
    tok = doc[0]
    if tok.pos_ != "NOUN":
        return surf
    if tok.morph.get("Number") != "Plur":
        return surf
    lem = tok.lemma_.lower()
    if lem == surf:
        return surf
    if not re.match(r"^[a-z][a-z'\-]*$", lem):
        return surf
    if surf in _regular_plural_surfaces_from_lemma(lem):
        return lem
    return surf


# 课文导入等场景对大量词逐次 nlp(surface) 极慢；改为分批拼接后单次 nlp。
_SPACY_LEMMA_BATCH_CHUNK = 400


def _spacy_lemma_map_for_surfaces(surfaces: List[str]) -> Dict[str, str]:
    """
    批量得到 surface -> spaCy lemma，避免对每个词单独 nlp()。
    若词形在合并文本中分词结果与 surface 不一致，该词不在 map 中，调用方可回退 _spacy_lemma_for_surface。
    """
    if not _wordbank_lemma_spacy_enabled():
        return {}
    nlp = _get_spacy_nlp()
    if nlp is None:
        return {}
    ordered = list(
        dict.fromkeys(
            s for s in surfaces
            if isinstance(s, str) and re.match(r"^[a-z][a-z'\-]*$", s)
        ),
    )
    if not ordered:
        return {}
    out: Dict[str, str] = {}
    for i in range(0, len(ordered), _SPACY_LEMMA_BATCH_CHUNK):
        chunk = ordered[i : i + _SPACY_LEMMA_BATCH_CHUNK]
        doc = nlp(" ".join(chunk))
        for token in doc:
            if not token.is_alpha:
                continue
            surf = _normalize_apostrophe_token(token.text.lower())
            lem = token.lemma_.lower()
            if len(lem) < 2:
                continue
            if not re.match(r"^[a-z][a-z'\-]*$", lem):
                continue
            if surf not in out:
                out[surf] = lem
    return out


def spacy_extract_lemmas_from_article(text: str) -> Optional[List[str]]:
    """用 spaCy 对全文分词，取各 token 的 lemma，去重保序（用于 VIP 课文导入「spaCy 分词」）。"""
    if spacy is None:
        return None
    nlp = _get_spacy_nlp()
    if nlp is None:
        return None
    doc = nlp(text)
    out: List[str] = []
    seen = set()
    for token in doc:
        if not token.is_alpha or token.is_stop:
            continue
        lem = token.lemma_.lower()
        if len(lem) < 2:
            continue
        if not re.match(r"^[a-z][a-z'\-]*$", lem):
            continue
        if lem not in seen:
            seen.add(lem)
            out.append(lem)
    return out


def spacy_extract_surfaces_from_article(text: str) -> Optional[List[str]]:
    """用 spaCy 对全文分词，取各 token 的表面形（小写），去重保序；停用词与词形过滤与 lemma 版一致。"""
    if spacy is None:
        return None
    nlp = _get_spacy_nlp()
    if nlp is None:
        return None
    doc = nlp(text)
    out: List[str] = []
    seen = set()
    for token in doc:
        if not token.is_alpha or token.is_stop:
            continue
        surf = _normalize_apostrophe_token(token.text.lower())
        if len(surf) < 2:
            continue
        if not re.match(r"^[a-z][a-z'\-]*$", surf):
            continue
        if surf not in seen:
            seen.add(surf)
            out.append(surf)
    return out


def _contraction_stem_variants(term: str) -> List[str]:
    """启发式：'s（所有格 / is、has 等）与 've（have）。"""
    t = _normalize_apostrophe_token(term)
    if not re.match(r"^[a-z']+$", t):
        return []
    out: List[str] = []
    if t.endswith("'ve") and len(t) >= 4:
        stem = t[:-3]
        if len(stem) >= 1:
            out.append(stem)
    if t.endswith("'s") and len(t) > 3:
        stem = t[:-2]
        if len(stem) >= 1:
            out.append(stem)
    return _dedupe_preserve_order(out)


def _iter_csv_lemma_candidates(
    surface: str, mappings: dict, use_spacy: bool = True,
    spacy_lemma_map: Optional[Dict[str, str]] = None,
    use_heuristics: bool = True,
):
    """按优先级产出 (候选原形, 类别)。

    - use_heuristics=False：仅管理员映射、表面形直配与 spaCy（导入单词等场景）。
    - use_heuristics=True：另含缩写/复数/-ed/-ing/派生等快速启发式（课文悬停释义默认用）。
    - use_spacy=False 时不调用 spaCy（课文 nlp=0 快速路径）。
    """
    if surface in mappings:
        yield mappings[surface], 'admin'
        return
    seen = set()
    if surface not in seen:
        seen.add(surface)
        yield surface, 'surface'
    if use_heuristics:
        for stem in _contraction_stem_variants(surface):
            x = mappings.get(stem, stem)
            if x not in seen:
                seen.add(x)
                yield x, 'contraction'
        for stem in _plural_stem_variants(surface):
            x = mappings.get(stem, stem)
            if x not in seen:
                seen.add(x)
                yield x, 'plural'
        for stem in _past_tense_stem_variants(surface):
            x = mappings.get(stem, stem)
            if x not in seen:
                seen.add(x)
                yield x, 'past'
        for stem in _ing_stem_variants(surface):
            x = mappings.get(stem, stem)
            if x not in seen:
                seen.add(x)
                yield x, 'ing'
        for stem in _extra_morph_stem_variants(surface):
            x = mappings.get(stem, stem)
            if x not in seen:
                seen.add(x)
                yield x, 'suffix'
    if use_spacy:
        lem: Optional[str] = None
        if spacy_lemma_map is not None:
            lem = spacy_lemma_map.get(surface)
            if lem is None:
                lem = spacy_lemma_map.get(_normalize_apostrophe_token(surface))
        if lem is None:
            lem = _spacy_lemma_for_surface(surface)
        if lem and lem != surface:
            x = mappings.get(lem, lem)
            if x not in seen:
                seen.add(x)
                yield x, 'lemma_nlp'


def _first_lemma_in_csv_with_kind(
    surface: str, mappings: dict, csv_keys: set, use_spacy: bool = True,
    spacy_lemma_map: Optional[Dict[str, str]] = None,
    use_heuristics: bool = True,
) -> Tuple[Optional[str], Optional[str]]:
    for c, kind in _iter_csv_lemma_candidates(
        surface, mappings, use_spacy, spacy_lemma_map, use_heuristics,
    ):
        ck = wordbank_v2.normalize_english_key(c)
        if ck in csv_keys:
            return ck, kind
    return None, None


def _first_lemma_in_csv(
    surface: str, mappings: dict, csv_keys: set, use_spacy: bool = True,
    spacy_lemma_map: Optional[Dict[str, str]] = None,
    use_heuristics: bool = True,
) -> Optional[str]:
    h, _ = _first_lemma_in_csv_with_kind(
        surface, mappings, csv_keys, use_spacy, spacy_lemma_map, use_heuristics,
    )
    return h


def _lemma_for_vocab_not_in_csv(
    surface: str, mappings: dict, use_heuristics: bool = True,
) -> str:
    """词库无该词时，用于生成/排队的目标 lemma（启发式与 spaCy 顺序见 use_heuristics）。"""
    if surface in mappings:
        return mappings[surface]
    if use_heuristics:
        cov = _contraction_stem_variants(surface)
        if cov:
            return mappings.get(cov[0], cov[0])
        stems = _plural_stem_variants(surface)
        if stems:
            return mappings.get(stems[0], stems[0])
        pst = _past_tense_stem_variants(surface)
        if pst:
            return mappings.get(pst[0], pst[0])
        ing = _ing_stem_variants(surface)
        if ing:
            return mappings.get(ing[0], ing[0])
        for stem in _extra_morph_stem_variants(surface):
            return mappings.get(stem, stem)
    lem = _spacy_lemma_for_surface(surface)
    if lem and lem != surface:
        return mappings.get(lem, lem)
    return surface


def _vocab_import_spacy_accepts_phrase_surface(s: str) -> bool:
    """
    多词英文词组（空格分隔）：字符集与单段一致；开启 spaCy 时逐词须能通过单段 lemma 校验。
    """
    if not re.match(r"^[a-z][a-z'\-]*(?: [a-z][a-z'\-]*)+$", s):
        return False
    if not _wordbank_lemma_spacy_enabled():
        return True
    nlp = _get_spacy_nlp()
    if nlp is None:
        return True
    doc = nlp(s)
    n_words = 0
    for tok in doc:
        if tok.is_space:
            continue
        if tok.is_punct:
            continue
        t = tok.text.lower()
        if not t:
            continue
        n_words += 1
        if _spacy_lemma_for_surface(t) is None:
            return False
    return n_words >= 2


def _vocab_import_spacy_accepts_surface(
    surface: str,
    mappings: dict,
    lemma_map: Optional[Dict[str, str]],
) -> bool:
    """
    VIP 词汇导入：用 spaCy 原型校验词形是否可识别；管理员映射的词直接通过。
    支持空格分隔的多词词组（如 anything else）；关闭 wordbank_lemma_spacy 或模型不可用时放宽。
    """
    s = " ".join(str(surface or "").strip().lower().split())
    if not s or len(s) < 2:
        return False
    if s in mappings:
        return True
    if re.match(r"^[a-z][a-z'\-]*$", s):
        if not _wordbank_lemma_spacy_enabled():
            return True
        if _get_spacy_nlp() is None:
            return True
        if lemma_map:
            lem = lemma_map.get(s) or lemma_map.get(_normalize_apostrophe_token(s))
            if lem is not None:
                return True
        return _spacy_lemma_for_surface(s) is not None
    if " " in s:
        return _vocab_import_spacy_accepts_phrase_surface(s)
    return False


@app.route('/api/wordbank/csv/search', methods=['GET'])
@token_required
def search_wordbank_csv(username):
    """在 CSV 词汇表中搜索（支持英文/中文，逗号分隔多词）。

    - ``heuristics=1``：启用缩写/复数/-ed/-ing/派生等快速规则。
    - ``heuristics=0``：关闭（仅表面形、管理员映射与 spaCy）。
    - 未指定 ``heuristics`` 时：``per_surface=1``（课文学习逐词）默认开启启发式；否则默认关闭（导入页词库搜索）。
    - ``surface_first=1``（导入页「从词库搜索」）：英文词先仅用表面形与管理员映射匹配，未命中再启用 spaCy 原型匹配。
    """
    if not _rate_allow(f"wordbank_search:{username}", _RATE_MAX_WORDBANK_SEARCH):
        return jsonify({'error': '词库搜索过于频繁，请稍后再试'}), 429
    q = request.args.get('q', '').strip()
    level = request.args.get('level', '').strip()
    per_surface = request.args.get('per_surface', '').strip().lower() in ('1', 'true', 'yes')
    surface_first = request.args.get('surface_first', '').strip().lower() in (
        '1', 'true', 'yes', 'on',
    )
    # nlp=0：无 spaCy；启发式由 heuristics 控制
    use_spacy = request.args.get('nlp', '1').strip().lower() not in ('0', 'false', 'no', 'off')
    raw_heur = (request.args.get('heuristics') or '').strip().lower()
    if raw_heur in ('1', 'true', 'yes', 'on'):
        use_heuristics = True
    elif raw_heur in ('0', 'false', 'no', 'off'):
        use_heuristics = False
    else:
        # 未传参：课文学习（per_surface 逐词）默认开启启发式；导入页词库搜索无 per_surface，默认关
        use_heuristics = per_surface
    if not q:
        return jsonify({
            'words': [],
            'count': 0,
            'lemma_resolution': {},
            'implicit_lemma_nlp_resolution': {},
            'implicit_plural_resolution': {},
            'implicit_past_resolution': {},
            'implicit_ing_resolution': {},
            'implicit_contraction_resolution': {},
            'implicit_suffix_resolution': {},
            'surface_hits': {},
            'surface_blocked': {},
            'nlp_enabled': use_spacy,
            'heuristics_enabled': use_heuristics,
            'surface_first': surface_first,
        }), 200
    if len(q) > 2000:
        return jsonify({'error': '搜索内容过长，请减少后重试'}), 400
    terms = _dedupe_preserve_order([
        _normalize_apostrophe_token(t.strip())
        for t in re.split(r'[,，]', q) if t.strip()
    ])
    if len(terms) > 64:
        return jsonify({'error': '单次最多搜索 64 个词'}), 400
    mappings = get_wordbank_lemma_mappings()
    rows, csv_row_keys = merge_wordbank_rows_for_search(level)
    lemma_resolution: Dict[str, str] = {}
    implicit_lemma_nlp_resolution: Dict[str, str] = {}
    implicit_plural_resolution: Dict[str, str] = {}
    implicit_past_resolution: Dict[str, str] = {}
    implicit_ing_resolution: Dict[str, str] = {}
    implicit_contraction_resolution: Dict[str, str] = {}
    implicit_suffix_resolution: Dict[str, str] = {}
    surface_hits: Dict[str, Optional[dict]] = {}
    surface_blocked: Dict[str, bool] = {}
    difficult: Dict[str, object] = {}
    if per_surface:
        with _TROUBLES_LOCK:
            difficult = dict(_read_troubles_unlocked().get('difficult') or {})
    spacy_lemma_map: Optional[Dict[str, str]] = None
    if use_spacy:
        batch_terms = [t for t in terms if re.match(r'[a-z]', t)]
        if batch_terms:
            spacy_lemma_map = _spacy_lemma_map_for_surfaces(batch_terms)
            if not spacy_lemma_map:
                spacy_lemma_map = None

    def _resolve_term_hit(t: str) -> Tuple[Optional[str], Optional[str]]:
        """解析英文检索词在词库中的命中原形；surface_first 时先表面形与映射，未命中再 spaCy。"""
        if surface_first:
            h1, k1 = _first_lemma_in_csv_with_kind(
                t, mappings, csv_row_keys, False, None, use_heuristics,
            )
            if h1 is not None or not use_spacy:
                return h1, k1
            return _first_lemma_in_csv_with_kind(
                t, mappings, csv_row_keys, True, spacy_lemma_map, use_heuristics,
            )
        return _first_lemma_in_csv_with_kind(
            t, mappings, csv_row_keys, use_spacy, spacy_lemma_map, use_heuristics,
        )

    term_hits: Dict[str, Optional[str]] = {}
    for term in terms:
        if re.match(r'[a-z]', term):
            hit, kind = _resolve_term_hit(term)
            term_hits[term] = hit
            if per_surface and term not in surface_hits:
                if hit is None:
                    surface_hits[term] = None
                    surface_blocked[term] = term in difficult
                else:
                    surface_hits[term] = lookup_csv_word(hit)
                    surface_blocked[term] = False
            if hit is not None and hit != term:
                lemma_resolution[term] = hit
                if term not in mappings:
                    if kind == 'lemma_nlp':
                        implicit_lemma_nlp_resolution[term] = hit
                    elif kind == 'plural':
                        implicit_plural_resolution[term] = hit
                    elif kind == 'past':
                        implicit_past_resolution[term] = hit
                    elif kind == 'ing':
                        implicit_ing_resolution[term] = hit
                    elif kind == 'contraction':
                        implicit_contraction_resolution[term] = hit
                    elif kind == 'suffix':
                        implicit_suffix_resolution[term] = hit
    _nk = wordbank_v2.normalize_english_key
    english_target_keys: Set[str] = set()
    chinese_terms: List[str] = []
    for term in terms:
        if re.match(r'[a-z]', term):
            if surface_first:
                hit = term_hits.get(term)
                if hit is not None:
                    english_target_keys.add(_nk(hit))
                continue
            for candidate, _kind in _iter_csv_lemma_candidates(
                term, mappings, use_spacy, spacy_lemma_map, use_heuristics,
            ):
                english_target_keys.add(_nk(candidate))
        else:
            chinese_terms.append(term)

    result = []
    seen = set()
    for row in rows:
        en = _nk(row.get("english", ""))
        zh = row.get("chinese", "")
        matched = en in english_target_keys or any(term in zh for term in chinese_terms)
        if matched and en not in seen:
            seen.add(en)
            result.append(row)
    out: Dict[str, object] = {
        'words': result,
        'count': len(result),
        'lemma_resolution': lemma_resolution,
        'implicit_lemma_nlp_resolution': implicit_lemma_nlp_resolution,
        'implicit_plural_resolution': implicit_plural_resolution,
        'implicit_past_resolution': implicit_past_resolution,
        'implicit_ing_resolution': implicit_ing_resolution,
        'implicit_contraction_resolution': implicit_contraction_resolution,
        'implicit_suffix_resolution': implicit_suffix_resolution,
    }
    if per_surface:
        out['surface_hits'] = surface_hits
        out['surface_blocked'] = surface_blocked
    out['nlp_enabled'] = use_spacy
    out['heuristics_enabled'] = use_heuristics
    out['surface_first'] = surface_first
    return jsonify(out), 200


@app.route('/api/words/import-from-article', methods=['POST'])
@token_required
def import_from_article(username):
    """
    从文章文本提取单词，返回匹配词条列表（不直接加入待复习）：
    - 免费版：按空格分词后去查 CSV；可勾选 use_spacy 在匹配步用 spaCy
    - VIP：默认 extract_mode=spacy；extract_mode=ai 仅当管理后台开启且请求携带有效管理员 token
    前端拿到词条列表后注入选框，让用户确认后再加入待复习。
    """
    if not _rate_allow(f"article_import:{username}", _RATE_MAX_ARTICLE_IMPORT):
        return jsonify({'error': '文章提取过于频繁，请稍后再试'}), 429
    data = request.get_json(silent=True) or {}
    text = str(data.get('text', '')).strip()
    if not text:
        return jsonify({'error': '文章内容不能为空'}), 400
    if len(text) > 20000:
        return jsonify({'error': '文章内容过长（最多20000字符）'}), 400

    plan = get_user_plan(username)
    extract_mode = ''
    if plan == 'paid':
        has_ds = bool(get_deepseek_api_key())
        raw_mode = str(data.get('extract_mode', '') or '').strip().lower()
        if raw_mode in ('ai', 'deepseek'):
            admin_tok = _admin_token_from_request()
            if not _article_ai_extract_enabled():
                return jsonify({
                    'error': '未在管理后台开启「AI 文章分词」，VIP 默认使用 spaCy 分词',
                }), 403
            if not has_ds:
                return jsonify({
                    'error': '未配置 DeepSeek API，无法使用 AI 分词',
                }), 400
            if not verify_admin_token(admin_tok):
                return jsonify({
                    'error': 'AI 分词仅限管理员：请先登录管理后台并保持会话有效',
                }), 403
            extract_mode = 'ai'
        else:
            extract_mode = 'spacy'
        if extract_mode == 'spacy':
            lemmas = spacy_extract_lemmas_from_article(text)
            if lemmas is None:
                return jsonify({
                    'error': 'spaCy 模型未就绪，请安装 en_core_web_sm 或稍后重试',
                }), 500
            method = 'spacy'
        else:
            lemmas = deepseek_extract_lemmas(text)
            if lemmas is None:
                return jsonify({'error': 'DeepSeek API 调用失败，请稍后重试'}), 500
            method = 'deepseek'
        use_spacy = True
    else:
        raw_us = data.get('use_spacy', False)
        if isinstance(raw_us, str):
            use_spacy = raw_us.strip().lower() in ('1', 'true', 'yes', 'on')
        else:
            use_spacy = bool(raw_us)
        raw_words = re.findall(r"[a-zA-Z']+", text)
        lemmas = list({w.lower().strip("'") for w in raw_words if len(w) >= 2})
        method = 'simple'

    csv_set = get_wordbank_english_set()
    mappings = get_wordbank_lemma_mappings()
    unique_lemmas = list(dict.fromkeys(lemmas))
    # VIP「spaCy 分词」已在全文 nlp 中得到 lemma，匹配阶段无需再逐词 spaCy（否则双倍耗时）
    use_spacy_match = use_spacy
    if plan == 'paid' and extract_mode == 'spacy':
        use_spacy_match = False
    spacy_lemma_map: Optional[Dict[str, str]] = None
    if use_spacy_match:
        spacy_lemma_map = _spacy_lemma_map_for_surfaces(unique_lemmas)
    unmatched_lemmas: List[str] = []
    matched_effective: List[str] = []
    seen_eff = set()
    matched_surface_count = 0
    for w in unique_lemmas:
        eff = _first_lemma_in_csv(
            w, mappings, csv_set, use_spacy_match, spacy_lemma_map,
            use_heuristics=False,
        )
        if eff is None:
            unmatched_lemmas.append(w)
        else:
            matched_surface_count += 1
            if eff not in seen_eff:
                seen_eff.add(eff)
                matched_effective.append(eff)
    stats = {
        'lemmas_total': len(unique_lemmas),
        'matched_in_csv': matched_surface_count,
        'not_in_csv': len(unmatched_lemmas),
    }
    if not matched_effective:
        empty_out: Dict[str, object] = {
            'message': '未在词库中找到匹配词汇',
            'method': method,
            'words': [],
            'stats': stats,
            'unmatched_lemmas': unmatched_lemmas,
            'use_spacy': use_spacy_match,
        }
        if plan == 'paid':
            empty_out['extract_mode'] = extract_mode
        return jsonify(empty_out), 200

    # 返回完整词条数据，供前端注入选框（按管理员映射解析到词库原形）
    words = []
    for en in matched_effective:
        row = lookup_csv_word(en)
        if row:
            words.append(row)

    stats['matched_in_csv'] = len(words)

    ok_out: Dict[str, object] = {
        'message': f'从文章提取到 {len(words)} 个匹配词汇，请勾选后加入待复习',
        'method': method,
        'words': words,
        'stats': stats,
        'unmatched_lemmas': unmatched_lemmas,
        'use_spacy': use_spacy_match,
    }
    if plan == 'paid':
        ok_out['extract_mode'] = extract_mode
    return jsonify(ok_out), 200


# 词汇导入：本地 Tesseract OCR（需系统安装 tesseract 与 eng.traineddata）
_OCR_MAX_UPLOAD_BYTES = 8 * 1024 * 1024
_OCR_MAX_SIDE = 2400
_OCR_MAX_TOKENS = 500
_OCR_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]*")
_GAOKAO_IMPORT_BATCH_WORDS = 5
_GAOKAO_IMPORT_AUDIT_ATTEMPTS = 2


def _ocr_stack_ready() -> bool:
    return PILImage is not None and pytesseract is not None


def _prepare_image_for_ocr(stream) -> Any:
    assert PILImage is not None
    raw = stream.read()
    if len(raw) > _OCR_MAX_UPLOAD_BYTES:
        raise ValueError("图片过大（单张不超过 8MB）")
    if not raw:
        raise ValueError("文件为空")
    im = PILImage.open(BytesIO(raw))
    im = im.convert("RGB")
    w, h = im.size
    if max(w, h) > _OCR_MAX_SIDE:
        scale = _OCR_MAX_SIDE / float(max(w, h))
        nw = max(1, int(round(w * scale)))
        nh = max(1, int(round(h * scale)))
        im = im.resize((nw, nh), PILImage.Resampling.LANCZOS)
    return im


def _english_tokens_from_ocr_text(text: str) -> List[str]:
    """从 OCR 文本中提取英文词形，按出现顺序去重（忽略大小写），最多 _OCR_MAX_TOKENS 个。"""
    seen: set = set()
    out: List[str] = []
    for m in _OCR_WORD_RE.finditer(text or ""):
        w = m.group(0)
        if len(w) == 1 and w.lower() not in ("a", "i"):
            continue
        key = w.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(w)
        if len(out) >= _OCR_MAX_TOKENS:
            break
    return out


def _audit_combined_gaokao_questions_for_new_entries(
    entries: List[dict],
    candidate_records: Dict[str, dict],
    generation_errors: Dict[str, str],
) -> dict:
    """Audit and publish question candidates produced with new v2 entries."""
    sources: List[dict] = []
    skipped_words: List[str] = []
    seen = set()
    for entry in entries:
        flat = wordbank_v2.v2_entry_to_flat_csv_row(entry)
        key = gaokao_questions.normalize_word(flat.get("english"))
        if not key or key in seen:
            continue
        seen.add(key)
        source = gaokao_questions.source_from_wordbank_row(flat)
        if source:
            sources.append(source)
        else:
            skipped_words.append(key)

    source_keys = [source["english"] for source in sources]
    pending_records = gaokao_questions.pending_candidate_records(
        word_keys=source_keys,
    )
    approved_words: List[str] = []
    rejected_words: List[str] = []
    audit_retry_words: List[str] = []
    pending_items = list(pending_records.items())
    for start in range(0, len(pending_items), _GAOKAO_IMPORT_BATCH_WORDS):
        audit_batch = dict(pending_items[start : start + _GAOKAO_IMPORT_BATCH_WORDS])
        for attempt in range(_GAOKAO_IMPORT_AUDIT_ATTEMPTS):
            try:
                approved, rejected, retry_errors = gaokao_questions.audit_question_records(
                    audit_batch,
                    lambda messages, max_tokens: _deepseek_chat(
                        messages,
                        max_tokens=max_tokens,
                        temperature=0.0,
                    ),
                )
            except Exception as exc:
                logger.warning(
                    "导入新词后审查高考题失败: words=%s attempt=%s error=%s",
                    sorted(audit_batch),
                    attempt + 1,
                    exc,
                )
                approved = {}
                rejected = {}
                retry_errors = {
                    key: f"vocab import audit exception: {exc}" for key in audit_batch
                }
            gaokao_questions.persist_audit_result(approved, rejected, retry_errors)
            approved_words.extend(approved)
            rejected_words.extend(rejected)
            if not retry_errors:
                audit_batch = {}
                break
            audit_batch = {
                key: audit_batch[key]
                for key in retry_errors
                if key in audit_batch
            }
        audit_retry_words.extend(audit_batch)

    already_published = sum(
        1
        for source in sources
        if gaokao_questions.has_complete_questions(
            source["english"],
            source_hash=str(source.get("source_hash") or ""),
        )
        and source["english"] not in approved_words
    )

    return {
        "requested": len(entries),
        "eligible": len(sources),
        "candidates_generated": len(candidate_records),
        "approved": len(set(approved_words)),
        "already_published": already_published,
        "rejected": len(set(rejected_words)),
        "generation_failed": len(generation_errors),
        "audit_retry": len(set(audit_retry_words)),
        "skipped_no_source": len(skipped_words),
        "generated_words": sorted(candidate_records),
        "approved_words": sorted(set(approved_words)),
        "rejected_words": sorted(set(rejected_words)),
        "generation_failed_words": sorted(generation_errors),
        "audit_retry_words": sorted(set(audit_retry_words)),
        "skipped_words": skipped_words,
    }


@app.route('/api/wordbank/ocr-extract', methods=['POST'])
@token_required
def wordbank_ocr_extract(username):
    """上传图片，本地 Tesseract 识别英文并返回 raw_text 与词列表（不入库；供从图片导入流程使用）。"""
    if not _rate_allow(f"ocr:{username}", _RATE_MAX_OCR):
        return jsonify({'error': '图片识别过于频繁，请稍后再试'}), 429
    if not _ocr_stack_ready():
        return jsonify({
            'error': '服务器未启用图片识别：请安装 Pillow、pytesseract，并在系统安装 Tesseract（含 eng 语言包）',
        }), 503
    if 'file' not in request.files:
        return jsonify({'error': '缺少 file 字段'}), 400
    f = request.files['file']
    if not f or not f.filename:
        return jsonify({'error': '未选择文件'}), 400
    ct = (f.mimetype or '').lower()
    if ct not in ('image/jpeg', 'image/png', 'image/webp', 'image/gif'):
        return jsonify({'error': '仅支持 JPEG、PNG、WebP、GIF'}), 400
    try:
        f.stream.seek(0)
    except (OSError, AttributeError, TypeError):
        pass
    try:
        im = _prepare_image_for_ocr(f.stream)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.exception("OCR 图片解析失败: %s", e)
        return jsonify({'error': '无法解析图片'}), 400
    try:
        assert pytesseract is not None
        raw_text = pytesseract.image_to_string(
            im,
            lang="eng",
            config="--psm 6",
        )
    except TesseractNotFoundError:
        return jsonify({
            'error': '未检测到 Tesseract。请在服务器安装（如 macOS: brew install tesseract；Linux: apt install tesseract-ocr tesseract-ocr-eng）',
        }), 503
    except Exception as e:
        logger.exception("OCR 识别失败: %s", e)
        return jsonify({'error': '文字识别失败'}), 500
    tokens = _english_tokens_from_ocr_text(raw_text)
    return jsonify({
        'raw_text': (raw_text or "").strip(),
        'tokens': tokens,
    }), 200


@app.route('/api/wordbank/csv/import-words', methods=['POST'])
@token_required
def import_vocab_to_csv(username):
    """
    词汇导入功能（仅 VIP）：
    - 接收逗号分隔的单词列表
    - 管理员映射（表面形 -> 原形）优先；未映射时先用 spaCy 校验词形是否可识别，不通过则跳过
    - 未映射时：名词「简单复数」表面形（如 apples）规范为原形（apple）再写入词库与待复习；不规则复数（feet 等）保持表面形；动词/形容词不因 lemma 误收成原形（避免 are→be）
    - 疑难词（AI 曾失败）不再重复调用 DeepSeek，直至管理员配置映射或删除记录
    - 仅当 **words_v2.json** 中尚无该英文键时调用 DeepSeek 生成并写入 v2；仅在旧 ``words.csv`` 中有仍会生成并写入 v2。已存在于 v2 则跳过生成。
    - 新写入 v2 的词会继续生成高考题候选，并自动审查；通过后写入私有题库正式区。
    - 可选 also_add_to_queue（默认 True）：是否将词加入当前用户待复习；为 False 时仅写词库
    """
    if not _rate_allow(f"vocab_import:{username}", _RATE_MAX_VOCAB_IMPORT):
        return jsonify({'error': '词汇导入过于频繁，请稍后再试'}), 429
    if not is_paid_user(username):
        return jsonify({'error': '词汇导入功能仅限 VIP 用户使用'}), 403

    body = request.get_json(silent=True) or {}
    raw = str(body.get('words', '')).strip()
    level_hint = str(body.get('level', '')).strip()  # 用户指定的level（可选）
    also_queue = bool(body.get('also_add_to_queue', True))

    if not raw:
        return jsonify({'error': '单词列表不能为空'}), 400

    # 解析逗号分隔（支持 a,b 和 a, b）
    input_surfaces = [w.strip().lower() for w in re.split(r'[,，]', raw) if w.strip()]
    if not input_surfaces:
        return jsonify({'error': '未解析到有效单词'}), 400
    if len(input_surfaces) > 500:
        return jsonify({'error': '单次最多处理 500 个单词'}), 400

    # 是否调用 AI：仅看新词库 v2；老 CSV 仅有仍会生成并写入 v2
    v2_keys = wordbank_v2.get_v2_english_key_set()

    with _TROUBLES_LOCK:
        tdoc = _read_troubles_unlocked()
        mappings = dict(tdoc.get('mappings') or {})
        difficult = dict(tdoc.get('difficult') or {})

    # 批量 spaCy：仅用于校验「是否可识别词形」，不用于写入词库时的英文键
    uniq_for_spacy = list(
        dict.fromkeys(s for s in input_surfaces if s not in mappings),
    )
    lemma_map: Optional[Dict[str, str]] = None
    if uniq_for_spacy and _wordbank_lemma_spacy_enabled() and _get_spacy_nlp() is not None:
        lemma_map = _spacy_lemma_map_for_surfaces(uniq_for_spacy)
        if not lemma_map:
            lemma_map = None

    invalid_surfaces: List[str] = []
    surface_to_target: Dict[str, str] = {}
    for s in input_surfaces:
        if not _vocab_import_spacy_accepts_surface(s, mappings, lemma_map):
            invalid_surfaces.append(s)
            continue
        surface_to_target[s] = (
            mappings[s] if s in mappings else _normalize_import_english_surface(s)
        )

    already_in_v2 = [
        s for s in input_surfaces
        if s in surface_to_target and surface_to_target[s] in v2_keys
    ]

    # 待生成：按用户输入顺序去重表面形；英文键 = 管理员映射目标或原词
    to_generate_unique: List[str] = []
    seen_tg = set()
    for s in input_surfaces:
        if s not in surface_to_target:
            continue
        t = surface_to_target[s]
        if t in v2_keys:
            continue
        if s in difficult:
            continue
        if s in seen_tg:
            continue
        seen_tg.add(s)
        to_generate_unique.append(s)

    blocked_surfaces: List[str] = []
    for s in input_surfaces:
        if s not in surface_to_target:
            continue
        t = surface_to_target[s]
        if t in v2_keys:
            continue
        if s in difficult:
            blocked_surfaces.append(s)
    blocked_surfaces = _dedupe_preserve_order(blocked_surfaces)

    to_generate = to_generate_unique

    if to_generate and not get_deepseek_api_key():
        return jsonify({'error': '服务端未配置 DEEPSEEK_API_KEY，无法使用此功能'}), 503

    generated_entries: List[dict] = []
    failed_surfaces: List[str] = []
    gaokao_candidate_records: Dict[str, dict] = {}
    gaokao_generation_errors: Dict[str, str] = {}

    if to_generate:
        wordbank_so_far = set(v2_keys)
        # surface -> 本次写入词库使用的英文键（用户原词或映射目标）
        gen_key_to_surface: Dict[str, str] = {}  # 同一 batch 内 key 应对应唯一 surface（去重后）
        for s in to_generate:
            gen_key_to_surface[surface_to_target[s]] = s

        for i in range(0, len(to_generate), _GAOKAO_IMPORT_BATCH_WORDS):
            batch_surfaces = to_generate[i : i + _GAOKAO_IMPORT_BATCH_WORDS]
            batch = [surface_to_target[s] for s in batch_surfaces]
            entries = deepseek_generate_word_entries_v2(
                batch,
                level=level_hint,
                include_gaokao_candidate=True,
            )
            batch_lower = {b.lower() for b in batch}
            if entries is not None:
                rows, success = accumulate_valid_deepseek_v2_entries(
                    entries,
                    level_hint=level_hint,
                    v2_so_far=wordbank_so_far,
                    batch_lower=batch_lower,
                )
                if rows:
                    try:
                        _, skipped_dup = wordbank_v2.append_words_v2_entries(rows)
                        wordbank_v2.invalidate_words_v2_cache()
                        invalidate_merge_wordbank_rows_cache()
                        if skipped_dup:
                            logger.info("words_v2 批次落盘，跳过已存在键: %s", skipped_dup[:20])
                    except Exception as e:
                        logger.error("写入 words_v2.json 失败（本批落盘）: %s", e)
                        return jsonify(
                            {
                                'error': f'写入新词库失败（此前批次若已成功则已保存）: {e}',
                            }
                        ), 500
                    candidate_records, candidate_errors = finalize_combined_gaokao_candidates(
                        entries,
                        rows,
                    )
                    try:
                        gaokao_questions.persist_candidate_result(
                            candidate_records,
                            candidate_errors,
                        )
                        gaokao_candidate_records.update(candidate_records)
                        gaokao_generation_errors.update(candidate_errors)
                    except Exception as exc:
                        logger.exception(
                            "新词已写入 words_v2，但高考题候选落盘失败: %s",
                            exc,
                        )
                        for row in rows:
                            key = gaokao_questions.normalize_word(row.get("english"))
                            if key:
                                gaokao_generation_errors[key] = (
                                    f"combined candidate persistence failed: {exc}"
                                )
                    generated_entries.extend(rows)
                    wordbank_so_far = wordbank_v2.get_v2_english_key_set()
                miss_lemmas = [b for b in batch if b.lower() not in success]
            else:
                miss_lemmas = list(batch)
            for key in miss_lemmas:
                surf = gen_key_to_surface.get(key, key)
                failed_surfaces.append(surf)

        failed_surfaces = _dedupe_preserve_order(failed_surfaces)
        if failed_surfaces:
            record_surfaces_to_difficult(failed_surfaces)

    # 加入待复习
    queue_result = None
    if also_queue:
        items_to_queue = []
        for s in already_in_v2:
            t = surface_to_target[s]
            row = lookup_csv_word(t)
            if row:
                picked = pick_example_for_word(row, row.get("english") or "")
                items_to_queue.append({
                    'english': picked['english'],
                    'chinese': picked['chinese'],
                    'example': picked['example'],
                })
        for entry in generated_entries:
            flat = wordbank_v2.v2_entry_to_flat_csv_row(entry)
            picked = pick_example_for_word(flat, flat.get("english") or "")
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

    try:
        gaokao_result = _audit_combined_gaokao_questions_for_new_entries(
            generated_entries,
            gaokao_candidate_records,
            gaokao_generation_errors,
        )
    except Exception as exc:
        gaokao_words = sorted({
            gaokao_questions.normalize_word(entry.get("english"))
            for entry in generated_entries
            if gaokao_questions.normalize_word(entry.get("english"))
        })
        logger.exception("新词已入库，但自动生成高考题流程失败: %s", exc)
        gaokao_result = {
            "requested": len(generated_entries),
            "eligible": len(gaokao_words),
            "candidates_generated": 0,
            "approved": 0,
            "already_published": 0,
            "rejected": 0,
            "generation_failed": len(gaokao_words),
            "audit_retry": 0,
            "skipped_no_source": 0,
            "generated_words": [],
            "approved_words": [],
            "rejected_words": [],
            "generation_failed_words": gaokao_words,
            "audit_retry_words": [],
            "skipped_words": [],
        }

    msg = f"处理 {len(input_surfaces)} 个单词：{len(generated_entries)} 个新词已写入新词库（words_v2.json）"
    if already_in_v2:
        msg += f"，{len(already_in_v2)} 个已在新词库（words_v2.json）中，未再生成"
    if invalid_surfaces:
        inv = _dedupe_preserve_order(invalid_surfaces)
        msg += f"，{len(inv)} 个未通过词形校验（已跳过）"
    if blocked_surfaces:
        msg += f"，{len(blocked_surfaces)} 个疑难词（已跳过 AI 生成）"
    if failed_surfaces:
        msg += f"，{len(failed_surfaces)} 个生成失败已记入疑难词"
    if gaokao_result["approved"]:
        msg += f"；已自动生成并通过高考题审查 {gaokao_result['approved']} 个"
    if gaokao_result["already_published"]:
        msg += f"；{gaokao_result['already_published']} 个已有已审查高考题"
    if gaokao_result["rejected"]:
        msg += f"；{gaokao_result['rejected']} 个高考题候选未通过语义审查"
    gaokao_deferred = (
        gaokao_result["generation_failed"]
        + gaokao_result["audit_retry"]
        + gaokao_result["skipped_no_source"]
    )
    if gaokao_deferred:
        msg += f"；{gaokao_deferred} 个高考题候选待后续补全"
    if queue_result:
        msg += f"；已加入待复习 {queue_result.get('added', 0)} 个"
    elif not also_queue:
        msg += "；未加入待复习（仅写入词库）"

    return jsonify({
        'message': msg,
        'new_in_csv': len(generated_entries),
        'already_in_v2': len(already_in_v2),
        'already_in_v2_words': already_in_v2,
        # 兼容旧前端字段名（语义：已在新词库 v2，非「仅 CSV」）
        'already_in_csv': len(already_in_v2),
        'already_in_csv_words': already_in_v2,
        'failed': failed_surfaces,
        'blocked_surfaces': blocked_surfaces,
        'invalid_surfaces': _dedupe_preserve_order(invalid_surfaces),
        'queue_result': queue_result,
        'also_add_to_queue': also_queue,
        'gaokao_questions': gaokao_result,
    }), 200


@app.route('/api/wordbank/csv/trouble-status', methods=['GET'])
@token_required
def wordbank_trouble_status(username):
    """
    课文/导入前查询：某表面形是否被疑难词拦截、是否已有管理员映射。
    """
    q = request.args.get('q', '').strip().lower()
    if not q:
        return jsonify({
            'blocked': False,
            'mapped_to': None,
            'in_difficult': False,
            'in_csv': False,
            'resolved_lemma': None,
        }), 200
    with _TROUBLES_LOCK:
        tdoc = _read_troubles_unlocked()
        mappings = dict(tdoc.get('mappings') or {})
        difficult = dict(tdoc.get('difficult') or {})
    csv_set = get_wordbank_english_set()
    hit = _first_lemma_in_csv(q, mappings, csv_set, use_spacy=True, use_heuristics=False)
    if hit is not None:
        in_csv = True
        resolved_lemma = hit
        mapped_to = hit if hit != q else None
    else:
        in_csv = False
        resolved_lemma = _lemma_for_vocab_not_in_csv(q, mappings, use_heuristics=False)
        mapped_to = resolved_lemma if resolved_lemma != q else None
    blocked = (q in difficult) and not in_csv
    return jsonify({
        'blocked': blocked,
        'mapped_to': mapped_to,
        'in_difficult': q in difficult,
        'in_csv': in_csv,
        'resolved_lemma': resolved_lemma,
    }), 200


@app.route('/api/wordbank/community', methods=['GET'])
@token_required
def get_community_wordbank(username):
    """返回社区待审区（家长贡献）；正式词条仍以 words_v2.json 为准。"""
    with _community_wb_guard():
        data = _read_community_file_unlocked()
    return jsonify(
        {
            "schema": data.get("schema"),
            "phase": "community",
            "label": data.get("label", "社区词库（待审核）"),
            "count": len(data.get("words") or []),
            "words": data.get("words") or [],
        }
    ), 200


@app.route('/api/words/pending', methods=['GET'])
@token_required
def list_pending_words_for_settings(username):
    """待复习单词列表（配置页；家长登录时可删；学生仅在未开通家长账户时可删）。"""
    is_parent = getattr(g, "is_parent", False)
    has_parent = student_has_enabled_parent_account(username)
    can_remove = bool(is_parent or not has_parent)
    try:
        with user_reciter_session(username) as reciter:
            today_d = china_today()
            out: List[dict] = []
            for w in reciter.all_words:
                csv_row = lookup_csv_word(w.english)
                nd = w.next_review_date
                out.append(
                    {
                        "english": w.english,
                        "chinese": w.chinese,
                        "phonetic": csv_row.get("phonetic", "") if csv_row else "",
                        "next_review_date": nd.isoformat(),
                        "remaining_days": (nd - today_d).days,
                        "review_count": w.review_count,
                    }
                )
            return jsonify(
                {
                    "words": out,
                    "count": len(out),
                    "can_remove_pending": can_remove,
                }
            ), 200
    except Exception as e:
        logger.error("列出待复习失败: %s", e)
        return jsonify({"error": "服务器内部错误"}), 500


@app.route('/api/words/pending/remove', methods=['POST'])
@token_required
def remove_pending_words_api(username):
    """从待复习中移除单词（不删除已掌握词）。家长登录可删；学生仅在未开通家长账户时可删。"""
    if not _rate_allow(f"pending_rm:{_client_ip()}", 120):
        return jsonify({"error": "请求过于频繁，请稍后再试"}), 429
    is_parent = getattr(g, "is_parent", False)
    if not is_parent and student_has_enabled_parent_account(username):
        return jsonify(
            {
                "error": "已开通家长账户，待复习词汇请由家长登录后在配置中管理",
            }
        ), 403
    body = request.get_json(silent=True) or {}
    raw = body.get("english")
    if isinstance(raw, str):
        english_list = [raw]
    elif isinstance(raw, list):
        english_list = raw
    else:
        return jsonify({"error": "请提供 english 字段（字符串或字符串数组）"}), 400
    seen = set()
    keys_ordered: List[str] = []
    for e in english_list:
        s = str(e or "").strip()
        if not s:
            continue
        k = s.lower()
        if k not in seen:
            seen.add(k)
            keys_ordered.append(s)
    if not keys_ordered:
        return jsonify({"error": "未提供有效单词"}), 400
    if len(keys_ordered) > 200:
        return jsonify({"error": "单次最多移除 200 条"}), 400

    try:
        with user_reciter_session(username) as reciter:
            result = reciter.remove_pending_words_by_english(keys_ordered)
            task_bundle = reciter.get_today_learning_plan(
                listening_available=_reliable_listening_available(),
            )
            result['plan'] = task_bundle['plan']
            reciter.save_learning_data(backup=False)
        _invalidate_user_reciter_cache(username)
        logger.info(
            "用户 %s 待复习移除: removed=%s not_found=%s",
            username,
            result.get("removed"),
            len(result.get("not_found") or []),
        )
        return jsonify(result), 200
    except Exception as e:
        logger.error("待复习移除失败: %s", e)
        return jsonify({"error": "服务器内部错误"}), 500


@app.route('/api/wordbank/community/import-simple', methods=['POST'])
@token_required
def community_import_simple(username):
    """
    家长简易导入：单词 + 例句 + 译文 → 写入社区待审区。
    若英文已出现在正式/旧版系统词库，或已在社区待审区中，则拒绝/跳过并返回明细。
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

    with _community_wb_guard():
        data = _read_community_file_unlocked()
        words: List[dict] = list(data.get("words") or [])
        comm_keys = {_community_word_key(w.get("english")) for w in words if w.get("english")}

        for row in rows:
            en = str(row.get("english", "")).strip()[:500]
            zh = str(row.get("chinese", "")).strip()[:500]
            ex_raw = row.get("example")
            ex = str(ex_raw).strip()[:4000] if ex_raw is not None else ""
            if not en or not zh:
                skipped_invalid += 1
                continue
            en = _normalize_import_english_surface(en)
            if len(en) > 500:
                en = en[:500]
            key = _community_word_key(en)
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
                "added_at": china_now_iso(timespec="seconds"),
                "status": "pending",
                "promotion_attempts": 0,
                "last_attempt_at": None,
                "last_error": None,
                "promoted_at": None,
                "promoted_word_key": None,
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
                review_state = reciter.get_review_state(w)
                state_payload = reciter.review_state_payload(w)
                csv_row = lookup_csv_word(w.english)
                ex_text = ''
                if csv_row:
                    picked = pick_example_for_word(csv_row, w.english)
                    ex_text = (picked.get('example') or '').strip()
                if not ex_text and getattr(w, 'example', None):
                    ex_text = (w.example or '').strip()
                words.append({
                    'english': w.english,
                    'chinese': w.chinese,
                    'phonetic': csv_row.get('phonetic', '') if csv_row else '',
                    'example': ex_text,
                    'review_count': w.review_count,
                    'mastered_date': review_state.get('mastered_date') or w.next_review_date.isoformat(),
                    'memory_status': str(review_state.get('memory_status') or 'stable'),
                    **state_payload,
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
    revoke_token(SESSION_KIND_ADMIN, tok)
    return jsonify({'message': '已退出'}), 200


@app.route('/api/admin/performance/logs', methods=['GET'])
@admin_required
def admin_performance_logs():
    """列出最近的性能采集 JSONL 文件。"""
    limit = request.args.get("limit", 14, type=int) or 14
    return jsonify(
        {
            "enabled": performance_enabled(),
            "log_dir": str(performance_log_dir(DATA_DIR)),
            "logs": list_performance_logs(DATA_DIR, limit=limit),
        }
    ), 200


@app.route('/api/admin/performance/logs/<name>', methods=['GET'])
@admin_required
def admin_performance_log_download(name):
    """下载单个性能采集 JSONL 文件。"""
    if not is_valid_performance_log_name(name):
        return jsonify({"error": "无效的日志文件名"}), 400
    path = performance_log_dir(DATA_DIR) / name
    if not path.is_file():
        return jsonify({"error": "日志不存在"}), 404
    return send_file(
        path,
        mimetype="application/x-ndjson",
        as_attachment=True,
        download_name=name,
        max_age=0,
    )


@app.route('/api/admin/performance/logs/<name>/share-link', methods=['POST'])
@admin_required
def admin_performance_log_share_link(name):
    """生成短期有效的公开性能日志下载链接。"""
    if not is_valid_performance_log_name(name):
        return jsonify({"error": "无效的日志文件名"}), 400
    path = performance_log_dir(DATA_DIR) / name
    if not path.is_file():
        return jsonify({"error": "日志不存在"}), 404
    data = request.get_json(silent=True) or {}
    try:
        ttl = int(data.get("ttl_seconds") or 1800)
    except (TypeError, ValueError):
        ttl = 1800
    ttl = max(60, min(PERFORMANCE_SHARE_MAX_TTL_SEC, ttl))
    expires_at = int(time() + ttl)
    sig = sign_performance_log_name(name, expires_at, app.secret_key)
    url = request.host_url.rstrip("/") + f"/api/performance/logs/{name}?expires={expires_at}&sig={sig}"
    return jsonify(
        {
            "url": url,
            "name": name,
            "expires_at": expires_at,
            "ttl_seconds": ttl,
        }
    ), 201


@app.route('/api/performance/logs/<name>', methods=['GET'])
def performance_log_public_download(name):
    """短期签名链接下载性能日志；不需要管理员 token。"""
    if not is_valid_performance_log_name(name):
        return jsonify({"error": "无效的日志文件名"}), 400
    try:
        expires_at = int(request.args.get("expires") or "0")
    except (TypeError, ValueError):
        expires_at = 0
    sig = request.args.get("sig") or ""
    if not verify_performance_share_signature(name, expires_at, sig, app.secret_key, int(time())):
        return jsonify({"error": "链接无效或已过期"}), 403
    path = performance_log_dir(DATA_DIR) / name
    if not path.is_file():
        return jsonify({"error": "日志不存在"}), 404
    return send_file(
        path,
        mimetype="application/x-ndjson",
        as_attachment=True,
        download_name=name,
        max_age=0,
    )


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
        if is_parent_user_record(u):
            continue
        summ = _learning_data_summary(uname)
        pname = parent_login_username_for_child(uname)
        has_parent = bool(
            pname and pname in users and is_parent_user_record(users.get(pname))
        )
        out.append({
            'username': uname,
            'email': u.get('email'),
            'created_at': u.get('created_at'),
            'enabled': u.get('enabled', True),
            'plan': u.get('plan', 'free'),
            **invite_quota_payload(u),
            'pending_words': summ['pending'],
            'mastered_words': summ['mastered'],
            'parent_account_enabled': has_parent,
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

    removed_parent = None

    def _set_enabled(users: Dict[str, Any]) -> Tuple[bool, str, Optional[str]]:
        if username not in users:
            return False, '用户不存在', None
        if is_parent_user_record(users[username]):
            return False, '请使用「家长账户」开关管理家长账号', None
        users[username]['enabled'] = enabled
        removed = None
        if not enabled:
            pname = parent_login_username_for_child(username)
            if pname and pname in users and is_parent_user_record(users.get(pname)):
                del users[pname]
                removed = pname
        return True, '', removed

    ok, err, removed_parent = mutate_users(_set_enabled)
    if not ok:
        if err == '用户不存在':
            return jsonify({'error': err}), 404
        return jsonify({'error': err}), 400
    if removed_parent:
        _revoke_user_tokens(removed_parent)
    if not enabled:
        _revoke_user_tokens(username)
        _invalidate_user_reciter_cache(username)
        logger.info("管理员禁用用户: %s", username)
    else:
        logger.info("管理员启用用户: %s", username)

    return jsonify({'username': username, 'enabled': enabled}), 200


@app.route('/api/admin/users/<username>/parent', methods=['PATCH'])
@admin_required
def admin_set_user_parent(username):
    """为学生开启或关闭家长账户；登录名为 学生名_parent，默认密码见 DEFAULT_PARENT_PASSWORD。"""
    if not is_valid_username(username):
        return jsonify({'error': '无效的用户名'}), 400
    data = request.get_json(silent=True) or {}
    if 'enabled' not in data:
        return jsonify({'error': '缺少 enabled 字段'}), 400
    want = bool(data['enabled'])

    pname = parent_login_username_for_child(username)
    if not pname:
        return jsonify({'error': '该用户名过长，无法创建家长账号（须为 学生名_parent 且不超过 32 字符）'}), 400

    def _set_parent(users: Dict[str, Any]) -> Tuple[bool, int, Dict[str, Any]]:
        u = users.get(username)
        if not isinstance(u, dict):
            return False, 404, {'error': '用户不存在'}
        if is_parent_user_record(u):
            return False, 400, {'error': '只能为学生账号设置家长账户'}
        if want:
            created_new = False
            if pname in users:
                pr = users[pname]
                if not is_parent_user_record(pr) or pr.get('child_username') != username:
                    return False, 400, {'error': '家长登录名已被占用'}
            else:
                created_new = True
                users[pname] = {
                    'role': USER_ROLE_PARENT,
                    'child_username': username,
                    'password_hash': hash_password(DEFAULT_PARENT_PASSWORD),
                    'enabled': True,
                    'created_at': china_now_iso(timespec="seconds"),
                }
            body = {
                'username': username,
                'parent_enabled': True,
                'parent_login': pname,
            }
            if created_new:
                body['default_password_hint'] = DEFAULT_PARENT_PASSWORD
            return True, 200, body

        if pname in users:
            pr = users[pname]
            if not is_parent_user_record(pr) or pr.get('child_username') != username:
                return False, 400, {'error': '家长账号数据不一致'}
            del users[pname]
        return True, 200, {'username': username, 'parent_enabled': False}

    ok, status_code, body = mutate_users(_set_parent)
    if not ok:
        return jsonify(body), status_code
    if want:
        logger.info("管理员开启家长账户: student=%s parent=%s", username, pname)
        return jsonify(body), 200
    if pname:
        _revoke_user_tokens(pname)
    logger.info("管理员关闭家长账户: student=%s", username)
    return jsonify(body), 200


@app.route('/api/admin/users/<username>/parent-password', methods=['PATCH'])
@admin_required
def admin_set_parent_password(username):
    """管理员重置指定学生对应家长账户的登录密码（username 为学生名，非 _parent）。"""
    if not is_valid_username(username):
        return jsonify({'error': '无效的用户名'}), 400
    data = request.get_json(silent=True) or {}
    new_password = (data.get('password') or '').strip()
    if len(new_password) < 6:
        return jsonify({'error': '密码至少6个字符'}), 400

    pname = parent_login_username_for_child(username)
    def _set_parent_password(users: Dict[str, Any]) -> Tuple[bool, int, str]:
        u = users.get(username)
        if not isinstance(u, dict):
            return False, 404, '用户不存在'
        if is_parent_user_record(u):
            return False, 400, '请在学生所在行使用「家长密码」'
        if not pname or pname not in users or not is_parent_user_record(users[pname]):
            return False, 404, '未开启家长账户'
        if users[pname].get('child_username') != username:
            return False, 400, '家长账号数据不一致'
        users[pname]['password_hash'] = hash_password(new_password)
        return True, 200, ''

    ok, status_code, err = mutate_users(_set_parent_password)
    if not ok:
        return jsonify({'error': err}), status_code
    _revoke_user_tokens(pname)
    logger.info("管理员重置家长密码: student=%s parent=%s", username, pname)
    return jsonify({
        'username': username,
        'parent_login': pname,
        'message': '家长密码已更新，该家长需重新登录',
    }), 200


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

    def _set_user_password(users: Dict[str, Any]) -> Tuple[bool, int, str]:
        if username not in users:
            return False, 404, '用户不存在'
        if is_parent_user_record(users[username]):
            return False, 400, '家长账户请使用「家长密码」按钮重置，或由家长在客户端修改'
        users[username]['password_hash'] = hash_password(new_password)
        return True, 200, ''

    ok, status_code, err = mutate_users(_set_user_password)
    if not ok:
        return jsonify({'error': err}), status_code
    _revoke_user_tokens(username)
    _invalidate_user_reciter_cache(username)
    logger.info("管理员重置用户密码: %s", username)
    return jsonify({'username': username, 'message': '密码已更新，该用户需重新登录'}), 200


@app.route('/api/admin/users/<username>/invite-quota', methods=['PATCH'])
@admin_required
def admin_reset_user_invite_quota(username):
    """管理员重置用户邀请次数；默认把已用数清零，可选调整总额度。"""
    if not is_valid_username(username):
        return jsonify({'error': '无效的用户名'}), 400
    data = request.get_json(silent=True) or {}
    limit_raw = data.get("limit")
    new_limit: Optional[int] = None
    if limit_raw not in (None, ""):
        try:
            new_limit = int(limit_raw)
        except (TypeError, ValueError):
            return jsonify({'error': 'limit 须为整数'}), 400
        if new_limit < 0 or new_limit > 100:
            return jsonify({'error': 'limit 须在 0 到 100 之间'}), 400

    def _reset_quota(users: Dict[str, Any]) -> Tuple[bool, int, Dict[str, Any]]:
        u = users.get(username)
        if not isinstance(u, dict):
            return False, 404, {'error': '用户不存在'}
        if is_parent_user_record(u):
            return False, 400, {'error': '不能为家长账户设置邀请次数'}
        if new_limit is not None:
            u["invite_quota_limit"] = new_limit
        else:
            u["invite_quota_limit"] = invite_quota_limit(u)
        u["invite_quota_used"] = 0
        return True, 200, {
            "username": username,
            **invite_quota_payload(u),
            "message": "邀请次数已重置",
        }

    ok, status_code, body = mutate_users(_reset_quota)
    if not ok:
        return jsonify(body), status_code
    logger.info("管理员重置用户邀请次数: %s limit=%s", username, body.get("invite_quota_limit"))
    return jsonify(body), 200


@app.route('/api/admin/users/<username>', methods=['DELETE'])
@admin_required
def admin_delete_user(username):
    """
    永久删除学生用户及其数据目录；须同时提供管理员密码两次且一致，并与配置中的管理员密码匹配。
    """
    if not _rate_allow(f"admin_delete_user:{_client_ip()}", _RATE_MAX_ADMIN_DELETE_USER):
        return jsonify({'error': '请求过于频繁，请稍后再试'}), 429
    if not is_valid_username(username):
        return jsonify({'error': '无效的用户名'}), 400

    data = request.get_json(silent=True) or {}
    p1 = (data.get('admin_password') or '').strip()
    p2 = (data.get('admin_password_confirm') or '').strip()
    if not p1 or not p2:
        return jsonify({'error': '请填写两遍管理员密码'}), 400
    if p1 != p2:
        return jsonify({'error': '两次输入的管理员密码不一致'}), 400

    cfg = _get_admin_config()
    if not verify_admin_credentials(cfg['username'], p1):
        logger.warning("管理员删除用户失败（密码错误）: target=%s ip=%s", username, _client_ip())
        return jsonify({'error': '管理员密码错误'}), 401

    users = load_users()
    if username not in users:
        return jsonify({'error': '用户不存在'}), 404
    if is_parent_user_record(users[username]):
        return jsonify({'error': '不能单独删除家长账号，请删除对应学生账号'}), 400

    _purge_student_account_completely(username)
    logger.info("管理员已删除用户: %s ip=%s", username, _client_ip())
    return jsonify({'username': username, 'message': '用户已删除'}), 200


@app.route('/api/admin/config', methods=['GET'])
@admin_required
def admin_get_config():
    """读取 config.json（敏感字段脱敏显示）。"""
    cfg = _load_app_config()
    raw = str(cfg.get("deepseek_api_key", "") or "").strip()
    key_plain = _decrypt_deepseek_from_config(raw) if raw else ""
    if raw and raw.startswith(_DEEPSEEK_ENC_PREFIX) and not key_plain:
        preview = "（已加密，解密失败或未配置 SECRET_KEY）"
    elif key_plain:
        preview = (
            key_plain[:8] + "…"
            if len(key_plain) > 8
            else ("（已设置）" if key_plain else "")
        )
    else:
        preview = ""
    return jsonify({
        "deepseek_api_key_set": bool(raw),
        "deepseek_api_key_preview": preview,
        "article_ai_extract_enabled": bool(cfg.get("article_ai_extract_enabled", False)),
    }), 200


@app.route('/api/admin/config', methods=['PATCH'])
@admin_required
def admin_update_config():
    """更新 config.json 中的运行时配置（deepseek_api_key、article_ai_extract_enabled 等）。"""
    data = request.get_json(silent=True) or {}
    cfg = _load_app_config()
    changed = False
    if "deepseek_api_key" in data:
        new_key = str(data["deepseek_api_key"] or "").strip()
        if new_key:
            try:
                cfg["deepseek_api_key"] = _encrypt_deepseek_for_config(new_key)
            except RuntimeError as e:
                return jsonify({"error": str(e)}), 400
        else:
            cfg["deepseek_api_key"] = ""
        changed = True
    if "article_ai_extract_enabled" in data:
        cfg["article_ai_extract_enabled"] = bool(data["article_ai_extract_enabled"])
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


@app.route('/api/admin/system-broadcast', methods=['GET'])
@admin_required
def admin_get_system_broadcast():
    """读取当前系统广播正文（管理员编辑用）。"""
    doc = get_system_broadcast()
    return jsonify({
        'id': doc.get('id') or '',
        'message': doc.get('message') or '',
        'created_at': doc.get('created_at'),
    }), 200


@app.route('/api/admin/system-broadcast', methods=['PUT'])
@admin_required
def admin_put_system_broadcast():
    """发布或清空系统广播；发布时生成新 id，所有未确认用户将在下次登录后看到。"""
    data = request.get_json(silent=True) or {}
    msg = _sanitize_broadcast_message(str(data.get('message') or ''))
    with _SYSTEM_BROADCAST_LOCK:
        if not msg:
            doc = _empty_system_broadcast_doc()
        else:
            doc = {
                'schema': _SYSTEM_BROADCAST_SCHEMA,
                'id': uuid.uuid4().hex,
                'message': msg,
                'created_at': china_now_iso(timespec="seconds"),
            }
        _write_system_broadcast_atomic(doc)
    logger.info("管理员更新系统广播: empty=%s", not bool(msg))
    return jsonify({
        'id': doc.get('id') or '',
        'message': doc.get('message') or '',
        'created_at': doc.get('created_at'),
    }), 200


@app.route('/api/admin/users/<username>/plan', methods=['PATCH'])
@admin_required
def admin_set_user_plan(username):
    """管理员设置用户套餐（free / paid，paid 即 VIP）。"""
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


@app.route('/api/admin/users/<username>/words', methods=['GET'])
@admin_required
def admin_list_user_words(username):
    """列出指定用户待复习/已掌握单词（管理员）。"""
    if not is_valid_username(username):
        return jsonify({'error': '无效的用户名'}), 400
    users = load_users()
    if username not in users:
        return jsonify({'error': '用户不存在'}), 404

    status = request.args.get('status', 'all').strip().lower()
    if status not in ('all', 'pending', 'mastered'):
        status = 'all'
    q = request.args.get('q', '').strip().lower()

    try:
        with user_reciter_session(username) as reciter:
            words = []
            if status in ('all', 'pending'):
                for w in reciter.all_words:
                    csv_row = lookup_csv_word(w.english)
                    words.append({
                        'english': w.english,
                        'chinese': w.chinese,
                        'phonetic': csv_row.get('phonetic', '') if csv_row else '',
                        'status': 'pending',
                        'success_count': w.success_count,
                        'max_success_count': reciter.config.MAX_SUCCESS_COUNT,
                        'review_count': w.review_count,
                        'next_review_date': w.next_review_date.isoformat(),
                    })
            if status in ('all', 'mastered'):
                for w in reciter.mastered_words:
                    csv_row = lookup_csv_word(w.english)
                    words.append({
                        'english': w.english,
                        'chinese': w.chinese,
                        'phonetic': csv_row.get('phonetic', '') if csv_row else '',
                        'status': 'mastered',
                        'review_count': w.review_count,
                        'next_review_date': w.next_review_date.isoformat(),
                    })
            if q:
                words = [
                    x for x in words
                    if q in x['english'].lower() or q in (x.get('chinese') or '').lower()
                ]
            return jsonify({'words': words, 'count': len(words)}), 200
    except Exception as e:
        logger.error("管理员列出用户单词失败: %s", e)
        return jsonify({'error': '服务器内部错误'}), 500


@app.route('/api/admin/users/<username>/words', methods=['DELETE'])
@admin_required
def admin_delete_user_words(username):
    """从指定用户学习数据中永久删除单词（管理员）。"""
    if not is_valid_username(username):
        return jsonify({'error': '无效的用户名'}), 400
    users = load_users()
    if username not in users:
        return jsonify({'error': '用户不存在'}), 404

    data = request.get_json(silent=True) or {}
    raw = data.get('english')
    if isinstance(raw, str):
        english_list = [raw]
    elif isinstance(raw, list):
        english_list = raw
    else:
        return jsonify({'error': '请提供 english 字段（字符串或字符串数组）'}), 400
    if not english_list:
        return jsonify({'error': '单词列表不能为空'}), 400

    try:
        with user_reciter_session(username) as reciter:
            result = reciter.remove_words_by_english(english_list)
        _invalidate_user_reciter_cache(username)
        logger.info(
            "管理员删除用户 %s 单词: removed=%s not_found=%s",
            username,
            result.get('removed'),
            len(result.get('not_found') or []),
        )
        return jsonify(result), 200
    except Exception as e:
        logger.error("管理员删除用户单词失败: %s", e)
        return jsonify({'error': '服务器内部错误'}), 500


@app.route('/api/admin/invites', methods=['POST'])
@admin_required
def admin_create_invite():
    """生成一次性邀请码（仅响应中明文展示一次）。"""
    with _locked_invite_storage():
        data = load_invites()
        invites = data.setdefault('invites', [])
        plain = _fresh_invite_code(invites).strip().upper()
        inv_id = str(uuid.uuid4())
        entry = {
            'id': inv_id,
            'code_hash': _hash_invite_code(plain),
            'created_at': china_now_iso(timespec="seconds"),
            'created_by': os.getenv('ADMIN_USERNAME', 'admin'),
            'created_by_kind': 'admin',
            'used_at': None,
            'used_by': None,
        }
        invites.append(entry)
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
    with _locked_invite_storage():
        data = load_invites()
    rows = []
    for inv in data.get('invites', []):
        if isinstance(inv, dict):
            rows.append(invite_public_row(inv))
    rows.sort(key=lambda x: x.get('created_at') or '', reverse=True)
    return jsonify({'invites': rows}), 200


@app.route('/api/admin/wordbank/troubles', methods=['GET'])
@admin_required
def admin_list_wordbank_troubles():
    """疑难词列表 + 表面形到词汇原形的映射（管理员）。"""
    with _TROUBLES_LOCK:
        data = _read_troubles_unlocked()
    difficult = data.get('difficult') or {}
    mappings = data.get('mappings') or {}
    diff_list = []
    for surf in sorted(difficult.keys()):
        meta = difficult.get(surf)
        if not isinstance(meta, dict):
            meta = {}
        diff_list.append({
            'surface': surf,
            'added_at': meta.get('added_at'),
            'last_attempt': meta.get('last_attempt'),
            'attempts': int(meta.get('attempts') or 0),
        })
    map_list = [{'surface': k, 'lemma': v} for k, v in sorted(mappings.items())]
    return jsonify({'difficult': diff_list, 'mappings': map_list}), 200


@app.route('/api/admin/wordbank/troubles/mapping', methods=['POST'])
@admin_required
def admin_add_wordbank_mapping():
    """设置映射：表面形 -> 词汇原形；该表面形从疑难词中移除并进入映射表。"""
    body = request.get_json(silent=True) or {}
    surface = str(body.get('surface', '')).strip()
    lemma = str(body.get('lemma', '')).strip()
    try:
        set_wordbank_surface_mapping(surface, lemma)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    logger.info("管理员设置词形映射: %s -> %s", surface, lemma)
    return jsonify({'message': '映射已保存，疑难词中已移除该表面形（若有）'}), 200


@app.route('/api/admin/wordbank/troubles/mapping', methods=['DELETE'])
@admin_required
def admin_remove_wordbank_mapping():
    body = request.get_json(silent=True) or {}
    surface = str(body.get('surface', '')).strip()
    if not surface:
        return jsonify({'error': '缺少 surface'}), 400
    if not delete_wordbank_mapping(surface):
        return jsonify({'error': '映射不存在'}), 404
    logger.info("管理员删除词形映射: %s", surface)
    return jsonify({'message': '已删除映射'}), 200


@app.route('/api/admin/wordbank/troubles/difficult', methods=['DELETE'])
@admin_required
def admin_remove_wordbank_difficult():
    """从疑难词列表中移除一条（不添加映射时由管理员清理误记）。"""
    body = request.get_json(silent=True) or {}
    surface = str(body.get('surface', '')).strip()
    if not surface:
        return jsonify({'error': '缺少 surface'}), 400
    if not delete_wordbank_difficult(surface):
        return jsonify({'error': '疑难词不存在'}), 404
    logger.info("管理员删除疑难词记录: %s", surface)
    return jsonify({'message': '已移除'}), 200


@app.route('/api/admin/wordbank/csv/incremental-upload', methods=['POST'])
@admin_required
def admin_wordbank_csv_incremental_upload():
    """
    管理员上传本地 words.csv，与服务器合并：
    - 不删除仅存在于服务端的词；
    - 同一 english（忽略大小写）以上传行覆盖；
    - 上传中新增词按上传顺序接在末尾。
    """
    if 'file' not in request.files:
        return jsonify({'error': '缺少表单字段 file'}), 400
    up = request.files['file']
    if not up or not up.filename:
        return jsonify({'error': '未选择文件'}), 400
    if not str(up.filename).lower().endswith('.csv'):
        return jsonify({'error': '请上传 .csv 文件'}), 400
    raw = up.read()
    if not raw:
        return jsonify({'error': '文件为空'}), 400
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return jsonify({'error': '文件须为 UTF-8 编码'}), 400
    reader = csv.DictReader(StringIO(text))
    fieldnames = reader.fieldnames or []
    fn_norm = {str(x or "").strip() for x in fieldnames}
    if "english" not in fn_norm:
        return jsonify({'error': 'CSV 须包含 english 列'}), 400
    upload_rows: List[dict] = []
    for row in reader:
        upload_rows.append(dict(row))
    if not any(str(r.get("english", "")).strip() for r in upload_rows):
        return jsonify({'error': '上传文件无有效词条（english 列为空）'}), 400

    try:
        with _words_csv_interprocess_lock():
            with _words_csv_lock:
                server_rows = _read_words_csv_from_path(WORDS_CSV_FILE)
                merged, stats = _merge_incremental_words_csv(server_rows, upload_rows)
                _write_words_csv_rows_atomic_under_lock(merged)
    except Exception as e:
        logger.exception("增量合并 words.csv 失败: %s", e)
        return jsonify({'error': f'写入失败: {e}'}), 500

    logger.info(
        "管理员增量上传 words.csv: added=%s replaced=%s final=%s",
        stats.get("added"),
        stats.get("replaced"),
        stats.get("final_count"),
    )
    return jsonify({
        'message': '词库已增量合并',
        'stats': stats,
    }), 200


def _chat_sanitize_body(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    text = text[:2000]
    text = "".join(ch for ch in text if ch.isprintable() or ch.isspace()).strip()[:2000]
    return text


def _chat_mentionable_usernames() -> Set[str]:
    users = load_users()
    return {
        u
        for u, row in users.items()
        if _user_row_is_enabled(row)
        and not is_parent_user_record(row)
    }


def _chat_extract_mentions(body: str, valid: Set[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for m in _CHAT_MENTION_RE.finditer(body):
        u = m.group(1)
        if u in valid and u not in seen:
            seen.add(u)
            out.append(u)
    return out


@app.route("/api/chat/stream-token", methods=["POST"])
@token_required
def api_chat_stream_token(username):
    """用登录 token 换取短期 SSE token；SSE URL 不再携带长期 bearer token。"""
    if not _rate_allow(f"chat_stream_token:{username}", _RATE_MAX_CHAT_STREAM_TOKEN):
        return jsonify({"error": "聊天连接过于频繁，请稍后再试"}), 429
    login = getattr(g, "login_username", username)
    return jsonify({
        "stream_token": create_chat_stream_token(login),
        "expires_in": 120,
    }), 200


def _verify_chat_stream_auth() -> Optional[str]:
    """校验 SSE 查询参数 stream_token；成功返回 login_username。"""
    token = (request.args.get("stream_token") or "").strip()
    if not token:
        return None
    login_username = verify_session(token, SESSION_KIND_CHAT_STREAM)
    if not login_username:
        return None
    revoke_token(SESSION_KIND_CHAT_STREAM, token)
    urow = get_user(login_username)
    if not _user_row_is_enabled(urow):
        _revoke_user_tokens(login_username)
        return None
    if isinstance(urow, dict) and is_parent_user_record(urow):
        child = (urow.get("child_username") or "").strip()
        if not child or not is_valid_username(child):
            return None
        ch = get_user(child)
        if not isinstance(ch, dict) or is_parent_user_record(ch):
            return None
        if not _user_row_is_enabled(ch):
            return None
    return login_username


# ==================== 聊天室（JSONL + SSE）====================

@app.route("/api/chat/messages", methods=["GET"])
@token_required
def api_chat_messages_get(username):
    if not _rate_allow(f"chat_get:{username}", _RATE_MAX_CHAT_GET):
        return jsonify({"error": "聊天拉取过于频繁，请稍后再试"}), 429
    before_id = (request.args.get("before_id") or "").strip() or None
    after_id = (request.args.get("after_id") or "").strip() or None
    try:
        limit = int(request.args.get("limit") or 50)
    except ValueError:
        return jsonify({"error": "参数 limit 无效"}), 400
    try:
        msgs = chat_room.get_messages(before_id=before_id, after_id=after_id, limit=limit)
    except OSError as e:
        logger.warning("聊天历史读取失败: %s", e)
        return jsonify({"error": "聊天记录不可用"}), 503
    except Exception as e:
        logger.exception("聊天历史读取异常: %s", e)
        return jsonify({"error": "服务器内部错误"}), 500
    return jsonify({"messages": msgs}), 200


@app.route("/api/chat/messages", methods=["POST"])
@token_required
def api_chat_messages_post(username):
    if not _rate_allow(f"chat_post:{username}", _RATE_MAX_CHAT_POST):
        return jsonify({"error": "发送过于频繁，请稍后再试"}), 429
    data = request.get_json(silent=True) or {}
    body = _chat_sanitize_body(data.get("body") or "")
    if not body:
        return jsonify({"error": "消息不能为空"}), 400
    valid = _chat_mentionable_usernames()
    mentions = _chat_extract_mentions(body, valid)
    try:
        msg = chat_room.append_message(username, body, mentions)
    except OSError as e:
        logger.warning("聊天写入失败: %s", e)
        return jsonify({"error": "聊天服务不可用"}), 503
    except Exception as e:
        logger.exception("聊天写入异常: %s", e)
        return jsonify({"error": "服务器内部错误"}), 500
    return jsonify(msg), 201


@app.route("/api/chat/users", methods=["GET"])
@token_required
def api_chat_users_suggest(username):
    q = (request.args.get("q") or "").strip().lower()
    users = load_users()
    out: List[str] = []
    for u in sorted(users.keys()):
        if len(out) >= 20:
            break
        row = users.get(u)
        if not _user_row_is_enabled(row):
            continue
        if is_parent_user_record(row):
            continue
        if not q or u.lower().startswith(q):
            out.append(u)
    return jsonify({"users": out}), 200


@app.route("/api/chat/stream")
def api_chat_stream():
    if _verify_chat_stream_auth() is None:
        return jsonify({"error": "需要有效登录"}), 401

    return Response(
        stream_with_context(chat_room.sse_generator()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ==================== 健康检查 ====================

@app.route('/api/health', methods=['GET'])
def health():
    """健康检查"""
    return jsonify({'status': 'healthy', 'timestamp': china_now_iso(timespec="seconds")}), 200

# ==================== 启动配置 ====================

if __name__ == '__main__':
    _debug = os.getenv("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    app.run(
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        debug=_debug,
        threaded=True,
    )

"""Lightweight performance telemetry storage.

The app writes newline-delimited JSON so production data can be copied down and
analyzed with jq, pandas, or plain scripts without needing another service.
"""

from __future__ import annotations

import json
import math
import os
import random
import re
import threading
import hmac
import hashlib
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Generator, Iterable, List

from app_time import CHINA_TZ, china_now_iso, china_today


PERFORMANCE_SCHEMA = "english_reciter.performance/v1"
PERFORMANCE_LOG_PREFIX = "perf-"
PERFORMANCE_LOG_SUFFIX = ".jsonl"
PERFORMANCE_SHARE_MAX_TTL_SEC = 24 * 60 * 60

_WRITE_LOCK = threading.Lock()
_LOG_NAME_RE = re.compile(r"^perf-\d{4}-\d{2}-\d{2}\.jsonl$")
_REDACT_KEY_RE = re.compile(r"(password|passwd|pwd|token|secret|authorization|cookie|answer)", re.IGNORECASE)
_REDACT_EXACT_KEYS = {
    "body",
    "request_body",
    "response_body",
    "raw_body",
    "session",
    "session_token",
    "auth",
}


@contextmanager
def _interprocess_file_lock(lock_path: Path) -> Generator[None, None, None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    f = open(lock_path, "a+b", buffering=0)
    try:
        if os.name == "nt":
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
        if os.name == "nt":
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


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", "disabled"}


def env_int(name: str, default: int, *, min_value: int | None = None, max_value: int | None = None) -> int:
    try:
        value = int(str(os.getenv(name, "")).strip())
    except (TypeError, ValueError):
        value = default
    if min_value is not None:
        value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value


def env_float(
    name: str,
    default: float,
    *,
    min_value: float | None = None,
    max_value: float | None = None,
) -> float:
    try:
        value = float(str(os.getenv(name, "")).strip())
    except (TypeError, ValueError):
        value = default
    if min_value is not None:
        value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value


def performance_enabled() -> bool:
    return env_bool("PERF_MONITOR_ENABLED", True)


def slow_request_threshold_ms() -> int:
    return env_int("PERF_SLOW_REQUEST_MS", 1000, min_value=100, max_value=120000)


def backend_sample_rate() -> float:
    return env_float("PERF_BACKEND_SAMPLE_RATE", 0.02, min_value=0.0, max_value=1.0)


def browser_sample_rate() -> float:
    return env_float("PERF_BROWSER_SAMPLE_RATE", 1.0, min_value=0.0, max_value=1.0)


def max_report_events() -> int:
    return env_int("PERF_MAX_REPORT_EVENTS", 60, min_value=1, max_value=200)


def max_report_bytes() -> int:
    return env_int("PERF_MAX_REPORT_BYTES", 256 * 1024, min_value=1024, max_value=2 * 1024 * 1024)


def should_sample(rate: float) -> bool:
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    return random.random() < rate


def performance_log_dir(data_dir: Path | str) -> Path:
    raw = os.getenv("PERF_LOG_DIR", "").strip()
    if raw:
        return Path(raw)
    return Path(data_dir) / "_shared" / "performance"


def performance_log_path(data_dir: Path | str) -> Path:
    return performance_log_dir(data_dir) / f"{PERFORMANCE_LOG_PREFIX}{china_today().isoformat()}{PERFORMANCE_LOG_SUFFIX}"


def is_valid_performance_log_name(name: str) -> bool:
    return bool(_LOG_NAME_RE.fullmatch(name or ""))


def performance_share_secret(app_secret: str) -> bytes:
    raw = os.getenv("PERF_SHARE_SECRET", "").strip() or app_secret or ""
    if not raw:
        raw = "english-reciter-performance-share-development-secret"
    return raw.encode("utf-8")


def sign_performance_log_name(name: str, expires_at: int, app_secret: str) -> str:
    msg = f"{name}.{int(expires_at)}".encode("utf-8")
    return hmac.new(performance_share_secret(app_secret), msg, hashlib.sha256).hexdigest()


def verify_performance_share_signature(name: str, expires_at: int, sig: str, app_secret: str, now_ts: int) -> bool:
    if not is_valid_performance_log_name(name):
        return False
    if int(expires_at) < int(now_ts):
        return False
    if int(expires_at) - int(now_ts) > PERFORMANCE_SHARE_MAX_TTL_SEC:
        return False
    expected = sign_performance_log_name(name, int(expires_at), app_secret)
    return hmac.compare_digest(expected, str(sig or ""))


def list_performance_logs(data_dir: Path | str, *, limit: int = 14) -> List[Dict[str, Any]]:
    limit = max(1, min(120, int(limit or 14)))
    directory = performance_log_dir(data_dir)
    if not directory.is_dir():
        return []
    rows: List[Dict[str, Any]] = []
    for path in directory.iterdir():
        if not path.is_file() or not is_valid_performance_log_name(path.name):
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        rows.append(
            {
                "name": path.name,
                "size_bytes": st.st_size,
                "modified_at": datetime.fromtimestamp(st.st_mtime, CHINA_TZ)
                .replace(tzinfo=None)
                .isoformat(timespec="seconds"),
            }
        )
    rows.sort(key=lambda x: x["name"], reverse=True)
    return rows[:limit]


def _clean_string(value: str, max_len: int) -> str:
    cleaned = "".join(ch for ch in value if ch.isprintable() or ch in "\n\r\t")
    if len(cleaned) > max_len:
        return cleaned[:max_len] + "...[truncated]"
    return cleaned


def _should_redact_key(key: str) -> bool:
    lowered = key.strip().lower()
    return lowered in _REDACT_EXACT_KEYS or bool(_REDACT_KEY_RE.search(lowered))


def sanitize_value(value: Any, *, depth: int = 0, max_depth: int = 5) -> Any:
    if depth > max_depth:
        return None
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value, 3) if math.isfinite(value) else None
    if isinstance(value, str):
        return _clean_string(value, 1000)
    if isinstance(value, (list, tuple)):
        return [sanitize_value(v, depth=depth + 1, max_depth=max_depth) for v in list(value)[:60]]
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for raw_key, raw_val in list(value.items())[:120]:
            key = _clean_string(str(raw_key), 100)
            if not key:
                continue
            if _should_redact_key(key):
                out[key] = "[redacted]"
            else:
                out[key] = sanitize_value(raw_val, depth=depth + 1, max_depth=max_depth)
        return out
    return _clean_string(str(value), 300)


def write_performance_events(data_dir: Path | str, events: Iterable[Dict[str, Any]]) -> int:
    clean_events = [sanitize_value(ev) for ev in events if isinstance(ev, dict)]
    if not clean_events:
        return 0

    path = performance_log_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK:
        with _interprocess_file_lock(path.parent / ".performance.lock"):
            with open(path, "a", encoding="utf-8") as f:
                for ev in clean_events:
                    row = {
                        "schema": PERFORMANCE_SCHEMA,
                        "recorded_at": china_now_iso(timespec="milliseconds"),
                        "event": ev,
                    }
                    f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    return len(clean_events)

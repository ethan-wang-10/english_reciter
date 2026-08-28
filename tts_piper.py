"""本地 Piper TTS：通过官方 piper 可执行文件合成 WAV。

配置优先级：环境变量 > reciter Config（若传入 config）> 默认值。

- PIPER_MODEL：.onnx 模型文件绝对或相对路径（必填方可启用）
- PIPER_BINARY：piper 可执行文件名或路径；默认在 PATH 中查找 ``piper`` / ``piper.exe``
"""

from __future__ import annotations

import logging
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator, Optional

logger = logging.getLogger(__name__)

_cache_locks_guard = threading.Lock()
_cache_locks: Dict[str, tuple[threading.Lock, int]] = {}
_cache_cleanup_lock = threading.Lock()
_last_cache_cleanup = 0.0
_result_local = threading.local()


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, '')).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _cache_directory() -> Path:
    raw = os.getenv('PIPER_CACHE_DIR', '').strip()
    if raw:
        return Path(raw).expanduser()
    data_dir = Path(os.getenv('ENGLISH_RECITER_DATA_DIR', 'user_data_simple')).expanduser()
    return data_dir / '_shared' / 'tts_cache'


def _model_identity(model_path: Path, binary: str) -> str:
    parts = [str(model_path.resolve()), str(binary)]
    for path in (model_path, model_path.with_suffix(model_path.suffix + '.json')):
        try:
            stat = path.stat()
            parts.extend((str(stat.st_size), str(stat.st_mtime_ns)))
        except OSError:
            parts.extend(('0', '0'))
    return '|'.join(parts)


def _cache_key(model_path: Path, binary: str, text: str) -> str:
    normalized = ' '.join(str(text or '').split())
    source = f"{_model_identity(model_path, binary)}\0{normalized}".encode('utf-8')
    return hashlib.sha256(source).hexdigest()


@contextmanager
def _key_lock(key: str) -> Generator[None, None, None]:
    with _cache_locks_guard:
        lock, refs = _cache_locks.get(key, (threading.Lock(), 0))
        _cache_locks[key] = (lock, refs + 1)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()
        with _cache_locks_guard:
            current = _cache_locks.get(key)
            if current and current[0] is lock:
                if current[1] <= 1:
                    _cache_locks.pop(key, None)
                else:
                    _cache_locks[key] = (lock, current[1] - 1)


def _cleanup_cache(directory: Path, *, force: bool = False) -> None:
    global _last_cache_cleanup
    now = time.time()
    if not force and now - _last_cache_cleanup < 300:
        return
    if not _cache_cleanup_lock.acquire(blocking=False):
        return
    try:
        _last_cache_cleanup = now
        if not directory.is_dir():
            return
        max_age = _env_int('PIPER_CACHE_MAX_AGE_DAYS', 30, 1, 365) * 86400
        max_files = _env_int('PIPER_CACHE_MAX_FILES', 2000, 10, 50000)
        max_bytes = _env_int(
            'PIPER_CACHE_MAX_BYTES', 512 * 1024 * 1024, 1024 * 1024, 10 * 1024 * 1024 * 1024
        )
        rows = []
        for path in directory.glob('*.wav'):
            try:
                stat = path.stat()
                if now - stat.st_mtime > max_age:
                    path.unlink(missing_ok=True)
                    continue
                rows.append((stat.st_mtime, stat.st_size, path))
            except OSError:
                continue
        rows.sort()
        total = sum(row[1] for row in rows)
        while rows and (len(rows) > max_files or total > max_bytes):
            _, size, path = rows.pop(0)
            try:
                path.unlink(missing_ok=True)
                total -= size
            except OSError:
                pass
    finally:
        _cache_cleanup_lock.release()


def piper_last_result_metadata() -> Dict[str, Any]:
    return {
        'cache_hit': bool(getattr(_result_local, 'cache_hit', False)),
        'duration_ms': float(getattr(_result_local, 'duration_ms', 0.0)),
    }


def _resolve_piper_binary(explicit: str = "") -> Optional[str]:
    raw = (explicit or os.environ.get("PIPER_BINARY") or "").strip() or "piper"
    p = Path(raw)
    if p.is_file():
        return str(p.resolve())
    w = shutil.which(raw)
    if w:
        return w
    if sys.platform == "win32":
        w = shutil.which(raw + ".exe")
        if w:
            return w
    return None


def _resolve_model_path(config: Any = None) -> str:
    env = (os.environ.get("PIPER_MODEL") or "").strip()
    if env:
        return env
    if config is not None:
        v = getattr(config, "PIPER_MODEL", None) or ""
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _resolve_binary_for_config(config: Any = None) -> str:
    env = (os.environ.get("PIPER_BINARY") or "").strip()
    if env:
        return env
    if config is not None:
        v = getattr(config, "PIPER_BINARY", None) or ""
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def piper_runtime_ready(config: Any = None) -> bool:
    """模型文件存在且 piper 可执行文件可用。"""
    mp = _resolve_model_path(config)
    if not mp:
        return False
    if not Path(mp).is_file():
        return False
    return _resolve_piper_binary(_resolve_binary_for_config(config)) is not None


def piper_synthesize_wav(safe_text: str, config: Any = None) -> Optional[bytes]:
    """将已清理的文本合成为 WAV 字节；失败返回 None。"""
    if not safe_text:
        return None
    model = _resolve_model_path(config)
    if not model or not Path(model).is_file():
        return None
    binary = _resolve_piper_binary(_resolve_binary_for_config(config))
    if not binary:
        logger.debug("未找到 piper 可执行文件")
        return None

    started = time.perf_counter()
    _result_local.cache_hit = False
    _result_local.duration_ms = 0.0
    resolved_model = Path(model).resolve()
    model_path = str(resolved_model)
    cache_dir = _cache_directory()
    key = _cache_key(resolved_model, binary, safe_text)
    cache_path = cache_dir / f'{key}.wav'
    with _key_lock(key):
        try:
            cached = cache_path.read_bytes()
            if len(cached) >= 100:
                os.utime(cache_path, None)
                _result_local.cache_hit = True
                _result_local.duration_ms = round((time.perf_counter() - started) * 1000, 1)
                return cached
        except (FileNotFoundError, OSError):
            pass
        cache_dir.mkdir(parents=True, exist_ok=True)
        _cleanup_cache(cache_dir)
        return _synthesize_and_cache(
            safe_text,
            binary=binary,
            model_path=model_path,
            cache_path=cache_path,
            started=started,
        )


def _synthesize_and_cache(
    safe_text: str,
    *,
    binary: str,
    model_path: str,
    cache_path: Path,
    started: float,
) -> Optional[bytes]:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        out_path = tmp.name
    try:
        proc = subprocess.run(
            [binary, "--model", model_path, "--output_file", out_path],
            input=safe_text.encode("utf-8"),
            capture_output=True,
            timeout=90,
        )
        if proc.returncode != 0:
            err = (proc.stderr or b"").decode("utf-8", errors="replace")[:400]
            logger.warning("piper 退出码 %s: %s", proc.returncode, err)
            return None
        p = Path(out_path)
        if not p.is_file() or p.stat().st_size < 100:
            logger.warning("piper 输出 WAV 无效或过小")
            return None
        wav = p.read_bytes()
        fd, cache_tmp = tempfile.mkstemp(
            prefix=f'.{cache_path.stem}.',
            suffix='.tmp',
            dir=str(cache_path.parent),
        )
        try:
            with os.fdopen(fd, 'wb') as target:
                target.write(wav)
                target.flush()
                os.fsync(target.fileno())
            os.replace(cache_tmp, cache_path)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                Path(cache_tmp).unlink(missing_ok=True)
            except OSError:
                pass
            logger.warning("Piper 缓存写入失败", exc_info=True)
        _result_local.duration_ms = round((time.perf_counter() - started) * 1000, 1)
        return wav
    except subprocess.TimeoutExpired:
        logger.warning("piper 合成超时")
        return None
    except OSError as e:
        logger.warning("piper 执行失败: %s", e)
        return None
    finally:
        _result_local.duration_ms = round((time.perf_counter() - started) * 1000, 1)
        try:
            Path(out_path).unlink(missing_ok=True)
        except OSError:
            pass


def play_wav_bytes(wav: bytes) -> None:
    """播放 WAV 字节（写入临时文件后调用系统播放器）。"""
    if not wav:
        return
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(wav)
        path = tmp.name
    try:
        play_wav_path(path)
    finally:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass


def play_wav_path(path: str) -> None:
    """使用系统能力播放 WAV 文件。"""
    if not path or not Path(path).is_file():
        return
    if sys.platform == "win32":
        import winsound

        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_SYNC)
        return
    if sys.platform == "darwin":
        subprocess.run(["afplay", path], capture_output=True, timeout=180)
        return
    for cmd in (["paplay", path], ["aplay", "-q", path]):
        exe = cmd[0]
        if shutil.which(exe):
            subprocess.run(cmd, capture_output=True, timeout=180)
            return
    ff = shutil.which("ffplay")
    if ff:
        subprocess.run(
            [ff, "-nodisp", "-autoexit", "-loglevel", "quiet", path],
            capture_output=True,
            timeout=180,
        )
        return
    logger.debug("未找到 paplay/aplay/ffplay，无法播放 WAV")

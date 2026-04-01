"""本地 Piper TTS：通过官方 piper 可执行文件合成 WAV。

配置优先级：环境变量 > reciter Config（若传入 config）> 默认值。

- PIPER_MODEL：.onnx 模型文件绝对或相对路径（必填方可启用）
- PIPER_BINARY：piper 可执行文件名或路径；默认在 PATH 中查找 ``piper`` / ``piper.exe``
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


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

    model_path = str(Path(model).resolve())
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
        return p.read_bytes()
    except subprocess.TimeoutExpired:
        logger.warning("piper 合成超时")
        return None
    except OSError as e:
        logger.warning("piper 执行失败: %s", e)
        return None
    finally:
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

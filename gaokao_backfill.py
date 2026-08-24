"""Shared coordination helpers for automatic Gaokao question backfill."""

from __future__ import annotations

import errno
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional

import gaokao_questions


GENERATION_LOCK_FILE = (
    gaokao_questions.DATA_DIR / "_shared" / ".gaokao_backfill.lock"
)
AUTO_STATE_FILE = (
    gaokao_questions.DATA_DIR / "_shared" / "gaokao_backfill_state.json"
)


def is_deepseek_off_peak(moment: Optional[datetime] = None) -> bool:
    """Return whether DeepSeek currently charges off-peak rates.

    DeepSeek defines weekday 01:00-04:00 and 06:00-10:00 UTC as peak
    periods. Weekends and all remaining weekday hours are off-peak.
    """
    current = moment or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current_utc = current.astimezone(timezone.utc)
    if current_utc.weekday() >= 5:
        return True
    minute_of_day = current_utc.hour * 60 + current_utc.minute
    return not (
        60 <= minute_of_day < 240
        or 360 <= minute_of_day < 600
    )


@contextmanager
def generation_job_lock(
    *,
    blocking: bool = False,
    lock_file: Optional[Path] = None,
) -> Generator[bool, None, None]:
    """Try to hold the cross-process generation lock for an entire AI job."""
    path = lock_file or GENERATION_LOCK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b", buffering=0)
    acquired = False
    try:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
            try:
                msvcrt.locking(handle.fileno(), mode, 1)
                acquired = True
            except OSError as exc:
                if blocking or exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
        else:
            import fcntl

            mode = fcntl.LOCK_EX
            if not blocking:
                mode |= fcntl.LOCK_NB
            try:
                fcntl.flock(handle.fileno(), mode)
                acquired = True
            except BlockingIOError:
                acquired = False
        yield acquired
    finally:
        if acquired:
            if sys.platform == "win32":
                import msvcrt

                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl

                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
        handle.close()


def load_auto_state(state_file: Optional[Path] = None) -> dict:
    path = state_file or AUTO_STATE_FILE
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def save_auto_state(state: dict, state_file: Optional[Path] = None) -> None:
    path = state_file or AUTO_STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def seconds_since_last_start(state: dict, now: datetime) -> Optional[float]:
    raw = str(state.get("last_started_at") or "").strip()
    if not raw:
        return None
    try:
        previous = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if previous.tzinfo is None:
        previous = previous.replace(tzinfo=timezone.utc)
    current = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    elapsed = (
        current.astimezone(timezone.utc) - previous.astimezone(timezone.utc)
    ).total_seconds()
    return max(0.0, elapsed)

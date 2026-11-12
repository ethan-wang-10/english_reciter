"""Small primitives for JSON state shared by multiple Gunicorn workers."""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator


class PersistentStateLoadError(RuntimeError):
    """Existing state could not be read safely and must not be overwritten."""


def read_json_state(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as source:
            value = json.load(source)
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as exc:
        raise PersistentStateLoadError(f"Cannot safely read persisted state: {path}") from exc
    if not isinstance(value, type(default)):
        raise PersistentStateLoadError(f"Invalid persisted state root: {path}")
    return value


def preserve_file_metadata(path: Path, original: os.stat_result, *, file_descriptor: int | None = None) -> None:
    if file_descriptor is not None and hasattr(os, "fchown"):
        os.fchown(file_descriptor, original.st_uid, original.st_gid)
    elif hasattr(os, "chown"):
        os.chown(path, original.st_uid, original.st_gid)
    if file_descriptor is not None and hasattr(os, "fchmod"):
        os.fchmod(file_descriptor, stat.S_IMODE(original.st_mode))
    else:
        os.chmod(path, stat.S_IMODE(original.st_mode))


def fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def interprocess_file_lock(lock_path: Path) -> Generator[None, None, None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+b", buffering=0)
    try:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
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


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(suffix=".json", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise

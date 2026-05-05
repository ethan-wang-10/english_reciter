"""全站聊天室：JSONL 持久化 + 进程内 SSE 广播（多 worker 不跨进程）。"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional

from app_time import china_now_iso

DATA_DIR = Path("user_data_simple")
SHARED_DATA_DIR = DATA_DIR / "_shared"
CHAT_JSONL = SHARED_DATA_DIR / "chat_messages.jsonl"
CHAT_LOCKFILE = SHARED_DATA_DIR / ".chat_messages.jsonl.lock"

MAX_JSONL_BYTES = 32 * 1024 * 1024

_thread_lock = threading.Lock()
_subscribers: List[queue.Queue] = []
_subscribers_lock = threading.Lock()


@contextmanager
def _chat_interprocess_lock():
    SHARED_DATA_DIR.mkdir(parents=True, exist_ok=True)
    f = open(CHAT_LOCKFILE, "a+b", buffering=0)
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


def _iso_ts() -> str:
    return china_now_iso(timespec="seconds")


def _parse_line(line: str) -> Optional[dict]:
    line = line.strip()
    if not line:
        return None
    try:
        o = json.loads(line)
        if not isinstance(o, dict):
            return None
        if not all(k in o for k in ("id", "ts", "room", "from", "body", "mentions")):
            return None
        if not isinstance(o.get("mentions"), list):
            return None
        return o
    except json.JSONDecodeError:
        return None


def _read_all_messages() -> List[dict]:
    if not CHAT_JSONL.exists():
        return []
    sz = CHAT_JSONL.stat().st_size
    if sz > MAX_JSONL_BYTES:
        raise OSError("chat log exceeds size limit")
    with _chat_interprocess_lock():
        with _thread_lock:
            raw = CHAT_JSONL.read_text(encoding="utf-8")
    out: List[dict] = []
    for line in raw.splitlines():
        m = _parse_line(line)
        if m:
            out.append(m)
    return out


def _sort_key(msg: dict) -> tuple:
    return (str(msg.get("ts") or ""), str(msg.get("id") or ""))


def get_messages(
    before_id: Optional[str] = None,
    after_id: Optional[str] = None,
    limit: int = 50,
) -> List[dict]:
    rows = _read_all_messages()
    rows.sort(key=_sort_key)
    if not rows:
        return []
    limit = max(1, min(int(limit), 200))
    by_id: Dict[str, int] = {str(m["id"]): i for i, m in enumerate(rows) if m.get("id")}

    if after_id:
        idx = by_id.get(after_id)
        if idx is None:
            return []
        return rows[idx + 1 : idx + 1 + limit]

    if before_id:
        idx = by_id.get(before_id)
        if idx is None:
            return []
        start = max(0, idx - limit)
        return rows[start:idx]

    if len(rows) <= limit:
        return rows
    return rows[-limit:]


def broadcast_sse(msg: dict) -> None:
    data = json.dumps(msg, ensure_ascii=False)
    chunk = f"event: message\ndata: {data}\n\n"
    with _subscribers_lock:
        for q in _subscribers:
            try:
                q.put_nowait(chunk)
            except queue.Full:
                pass


def append_message(sender: str, body: str, mentions: List[str]) -> dict:
    msg = {
        "id": uuid.uuid4().hex,
        "ts": _iso_ts(),
        "room": "global",
        "from": sender,
        "body": body,
        "mentions": mentions,
    }
    line = json.dumps(msg, ensure_ascii=False) + "\n"
    with _chat_interprocess_lock():
        with _thread_lock:
            SHARED_DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(CHAT_JSONL, "a", encoding="utf-8") as f:
                f.write(line)
    broadcast_sse(msg)
    return msg


def sse_generator():
    q: queue.Queue = queue.Queue(maxsize=64)
    with _subscribers_lock:
        _subscribers.append(q)
    try:
        ready = json.dumps({"server_time": _iso_ts()}, ensure_ascii=False)
        yield f"event: ready\ndata: {ready}\n\n"
        while True:
            try:
                item = q.get(timeout=25.0)
                yield item
            except queue.Empty:
                ping = json.dumps({"t": int(time.time())}, ensure_ascii=False)
                yield f"event: ping\ndata: {ping}\n\n"
    finally:
        with _subscribers_lock:
            try:
                _subscribers.remove(q)
            except ValueError:
                pass

"""Bounded sliding-window limits shared by workers on one application host."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3


class RateLimitStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    def allow(self, key: str, maximum: int, *, now: float, window: int = 60,
              max_buckets: int = 10000) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(str(self.path), timeout=5)) as connection:
            connection.execute('PRAGMA busy_timeout=5000')
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('PRAGMA synchronous=NORMAL')
            connection.execute('''CREATE TABLE IF NOT EXISTS rate_buckets (
                key TEXT PRIMARY KEY, events TEXT NOT NULL, latest REAL NOT NULL)''')
            connection.execute('CREATE INDEX IF NOT EXISTS rate_expiry ON rate_buckets(latest)')
            connection.execute('BEGIN IMMEDIATE')
            try:
                cutoff = now - window
                connection.execute('DELETE FROM rate_buckets WHERE latest<=?', (cutoff,))
                row = connection.execute('SELECT events FROM rate_buckets WHERE key=?', (key,)).fetchone()
                events = [value for value in json.loads(row[0]) if value > cutoff] if row else []
                if row is None:
                    count = connection.execute('SELECT COUNT(*) FROM rate_buckets').fetchone()[0]
                    if count >= max_buckets:
                        connection.commit()
                        return False
                if len(events) >= maximum:
                    connection.commit()
                    return False
                events.append(now)
                connection.execute('''INSERT INTO rate_buckets(key, events, latest) VALUES(?,?,?)
                    ON CONFLICT(key) DO UPDATE SET events=excluded.events, latest=excluded.latest''',
                    (key, json.dumps(events, separators=(',', ':')), now))
                connection.commit()
                return True
            except BaseException:
                connection.rollback()
                raise

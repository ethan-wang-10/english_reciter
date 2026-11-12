from concurrent.futures import ThreadPoolExecutor
import sqlite3

from rate_limit_store import RateLimitStore


def test_workers_share_one_limit_and_expired_capacity_is_reused(tmp_path):
    path = tmp_path / 'limits.sqlite3'
    first, second = RateLimitStore(path), RateLimitStore(path)
    assert first.allow('a', 1, now=100, max_buckets=2)
    assert not second.allow('a', 1, now=101, max_buckets=2)
    assert second.allow('b', 1, now=101, max_buckets=2)
    assert not first.allow('c', 1, now=102, max_buckets=2)
    assert first.allow('c', 1, now=161, max_buckets=2)
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT key FROM rate_buckets').fetchall() == [('c',)]


def test_concurrent_connections_cannot_exceed_shared_limit(tmp_path):
    path = tmp_path / 'limits.sqlite3'
    RateLimitStore(path).allow('warmup', 1, now=100)
    def request(_):
        return RateLimitStore(path).allow('same-source', 7, now=100)
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(request, range(30))) == 7

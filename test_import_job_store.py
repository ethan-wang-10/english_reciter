import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest

from import_job_store import ImportJobStore, ImportLeaseLost, ImportOutcomeUnknown


def test_job_lifecycle_is_persisted_and_owner_scoped(tmp_path) -> None:
    path = tmp_path / 'jobs.sqlite3'
    first = ImportJobStore(path)
    queued = first.enqueue('vocab_import', 'alice', {'words': 'apple'})

    second = ImportJobStore(path)
    assert second.get(queued['job_id'], 'bob') is None
    assert second.get(queued['job_id'], 'alice')['status'] == 'queued'

    claimed = second.claim_next()
    assert claimed['job_id'] == queued['job_id']
    assert claimed['payload'] == {'words': 'apple'}
    second.complete(claimed['job_id'], {'new_in_csv': 1}, attempt=claimed['attempts'])

    done = first.get(queued['job_id'], 'alice')
    assert done['status'] == 'succeeded'
    assert done['result']['new_in_csv'] == 1


def test_connections_close_after_job_operations_and_errors(tmp_path, monkeypatch):
    connections = []
    original_connect = sqlite3.connect
    owner_thread = threading.get_ident()

    def connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        if threading.get_ident() == owner_thread:
            connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, 'connect', connect)
    store = ImportJobStore(tmp_path / 'jobs.sqlite3')
    job = store.enqueue('vocab_import', 'alice', {})
    store.get(job['job_id'])
    store.claim_next()
    assert store.renew_lease(job['job_id'], 1)
    store.fail(job['job_id'], 'failure', attempt=1)
    store.cleanup()
    with pytest.raises(TypeError):
        store.enqueue('vocab_import', 'alice', {'invalid': object()})
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            connection.execute('SELECT 1')


def test_heartbeat_preserves_long_running_job_and_fences_stale_attempt(tmp_path):
    store = ImportJobStore(tmp_path / 'jobs.sqlite3')
    queued = store.enqueue('vocab_import', 'alice', {})
    first = store.claim_next()
    old = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(timespec='seconds')
    with store._connect() as connection:
        connection.execute('UPDATE import_jobs SET started_at=?, updated_at=? WHERE id=?',
                           (old, old, queued['job_id']))

    assert store.renew_lease(first['job_id'], first['attempts'])
    assert store.claim_next() is None
    with store._connect() as connection:
        connection.execute('UPDATE import_jobs SET updated_at=? WHERE id=?',
                           (old, queued['job_id']))
    second = store.claim_next()
    assert second['attempts'] == first['attempts'] + 1
    assert not store.renew_lease(first['job_id'], first['attempts'])
    assert not store.complete(first['job_id'], {'stale': True}, attempt=first['attempts'])
    assert not store.fail(first['job_id'], 'stale failure', attempt=first['attempts'])
    assert store.get(first['job_id'])['status'] == 'running'
    assert store.complete(second['job_id'], {'current': True}, attempt=second['attempts'])
    assert store.get(second['job_id'])['result'] == {'current': True}


def test_completed_provider_result_is_reused_after_reclaim(tmp_path):
    store = ImportJobStore(tmp_path / 'jobs.sqlite3')
    store.enqueue('vocab_import', 'alice', {})
    first = store.claim_next()
    calls = []
    result = store.external_step(first['job_id'], first['attempts'], 'request-1',
                                 lambda: calls.append(1) or {'content': 'saved'})
    old = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(timespec='seconds')
    with store._connect() as connection:
        connection.execute('UPDATE import_jobs SET updated_at=? WHERE id=?', (old, first['job_id']))
    second = store.claim_next()
    assert store.external_step(second['job_id'], second['attempts'], 'request-1',
                               lambda: pytest.fail('paid request must not replay')) == result
    assert calls == [1]
    with pytest.raises(ImportLeaseLost):
        store.external_step(first['job_id'], first['attempts'], 'another', lambda: None)


def test_interrupted_paid_request_is_not_reclaimed_or_replayed(tmp_path):
    store = ImportJobStore(tmp_path / 'jobs.sqlite3')
    store.enqueue('vocab_import', 'alice', {})
    job = store.claim_next()
    def interrupted():
        raise RuntimeError('response lost')
    with pytest.raises(RuntimeError, match='response lost'):
        store.external_step(job['job_id'], job['attempts'], 'request-1', interrupted)
    with pytest.raises(ImportOutcomeUnknown):
        store.external_step(job['job_id'], job['attempts'], 'request-1',
                            lambda: pytest.fail('must not repeat uncertain request'))
    old = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(timespec='seconds')
    with store._connect() as connection:
        connection.execute('UPDATE import_jobs SET updated_at=? WHERE id=?', (old, job['job_id']))
    assert store.claim_next() is None
    assert store.get(job['job_id'])['status'] == 'failed'
    with pytest.raises(ImportLeaseLost):
        store.require_owner(job['job_id'], job['attempts'])

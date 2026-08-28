from import_job_store import ImportJobStore


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
    second.complete(claimed['job_id'], {'new_in_csv': 1})

    done = first.get(queued['job_id'], 'alice')
    assert done['status'] == 'succeeded'
    assert done['result']['new_in_csv'] == 1

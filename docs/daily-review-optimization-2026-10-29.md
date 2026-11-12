# Daily Review Optimization, 2026-10-29

## Scope

This iteration targets daily review workloads up to 1,000 assigned words. It
retains per-user SQLite, synchronous answer persistence and the current settings
limit. The 1,000-item benchmark uses an isolated persisted task; it does not
change the product's 300-item settings limit.

## Changes

- Build pending/mastered word indexes once per plan or due-count operation.
  Preserve first-match lookup and object identity for mastered membership.
  Discard indexes and normalized-state references when the operation ends.
- Normalize a word's review state once within that operation. Subsequent calls
  still validate replaced or directly modified state on a fresh operation.
- Refresh existing tasks by counting all eligible candidate keys, while only
  describing and sorting potential new insertions. Preserve backlog accounting,
  weak-word rules, started items, task identities and new-word preferences.
- Skip future maintenance evaluation for unique word keys. Duplicate keys retain
  the original state-initialization order and complete candidate classification.
- Compare SQLite save content in a read snapshot. A genuine no-op avoids state
  compression and the write reservation. A changed save rechecks current rows
  and state under the write lock before its atomic commit.

There is no schema migration or asynchronous write buffering. Serialization and
hashing still process the full state on each save. Per-word incremental storage
and splitting the singleton state BLOB remain a separate, higher-risk phase.

## Verification

- Authoritative Linux regression: 889 tests and 12 subtests passed. Three Node
  tests skipped in the non-login PATH were rerun successfully in the login PATH.
- Four added storage regressions cover no-op saves while a writer holds the
  lock, nested mutation, earlier external commits and the snapshot-to-write race.
- Fourteen added read-path regressions cover duplicate keys, list replacement,
  reordering, rename, bucket moves, state mutation, cleanup on exceptions, imports,
  started-task protection, backlog changes and day rollover.
- Benchmarks use separate temporary accounts, databases, sessions and rewards.
  They verify answer replay and reload persisted progress from SQLite.

## Measurements

The before/after JSON reports contain module SHA-256 hashes and every sample:

- `daily-review-benchmark-before-2026-10-29.json`
- `daily-review-benchmark-after-2026-10-29.json`

Both runs use the same Linux host and Python 3.11.6, seven sequential samples per
operation, twenty synthetic history events per word, half pending and half
mastered. All HTTP exercises use spelling. HTTP measurements use Flask's test
client and include authentication, locks, storage, rewards and gzip, without
TCP/TLS, browser rendering or concurrent network load. Sample p95 is not a
production service-level guarantee.

| Total Words / Daily Task | Review GET p50 Before / After | Answer POST p50 Before / After |
| --- | --- | --- |
| 1,000 / 100 | 254 / 138 ms | 212 / 204 ms |
| 2,000 / 100 | 563 / 256 ms | 407 / 374 ms |
| 2,000 / 1,000 | 905 / 608 ms | 406 / 408 ms |

Warm plan calculation fell from approximately 65 to 1.4 ms, 149 to 2.4 ms and
384 to 6.2 ms respectively. Answer persistence remains dominated by encoding and
writing the full state, and is approximately unchanged at the largest task.
Single-word save medians increased by 0.8% and 4.7% in the first two cases and
decreased in the third. These small samples do not establish a universal save
speedup; the changed-save path still performs an additional metadata recheck.

## Deployment

Deployment verification is recorded separately in
`daily-review-deployment-verification-2026-10-29.json`. Publication changes only
the two business modules and their three test/benchmark support files. Config,
hashed assets, user data and the active Python environment are preserved.
The temporary source rollback backup is removed only after public health,
authentication, asset hashes and database integrity checks pass.

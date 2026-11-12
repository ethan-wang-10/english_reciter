# User Store, Spelling Order, and Dependency Review

This supplements the main code review with reproducible measurements and dependency
evidence collected on 2026-10-29. It does not establish a whole-system reliability
percentage or replace production load tests.

## User Transactions

`mutate_users(callback, usernames=[...])` reads only selected primary keys and writes
only changed records. The original callback API remains available, with delta writes.
`get_email_owner` uses an indexed, stripped and casefolded email key. A SQLite unique
index provides the final guarantee when independent connections compete for an email.
Both the callback and its reads/writes run under `BEGIN IMMEDIATE`.

Failed writes now raise instead of silently reporting success. Failed transactions
always roll back, including the legacy `rollback_on_error=False` spelling. A callback
cannot insert outside its declared username scope. Full replacement and simultaneous
email swaps retain their original dictionary semantics.

Migration rejects existing normalized email conflicts, malformed legacy JSON, and
non-object account records. It preserves source data and rolls back partial imports;
it never chooses an account to lose its email. Resolve reported legacy conflicts
before startup. Update all application writers together; do not mix old/new versions.

Python 3.12.14, generated accounts, one warmup plus seven alternating measurements:

| Accounts | Original Median | Scoped Median | Equal Final State |
| --- | ---: | ---: | --- |
| 100 | 2.218 ms | 0.192 ms | Yes |
| 10,000 | 257.870 ms | 0.219 ms | Yes |

These are local function measurements with SQLite WAL, not HTTP percentiles or
concurrent production latency. SQL trace tests additionally verify that scoped
updates do not read or rewrite unrelated accounts. Independent connections test
lost-update prevention and concurrent email claims.

Reproduce with `python scripts/benchmark_user_mutation.py --output results.json`.
Raw samples: [user-store-benchmark-2026-10-29.json](user-store-benchmark-2026-10-29.json).

## Spelling Order

The original all-pairs calculation remains the ordering model. The implementation
prepares normalized keys once, uses exact lower bounds to skip impossible matches,
caches repeated pairs locally, and delegates optimal string alignment distance to
RapidFuzz's established OSA implementation. Missing RapidFuzz falls back to the
equivalent three-row Python implementation, with lower memory use than the original
full matrix. Production must install the declared dependency for the measured speed.

The original metric and optimized/native paths are compared exhaustively over
14,641 short-string pairs and 2,005 additional deterministic score cases. Benchmark
outputs are compared element by element, including duplicates and stable tie order.

Python 3.12.14, RapidFuzz 3.14.6, 300 strings per group, one warmup and three alternating
timed rounds, against Git commit `2e75d12c00e142df9a8bd5235e00e0f52ef86f9d`:

| Synthetic Dataset | Original Median | Optimized Median | Ratio |
| --- | ---: | ---: | ---: |
| Unrelated strings, 5-15 letters | 5,661.50 ms | 327.02 ms | 17.31x |
| 100 roots with three word forms each | 2,922.35 ms | 371.36 ms | 7.87x |
| Long phrases, 100 unique strings repeated three times | 61,576.40 ms | 194.78 ms | 316.14x |

These generated inputs exercise different algorithmic shapes; they are not a sample
of production user vocabulary. The phrase case benefits substantially from repeated
pair caching. Medians do not establish a production p95 or a capacity limit.

Reproduce after dependency installation with
`python scripts/benchmark_spelling_order.py --rounds 3 --output results.json`.
Raw samples: [spelling-benchmark-2026-10-29.json](spelling-benchmark-2026-10-29.json).

## Dependency Evidence

PyPI's version JSON exposes OSV advisory identifiers, aliases, fix versions, and
source links. The audit reads those structured fields and excludes withdrawn
advisories. It identified concrete advisories affecting the previous pins or allowed
lower bounds, then checked the selected replacements individually:

| Package | Previous Declaration | Updated Declaration |
| --- | --- | --- |
| Flask | 3.0.0 | 3.1.3 |
| Werkzeug | 3.0.1 | 3.1.9 |
| Flask-CORS | 4.0.0 | 6.0.5 |
| gunicorn | 21.2.0 | 23.0.0 |
| certifi | >=2024.8.30 | >=2026.10.23 |
| cryptography | >=42.0.0 | >=50.0.2 |
| nltk | 3.8.1 | 3.10.4 |
| Pillow | 12.0.0 | 13.0.0 |
| rapidfuzz | Not declared | 3.14.6 |

The initial cryptography candidate 49.0.0 was rejected because its own metadata
reported `GHSA-g6cj-pr64-35w5`; the final candidate 50.0.2 returned no advisories.
Unchanged direct dependencies were also checked. All 16 final direct pins/floors
returned zero unwithdrawn advisories at collection time. Ranges were audited at their
allowed minimum, not resolved as an installed lockfile. This is not a transitive
dependency audit and does not prove the absence of undisclosed vulnerabilities.

The application now requires Python 3.11 or later because of Pillow/RapidFuzz. The
patched Windows wheels and pure Python packages were installed into an isolated
Python 3.12 directory for regression tests. Gunicorn and the complete native OCR/NLP
dependency graph were then installed on the Linux deployment host. The final suite
passed 871 tests plus 12 subtests; RapidOCR inference, Piper WAV synthesis and
spaCy lemmatization passed with real models.
The application uses NLTK WordNet rather than Punkt tokenizers; no Punkt migration
is required by its current call sites.

Reproduce with
`python scripts/audit_dependency_versions.py --output direct-dependency-audit.json`.
Use `pip-audit` against the complete resolved deployment environment as an additional
check before release. The script itself only reads public metadata and writes its
requested report; it does not install packages.

Evidence:

- [Original declarations and advisory identifiers](dependency-audit-before-2026-10-29.json)
- [Final direct declarations and collection metadata](dependency-audit-after-2026-10-29.json)
- [Selected versions, Python constraints, and dependency metadata](dependency-audit-candidates-2026-10-29.json)
- [Installed Linux environment audit](installed-dependency-audit-2026-10-29.json):
  96 auditable packages, no known vulnerabilities after pip/setuptools upgrades;
  the local `en-core-web-sm 3.8.0` model is absent from PyPI and was skipped by
  advisory lookup, with inference verified separately.

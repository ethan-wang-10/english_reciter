# Question Authoring Archive

`question-authoring-20260912.tar.gz` preserves the local question-authoring work
stopped at the user's request on 2026-09-12. At the user's request, the unpacked
working directory is removed after the verified archive is committed and pushed.
Restored working files are ignored by Git.

At shutdown, the production wordbank had 13,588 words: 12,957 published and 631
unpublished. This continuation published 645 words. `emis` remains excluded at
the user's request; the other 630 words are deferred to later work.

The archive includes manuscripts, reviews, requests, IDs, receipts, snapshots,
scripts, and an `ARCHIVE_MANIFEST.json` with per-file SHA256 and metadata. Python
bytecode, OS metadata, and administrator token files are excluded.

Verified contents: 9,702 files, 106,742,990 uncompressed bytes. The archive is
22,254,124 bytes (approximately 21.2 MiB).

Extract into an empty directory to inspect it, or restore its members under
`output/question-authoring/` to recover the original layout. Read
`all-20260910/CLOSEOUT-20260912.md` before resuming. Existing DeepSeek scripts skip
external-owned records, so a later run needs an explicit ownership handover that
preserves the existing drafts; `--force` alone does not perform that handover.

The adjacent `.sha256` file verifies the compressed archive; the archive's internal
manifest verifies every restored file. This is a local authoring-material archive,
not a backup of the production learning databases.

## Root scratch cleanup (2026-11-14)

`question-authoring-scratch-20261114.tar.gz` preserves 219 previously untracked
files left in the main checkout: 110 audit input/correction JSON files, 108
`.tmp` scripts, and `rebuild_0843_0845.py`. They are historical authoring materials,
not application source dependencies. They are separate from the September archive;
identical copies were not found in that archive or the authoring output tree.

The archive contains `files/` with the original root filenames and an
`ARCHIVE_MANIFEST.json` recording each path, byte count, SHA-256, and original
modification time. It excludes runtime user data, wordbank backups and credentials.
All 219 members were verified against the originals before root cleanup. The
adjacent `.sha256` file verifies the compressed archive.

Extract into an empty directory to inspect or recover individual files. Archived
remote-maintenance and submission scripts are historical records: inspect and adapt
them before use; do not run them as part of the application or deployment.

The same cleanup removes 12 PDF output copies whose bytes match tracked published
PDFs, five unreferenced intermediate hashed assets and one empty `?` file. The
wordbank maintenance backup is kept locally under `backups/cleanup-20261114/`,
which is excluded from Git. Application source, current assets, published PDFs,
existing archives and learning data remain in place.

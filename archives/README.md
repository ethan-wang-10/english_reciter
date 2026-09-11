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

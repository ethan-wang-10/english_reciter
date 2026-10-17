#!/usr/bin/env python3
"""Remove source rows and question records whose only sense is a personal name.

Run without ``--apply`` to inspect the exact keys. The script deliberately keeps
ordinary words such as ``surname`` and ambiguous person/place entries for an
editorial decision.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import gaokao_questions as questions
import simple_web_app as web
import wordbank_v2


QUESTION_NAMESPACES = ("questions", "candidates", "rejections", "failures")


def _selected_keys(csv_rows: list[dict], v2_rows: list[dict]) -> set[str]:
    keys: set[str] = set()
    for row in csv_rows:
        key = wordbank_v2.normalize_english_key(row.get("english", ""))
        if key and questions.is_person_name_only_definition(row.get("chinese", "")):
            keys.add(key)
    for entry in v2_rows:
        if not isinstance(entry, dict):
            continue
        row = wordbank_v2.v2_entry_to_flat_csv_row(entry)
        key = wordbank_v2.normalize_english_key(row.get("english", ""))
        if key and questions.is_person_name_only_definition(row.get("chinese", "")):
            keys.add(key)
    return keys


def _backup(path: Path) -> str:
    original = path.read_bytes()
    original_stat = path.stat()
    backup = path.with_name(f"{path.name}.before-remove-person-names-{uuid.uuid4().hex}.bak")
    with backup.open("xb") as stream:
        stream.write(original)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(backup, stat.S_IMODE(original_stat.st_mode))
    try:
        os.chown(backup, original_stat.st_uid, original_stat.st_gid)
    except PermissionError:
        pass
    return str(backup)


def _remove_question_records(keys: set[str]) -> dict[str, int]:
    if not keys:
        return {namespace: 0 for namespace in QUESTION_NAMESPACES}
    removed = {namespace: 0 for namespace in QUESTION_NAMESPACES}
    with questions._thread_lock, questions._interprocess_lock():
        bank = questions._read_bank_keys_unlocked(keys)
        for namespace in QUESTION_NAMESPACES:
            records = bank[namespace]
            for key in keys:
                if key in records:
                    records.pop(key)
                    removed[namespace] += 1
        if any(removed.values()):
            questions._write_bank_keys_unlocked(bank, keys)
    return removed


def _apply(keys: set[str], csv_rows: list[dict], v2_rows: list[dict]) -> dict:
    with wordbank_v2._interprocess_lock(), wordbank_v2._words_v2_lock, web._words_csv_lock:
        current_v2 = wordbank_v2._read_words_v2_for_append()
        current_csv = web._read_words_csv_from_path(web.WORDS_CSV_FILE)
        new_v2 = [row for row in current_v2 if wordbank_v2.normalize_english_key(row.get("english", "")) not in keys]
        new_csv = [row for row in current_csv if wordbank_v2.normalize_english_key(row.get("english", "")) not in keys]
        removed_v2 = len(current_v2) - len(new_v2)
        removed_csv = len(current_csv) - len(new_csv)
        backups = []
        if removed_v2:
            backups.append(_backup(wordbank_v2.WORDS_V2_FILE))
            wordbank_v2._write_words_v2_atomic_under_lock(new_v2)
        if removed_csv:
            backups.append(_backup(web.WORDS_CSV_FILE))
            web._write_words_csv_rows_atomic_under_lock(new_csv)

    return {
        "removed_words_v2": removed_v2,
        "removed_words_csv": removed_csv,
        "backups": backups,
        "removed_question_records": _remove_question_records(keys),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="remove the reported entries")
    args = parser.parse_args()

    csv_rows = web._read_words_csv_from_path(web.WORDS_CSV_FILE)
    v2_rows = wordbank_v2._read_words_v2_for_append()
    keys = _selected_keys(csv_rows, v2_rows)
    result = {
        "mode": "apply" if args.apply else "dry_run",
        "keys": sorted(keys),
        "count": len(keys),
    }
    if args.apply:
        result.update(_apply(keys, csv_rows, v2_rows))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

"""Plan conservative example_form repairs; apply reviewed, content-guarded plans."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gaokao_questions as q
import wordbank_v2 as v

POS = {"noun": {"NOUN", "PROPN"}, "n": {"NOUN", "PROPN"},
       "verb": {"VERB", "AUX"}, "v": {"VERB", "AUX"},
       "adjective": {"ADJ"}, "adj": {"ADJ"}, "adverb": {"ADV"}, "adv": {"ADV"}}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def published_keys():
    keys = set()
    path = q.QUESTION_BANK_FILE
    if path.exists():
        bank = json.loads(path.read_text(encoding="utf-8-sig"))
        keys.update(bank.get("questions", {}))
    db = path.with_suffix(".sqlite3")
    if db.exists():
        with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            keys.update(row[0] for row in connection.execute(
                "SELECT word_key FROM records WHERE namespace='questions'"))
    return {v.normalize_english_key(key) for key in keys}


def valid(entry):
    return q.source_from_wordbank_row(v.v2_entry_to_flat_csv_row(entry)) is not None


def indexed(rows):
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("wordbank must be an array of objects")
    index = {}
    for row in rows:
        key = v.normalize_english_key(row.get("english", ""))
        index.setdefault(key, []).append(row)
    return index


def plan_repairs(path, keys, nlp=None, reviewed=None, published=None):
    raw = path.read_bytes()
    rows = json.loads(raw.decode("utf-8-sig"))
    index = indexed(rows)
    published = published_keys() if published is None else published
    reviewed = reviewed or {}
    plan = {"schema": "example-form-repair-v1", "wordbank": str(path.resolve()),
            "sha256": digest(raw), "selected_keys": sorted(keys), "changes": [], "results": []}
    for key in sorted(keys):
        matches = index.get(key, [])
        status = "unsupported"
        changes = []
        if key in published:
            status = "published_skipped"
        elif len(matches) != 1:
            status = "missing_or_duplicate_entry"
        elif valid(matches[0]):
            status = "already_valid"
        else:
            entry = copy.deepcopy(matches[0])
            senses = entry.get("senses", [])
            for sense in senses[:8]:
                sid = sense.get("id")
                if not sid or sum(s.get("id") == sid for s in senses) != 1:
                    continue
                example = str(sense.get("example_en") or "")
                form = reviewed.get(key, {}).get(sid)
                basis = "explicit_review"
                if form is None and nlp is not None:
                    allowed = POS.get(str(sense.get("pos", "")).rstrip(".").lower())
                    if not allowed:
                        continue
                    candidates = [token.text for token in nlp(example)
                                  if token.lemma_.lower() == key and token.pos_ in allowed]
                    if len(candidates) > 1:
                        status = "ambiguous"
                    if len(candidates) != 1:
                        continue
                    form = candidates[0]
                    basis = "spacy_lemma_pos"
                if not isinstance(form, str) or not form or not q._replace_target_once(example, form):
                    continue
                old = sense.get("example_form", "")
                if old == form:
                    continue
                change = {"word_key": key, "sense_id": sid, "example_en": example,
                          "old_form": old, "new_form": form, "basis": basis,
                          "had_field": "example_form" in sense}
                sense["example_form"] = form
                if valid(entry):
                    changes = [change]
                    status = "repairable"
                    break
                if change["had_field"]:
                    sense["example_form"] = old
                else:
                    sense.pop("example_form", None)
            plan["changes"].extend(changes)
        plan["results"].append({"word_key": key, "status": status})
    return plan


def apply_plan(plan, path=None):
    path = Path(path or v.WORDS_V2_FILE)
    if path.is_symlink():
        raise ValueError("apply requires the canonical regular file, not a symlink")
    if plan.get("schema") != "example-form-repair-v1" or str(path.resolve()) != plan.get("wordbank"):
        raise ValueError("plan schema or wordbank path mismatch")
    # Coordinate source edits with ordinary wordbank writes and question publication.
    with q._thread_lock, q._interprocess_lock(), v._interprocess_lock(), v._words_v2_lock:
        raw = path.read_bytes()
        if digest(raw) != plan.get("sha256"):
            raise ValueError("wordbank content changed since planning")
        rows = json.loads(raw.decode("utf-8-sig"))
        index = indexed(rows)
        protected = published_keys()
        selected = plan["selected_keys"]
        if not isinstance(selected, list) or not all(isinstance(key, str) for key in selected):
            raise ValueError("invalid selected keys")
        seen = set()
        changes = plan["changes"]
        if not isinstance(changes, list):
            raise ValueError("invalid changes")
        for change in changes:
            if set(change) != {"word_key", "sense_id", "example_en", "old_form", "new_form", "basis", "had_field"}:
                raise ValueError("unexpected repair fields")
            key, sid = change["word_key"], change["sense_id"]
            if key not in selected or key in protected or key in seen:
                raise ValueError("unselected, published, or duplicate repair: " + str(key))
            seen.add(key)
            matches = index.get(key, [])
            if len(matches) != 1 or valid(matches[0]):
                raise ValueError("entry missing, duplicate, or already valid: " + key)
            entry = matches[0]
            senses = [s for s in entry.get("senses", []) if s.get("id") == sid]
            if len(senses) != 1:
                raise ValueError("sense missing or duplicate")
            sense = senses[0]
            form = change["new_form"]
            if (change["basis"] not in {"explicit_review", "spacy_lemma_pos"}
                    or type(change["had_field"]) is not bool
                    or change["had_field"] != ("example_form" in sense)
                    or sense.get("example_form", "") != change["old_form"]
                    or sense.get("example_en", "") != change["example_en"]
                    or not isinstance(form, str) or not form.strip()
                    or form != form.strip()
                    or not q._replace_target_once(change["example_en"], form)):
                raise ValueError("repair field guard failed: " + key)
            sense["example_form"] = form
            if not valid(entry):
                raise ValueError("repair does not produce a valid source: " + key)
        if not changes:
            return {"changed": 0, "backup": None}
        original_stat = path.stat()
        backup = path.with_name(path.name + ".before-form-repair-" + uuid.uuid4().hex + ".bak")
        with backup.open("xb") as stream:
            os.fchown(stream.fileno(), original_stat.st_uid, original_stat.st_gid)
            os.fchmod(stream.fileno(), stat.S_IMODE(original_stat.st_mode))
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".form-repair-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                os.fchown(stream.fileno(), original_stat.st_uid, original_stat.st_gid)
                os.fchmod(stream.fileno(), stat.S_IMODE(original_stat.st_mode))
                json.dump(rows, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            if path.resolve() == v.WORDS_V2_FILE.resolve():
                v._words_v2_cache = None
                v._words_v2_by_key = None
                v._words_v2_cache_mtime = 0.0
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return {"changed": len(changes), "backup": str(backup)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keys", type=Path, help="JSON array of selected canonical English keys")
    parser.add_argument("--wordbank", type=Path, default=v.WORDS_V2_FILE)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--reviewed", type=Path, help="Reviewed mapping: {word: {sense_id: form}}")
    parser.add_argument("--apply", type=Path, help="Apply a previously reviewed plan")
    parser.add_argument("--model", default="en_core_web_sm")
    args = parser.parse_args()
    if args.apply:
        if args.keys or args.reviewed or args.output:
            parser.error("--apply cannot be combined with planning arguments")
        result = apply_plan(json.loads(args.apply.read_text()), args.wordbank)
    else:
        if not args.keys or not args.output:
            parser.error("planning requires --keys and --output")
        inputs = [args.wordbank, args.keys, q.QUESTION_BANK_FILE]
        if args.reviewed:
            inputs.append(args.reviewed)
        if args.output.resolve() in {p.resolve() for p in inputs}:
            parser.error("--output must differ from input and question bank files")
        keys = json.loads(args.keys.read_text())
        if not isinstance(keys, list) or not keys or not all(isinstance(k, str) and k for k in keys):
            parser.error("--keys must contain a nonempty JSON array of strings")
        reviewed = json.loads(args.reviewed.read_text()) if args.reviewed else {}
        if not isinstance(reviewed, dict) or any(not isinstance(val, dict) for val in reviewed.values()):
            parser.error("invalid reviewed mapping")
        try:
            import spacy
            nlp = spacy.load(args.model)
        except (ImportError, OSError):
            nlp = None
        result = plan_repairs(args.wordbank, {v.normalize_english_key(k) for k in keys}, nlp, reviewed)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        result = {"plan": str(args.output), "changes": len(result["changes"]), "morphology_available": nlp is not None}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

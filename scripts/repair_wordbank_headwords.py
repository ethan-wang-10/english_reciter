#!/usr/bin/env python3
"""Plan and apply the fifteen reviewed headword repairs while the service is stopped."""

import argparse
import base64
import copy
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from learning_sqlite_store import _decode, _encode
from project_paths import STATIC_WB_DIR

MAPPINGS = dict(zip(
    ("at all time", "in distance", "hope sb do", "behavious", "curs", "fer", "fric", "instalation",
     "merticulous", "pegment", "rusia", "swed", "theb", "de", "non"),
    ("at all times", "in the distance", "hope that", "behaviour", "curse", "for", "fricative", "installation",
     "meticulous", "pigment", "russia", "swede", "theban", "de-", "non-")))
NEW = {
    "at all times": ("/æt ɔːl taɪmz/", "phrase", "始终", "Keep your ID card with you at all times.", "请始终随身携带你的身份证。", "at all times"),
    "in the distance": ("/ɪn ðə ˈdɪstəns/", "phrase", "在远处", "A light glimmered in the distance.", "远处有微光闪烁。", "in the distance"),
    "hope that": ("/həʊp ðæt/", "phrase", "希望某事发生", "I hope that you can come to the party.", "我希望你能来参加派对。", "hope that"),
    "fricative": ("/ˈfrɪkətɪv/", "noun", "摩擦音", "The sounds /f/ and /v/ are fricatives.", "/f/ 和 /v/ 是摩擦音。", "fricatives"),
    "theban": ("/ˈθiːbən/", "noun", "底比斯人", "As a native of ancient Thebes, he was a Theban.", "他出生于古底比斯，是一名底比斯人。", "Theban"),
    "de-": ("/diː/", "prefix", "表示去除", "The prefix 'de-' can indicate removal, as in the verb defrost.", "前缀 de- 可以表示去除，例如动词 defrost（除霜）。", "de-"),
    "non-": ("/nɒn/", "prefix", "非", "The prefix 'non-' expresses negation, as in the adjective nonverbal.", "前缀 non- 表示否定，例如形容词 nonverbal（非语言的）。", "non-"),
}


def key(value):
    return " ".join(str(value or "").casefold().split())


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n"


def sha(value):
    return hashlib.sha256(value).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def db_snapshot(path):
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        rows = [list(row[:4]) + [bytes(row[4]).hex(), row[5]] for row in db.execute(
            "SELECT row_key,word_key,bucket,position,payload,payload_hash FROM words ORDER BY row_key")]
        state = [[row[0], bytes(row[1]).hex(), row[2]] for row in db.execute("SELECT singleton,payload,payload_hash FROM learning_state ORDER BY singleton")]
        meta = [list(row) for row in db.execute("SELECT key,value FROM metadata ORDER BY key")]
    return {"words": rows, "state": state, "metadata": meta}


def rekey_dict(value):
    result = copy.deepcopy(value)
    for old, new in MAPPINGS.items():
        if old in result:
            if new in result:
                raise ValueError("Learning state contains old and canonical keys; merge requires review")
            result[new] = result.pop(old)
    return result


def migrate_state(state):
    result = copy.deepcopy(state)
    if isinstance(result.get("review_states"), dict):
        result["review_states"] = rekey_dict(result["review_states"])
    if isinstance(result.get("daily_task"), dict):
        task = result["daily_task"]
        if task.get("word_key") in MAPPINGS:
            task["word_key"] = MAPPINGS[task["word_key"]]
        if isinstance(task.get("items"), list):
            for item in task["items"]:
                if isinstance(item, dict) and item.get("word_key") in MAPPINGS:
                    item["word_key"] = MAPPINGS[item["word_key"]]
    if isinstance(result.get("bonus_practice_session"), dict):
        session = result["bonus_practice_session"]
        if isinstance(session.get("word_keys"), list):
            before = session["word_keys"]
            after = [MAPPINGS.get(value, value) for value in before]
            if len(set(after)) != len(set(before)):
                raise ValueError("Bonus session canonical key collision")
            session["word_keys"] = after
        if isinstance(session.get("completed_events"), dict):
            session["completed_events"] = rekey_dict(session["completed_events"])
    return result


def migrate_db(snapshot):
    result = copy.deepcopy(snapshot)
    present = {row[1] for row in snapshot["words"]}
    for old, new in MAPPINGS.items():
        if old in present and new in present:
            raise ValueError("User has both old and canonical learning words; refusing progress merge")
    for row in result["words"]:
        old = row[1]
        if old not in MAPPINGS:
            continue
        new = MAPPINGS[old]
        if row[0] != old and not (row[0].startswith(old + "#") and row[0][len(old) + 1:].isdigit()):
            raise ValueError("Unexpected learning row key")
        payload = _decode(bytes.fromhex(row[4]))
        if key(payload.get("english")) != old:
            raise ValueError("Learning payload does not match row key")
        payload["english"] = new
        encoded, hashed = _encode(payload, compress=False)
        row[0], row[1], row[4], row[5] = new + row[0][len(old):], new, encoded.hex(), hashed
    if len({row[0] for row in result["words"]}) != len(result["words"]):
        raise ValueError("Learning row key collision")
    result["words"].sort(key=lambda row: row[0])
    for row in result["state"]:
        before = _decode(bytes.fromhex(row[1]))
        after = migrate_state(before)
        if after != before:
            encoded, hashed = _encode(after)
            row[1], row[2] = encoded.hex(), hashed
    if result != snapshot:
        revision = next((row for row in result["metadata"] if row[0] == "revision"), None)
        if revision is None:
            raise ValueError("Learning database has no revision")
        revision[1] = str(int(revision[1]) + 1)
    return result


def make_plan(data_dir, wordbank_dir=None):
    root = Path(data_dir).resolve()
    shared = root / "_shared"
    wordbank = Path(wordbank_dir or STATIC_WB_DIR).resolve()
    v2, csv_path, troubles = wordbank / "words_v2.json", wordbank / "words.csv", shared / "wordbank_troubles.json"
    entries = read_json(v2)
    if not isinstance(entries, list):
        raise ValueError("v2 must be a list")
    csv_bytes = csv_path.read_bytes()
    reader = csv.DictReader(io.StringIO(csv_bytes.decode("utf-8-sig"), newline=""))
    fields, csv_rows = reader.fieldnames, list(reader)
    if not fields or "english" not in fields:
        raise ValueError("CSV has no english column")
    by_key = {}
    for row in entries:
        if not isinstance(row, dict):
            raise ValueError("Invalid v2 entry")
        by_key.setdefault(key(row.get("english")), []).append(row)
    present = set(by_key) | {key(row.get("english")) for row in csv_rows}
    additions = []
    for old, new in MAPPINGS.items():
        if old not in present:
            if new not in present:
                raise ValueError("Neither reviewed old nor canonical headword exists: " + old)
            continue
        if new in present:
            continue
        if new not in NEW or len(by_key.get(old, [])) != 1:
            raise ValueError("Missing canonical row requires explicit reviewed replacement: " + old)
        entry = copy.deepcopy(by_key[old][0])
        if len(entry.get("senses", [])) != 1:
            raise ValueError("Reviewed replacement expects one sense")
        phonetic, pos, definition, example, translation, form = NEW[new]
        entry.update(english=new, phonetic=phonetic)
        sense = entry["senses"][0]
        sense.update(id=new + "#s0", pos=pos, definition_zh=definition, example_en=example,
                     example_cn=translation, example_form=form, phonetic_override=None)
        additions.append(entry)
    after_entries = [row for row in entries if key(row.get("english")) not in MAPPINGS] + additions
    after_csv = [row for row in csv_rows if key(row.get("english")) not in MAPPINGS]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    writer.writerows(after_csv)
    doc = read_json(troubles) if troubles.exists() else {"schema": "english_reciter.wordbank.troubles/v1", "difficult": {}, "mappings": {}}
    if not isinstance(doc, dict) or not isinstance(doc.get("mappings", {}), dict):
        raise ValueError("Invalid troubles mapping document")
    doc.setdefault("mappings", {})
    for old, new in MAPPINGS.items():
        if old in doc["mappings"] and doc["mappings"][old] != new:
            raise ValueError("Existing conflicting headword mapping")
        doc["mappings"][old] = new
    files = []
    source_guards = [{"path": str(path), "sha256": sha(path.read_bytes()) if path.exists() else None}
                     for path in (v2, csv_path, troubles)]
    for path, after in ((v2, dump(after_entries)), (csv_path, buffer.getvalue().encode("utf-8-sig") if csv_bytes.startswith(b"\xef\xbb\xbf") else buffer.getvalue().encode()), (troubles, dump(doc))):
        before = path.read_bytes() if path.exists() else None
        # Avoid serialization-only changes and make a completed migration a true no-op.
        same = (path == v2 and entries == after_entries) or (path == csv_path and csv_rows == after_csv) or (path == troubles and path.exists() and read_json(path) == doc)
        if before != after and not same:
            files.append({"path": str(path), "before_sha256": sha(before) if before is not None else None,
                          "after_sha256": sha(after), "after_base64": base64.b64encode(after).decode()})
    databases = []
    guards = []
    for user in sorted(root.iterdir()):
        if not user.is_dir() or user.name.startswith("_"):
            continue
        db_path = user / "learning.sqlite3"
        if db_path.exists():
            before = db_snapshot(db_path)
            after = migrate_db(before)
            if before != after and "legacy_migrated" not in dict(before["metadata"]):
                raise ValueError("Affected learning database has not completed legacy migration")
            guards.append({"path": str(db_path), "snapshot_sha256": sha(dump(before))})
            if before != after:
                databases.append({"path": str(db_path), "before": before, "after": after})
        if not db_path.exists() or "legacy_migrated" not in dict(before["metadata"]):
            for legacy in (user / "learning_data.json", user / "learning_data.learning_state_v2.json"):
                if legacy.exists():
                    payload = read_json(legacy)
                    if any('"' + old + '"' in json.dumps(payload, ensure_ascii=False).casefold() for old in MAPPINGS):
                        raise ValueError("Affected legacy-only learning data requires migration before headword repair")
    return {"schema": "reviewed-headwords-v1", "data_dir": str(root), "wordbank_dir": str(wordbank), "mappings": MAPPINGS,
            "files": files, "databases": databases, "database_guards": guards, "source_guards": source_guards}


def atomic(path, raw, template=None):
    path = Path(path)
    saved = (template or path).stat() if (template or path).exists() else None
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    if saved:
        os.chmod(temp, stat.S_IMODE(saved.st_mode))
        os.chown(temp, saved.st_uid, saved.st_gid)
    os.replace(temp, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def apply_plan(plan, plan_path):
    root = Path(plan["data_dir"]).resolve()
    if plan.get("schema") != "reviewed-headwords-v1" or plan.get("mappings") != MAPPINGS:
        raise ValueError("Unrecognized reviewed migration plan")
    changes = plan["files"] + plan["databases"]
    for change in changes:
        if not any(Path(change["path"]).resolve().is_relative_to(base) for base in (root, Path(plan["wordbank_dir"]).resolve())):
            raise ValueError("Plan path escapes data directory")
    changed_files = {row["path"]: row for row in plan["files"]}
    for guard in plan["source_guards"]:
        path = Path(guard["path"])
        current = sha(path.read_bytes()) if path.exists() else None
        allowed = [guard["sha256"]]
        if str(path) in changed_files:
            allowed.append(changed_files[str(path)]["after_sha256"])
        if current not in allowed:
            raise ValueError("Source file changed since review")
    for row in plan["files"]:
        path = Path(row["path"])
        current = sha(path.read_bytes()) if path.exists() else None
        if current not in (row["before_sha256"], row["after_sha256"]):
            raise ValueError("Source file changed since review")
        if sha(base64.b64decode(row["after_base64"])) != row["after_sha256"]:
            raise ValueError("Plan output checksum mismatch")
    changed_dbs = {row["path"]: row for row in plan["databases"]}
    for guard in plan["database_guards"]:
        current = db_snapshot(Path(guard["path"]))
        allowed = [guard["snapshot_sha256"]]
        if guard["path"] in changed_dbs:
            allowed.append(sha(dump(changed_dbs[guard["path"]]["after"])))
        if sha(dump(current)) not in allowed:
            raise ValueError("Learning records changed since review")
    journal_path = Path(str(plan_path) + ".journal.json")
    journal = read_json(journal_path) if journal_path.exists() else {"plan_sha256": sha(dump(plan)), "backups": {}, "completed": []}
    if journal["plan_sha256"] != sha(dump(plan)):
        raise ValueError("Journal belongs to a different plan")
    backup_dir = Path(str(plan_path) + ".backups")
    backup_dir.mkdir(exist_ok=True, mode=0o700)
    for index, change in enumerate(changes):
        path = Path(change["path"])
        if str(path) in journal["backups"]:
            continue
        backup = backup_dir / f"{index:03d}-{path.name}"
        if path.exists():
            if "before" in change:
                with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as source:
                    with sqlite3.connect(backup) as target:
                        source.backup(target)
                saved = path.stat()
                os.chmod(backup, stat.S_IMODE(saved.st_mode))
                os.chown(backup, saved.st_uid, saved.st_gid)
            else:
                atomic(backup, path.read_bytes(), template=path)
            journal["backups"][str(path)] = str(backup)
        else:
            journal["backups"][str(path)] = None
        atomic(journal_path, dump(journal))
    for change in changes:
        path = Path(change["path"])
        if "before" in change:
            if db_snapshot(path) != change["after"]:
                with sqlite3.connect(path) as db:
                    db.execute("BEGIN IMMEDIATE")
                    current = db_snapshot(path)
                    if current != change["before"]:
                        raise ValueError("Learning database changed before write")
                    old_rows = {row[0]: row for row in change["before"]["words"]}
                    new_rows = {row[0]: row for row in change["after"]["words"]}
                    for row_key in old_rows.keys() - new_rows.keys():
                        db.execute("DELETE FROM words WHERE row_key=?", (row_key,))
                    for row_key, row in new_rows.items():
                        if old_rows.get(row_key) != row:
                            db.execute("INSERT OR REPLACE INTO words VALUES(?,?,?,?,?,?)", (*row[:4], bytes.fromhex(row[4]), row[5]))
                    for row in change["after"]["state"]:
                        db.execute("UPDATE learning_state SET payload=?,payload_hash=? WHERE singleton=?", (bytes.fromhex(row[1]), row[2], row[0]))
                    revision = dict(change["after"]["metadata"])["revision"]
                    db.execute("UPDATE metadata SET value=? WHERE key='revision'", (revision,))
        elif not path.exists() or sha(path.read_bytes()) != change["after_sha256"]:
            atomic(path, base64.b64decode(change["after_base64"]))
        if str(path) not in journal["completed"]:
            journal["completed"].append(str(path))
        atomic(journal_path, dump(journal))
    return {"files": len(plan["files"]), "users": len(plan["databases"]), "journal": str(journal_path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--wordbank-dir", default=str(STATIC_WB_DIR))
    parser.add_argument("--plan", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--service-stopped", action="store_true")
    args = parser.parse_args()
    if args.apply:
        if not args.service_stopped:
            parser.error("--apply requires --service-stopped")
        plan = read_json(Path(args.plan))
        if Path(plan["data_dir"]).resolve() != Path(args.data_dir).resolve():
            parser.error("plan data directory differs")
        result = apply_plan(plan, args.plan)
    else:
        plan = make_plan(args.data_dir, args.wordbank_dir)
        path = Path(args.plan)
        if path.exists() and read_json(path) != plan:
            parser.error("existing plan differs; use a new plan path")
        if not path.exists():
            atomic(path, dump(plan))
            os.chmod(path, 0o600)
        result = {"files": len(plan["files"]), "users": len(plan["databases"]), "plan": str(path), "apply": False}
    print(json.dumps(result))


if __name__ == "__main__":
    main()

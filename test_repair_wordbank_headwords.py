import csv
import json
from pathlib import Path
import sqlite3

import pytest

import gaokao_questions as questions
import wordbank_v2
from learning_sqlite_store import LearningSQLiteStore, _decode, _encode
from scripts import repair_wordbank_headwords as repair


@pytest.fixture
def files(tmp_path):
    data, wb = tmp_path / "data", tmp_path / "wordbank"
    (data / "_shared").mkdir(parents=True)
    wb.mkdir()
    entries = []
    for old, new in repair.MAPPINGS.items():
        entries.append({"english": old, "level": "GRE", "phonetic": "old", "entry_kind": "word",
                        "senses": [{"id": old + "#s0", "definition_zh": "原义", "example_en": "old", "unknown": 3}]})
        if new not in repair.NEW:
            entries.append({"english": new, "senses": [{"id": new + "#s0", "example_en": "KEEP"}], "unknown": [1, 2]})
    (wb / "words_v2.json").write_bytes(repair.dump(entries))
    with (wb / "words.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["english", "chinese"])
        writer.writeheader()
        writer.writerows([{"english": old, "chinese": "旧词"} for old in repair.MAPPINGS])
    (data / "_shared" / "wordbank_troubles.json").write_text('{"unknown":42,"mappings":{"past":"previous"}}')
    user = data / "student"
    user.mkdir()
    db_path = user / "learning.sqlite3"
    with sqlite3.connect(db_path) as db:
        LearningSQLiteStore._create_schema(db)
        db.execute("INSERT INTO metadata VALUES('legacy_migrated','1')")
        payload = {"english": "non", "review_count": 7, "wrong_count": 2, "unknown": {"a": 3}}
        encoded, hashed = _encode(payload, compress=False)
        db.execute("INSERT INTO words VALUES(?,?,?,?,?,?)", ("non", "non", "mastered", 4, encoded, hashed))
        state = {"review_states": {"non": {"due": "tomorrow", "stability": 8}}, "daily_task": {"word_key": "non", "progress": 5,
                 "items": [{"item_id": "daily-item-1", "word_key": "non", "status": "completed", "attempts": 3,
                            "correct": True, "unknown": {"source": "non"}},
                           {"item_id": "daily-item-2", "word_key": "existing", "status": "pending", "attempts": 0}]},
                 "bonus_practice_session": {"word_keys": ["non"], "completed_events": {"non": "event-1"}}, "other": 20}
        encoded, hashed = _encode(state)
        db.execute("INSERT INTO learning_state VALUES(1,?,?)", (encoded, hashed))
    return data, wb, db_path


def test_apply_preserves_progress_canonical_entries_and_backups(files, tmp_path):
    data, wb, db_path = files
    (wb / "words_v2.json").chmod(0o640)
    db_path.chmod(0o600)
    original_entries = repair.read_json(wb / "words_v2.json")
    before = repair.db_snapshot(db_path)
    plan = repair.make_plan(data, wb)
    plan_path = tmp_path / "plan.json"
    result = repair.apply_plan(plan, plan_path)
    after = repair.db_snapshot(db_path)
    row = after["words"][0]
    assert row[:4] == ["non-", "non-", "mastered", 4]
    old_payload = _decode(bytes.fromhex(before["words"][0][4]))
    assert _decode(bytes.fromhex(row[4])) == {**old_payload, "english": "non-"}
    state = _decode(bytes.fromhex(after["state"][0][1]))
    assert state["review_states"] == {"non-": {"due": "tomorrow", "stability": 8}}
    assert state["daily_task"] == {"word_key": "non-", "progress": 5, "items": [
        {"item_id": "daily-item-1", "word_key": "non-", "status": "completed", "attempts": 3,
         "correct": True, "unknown": {"source": "non"}},
        {"item_id": "daily-item-2", "word_key": "existing", "status": "pending", "attempts": 0}]}
    assert state["bonus_practice_session"] == {"word_keys": ["non-"], "completed_events": {"non-": "event-1"}}
    assert dict(after["metadata"])["revision"] == "1"
    new_entries = repair.read_json(wb / "words_v2.json")
    for entry in original_entries:
        if entry["english"] not in repair.MAPPINGS:
            assert entry in new_entries
    assert not set(repair.MAPPINGS) & {row["english"] for row in new_entries}
    for entry in new_entries:
        if entry["english"] in repair.NEW:
            source = questions.source_from_wordbank_row(wordbank_v2.v2_entry_to_flat_csv_row(entry))
            assert source is not None, entry["english"]
            assert source["context_answer"] == repair.NEW[entry["english"]][-1]
    trouble = repair.read_json(data / "_shared" / "wordbank_troubles.json")
    assert trouble["unknown"] == 42 and trouble["mappings"]["past"] == "previous"
    journal = repair.read_json(Path(result["journal"]))
    assert Path(result["journal"]).stat().st_mode & 0o777 == 0o600
    assert (wb / "words_v2.json").stat().st_mode & 0o777 == 0o640
    assert Path(journal["backups"][str(wb / "words_v2.json")]).stat().st_mode & 0o777 == 0o640
    assert db_path.stat().st_mode & 0o777 == 0o600
    assert repair.db_snapshot(Path(journal["backups"][str(db_path)])) == before
    repair.apply_plan(plan, plan_path)
    assert repair.db_snapshot(db_path) == after
    repeat = repair.make_plan(data, wb)
    assert repeat["files"] == repeat["databases"] == []


def test_collision_and_changed_learning_abort_before_source_write(files, tmp_path):
    data, wb, db_path = files
    plan = repair.make_plan(data, wb)
    before = (wb / "words_v2.json").read_bytes()
    with sqlite3.connect(db_path) as db:
        encoded, hashed = _encode({"english": "non-"})
        db.execute("INSERT INTO words VALUES(?,?,?,?,?,?)", ("non-", "non-", "pending", 0, encoded, hashed))
    with pytest.raises(ValueError, match="both old and canonical"):
        repair.make_plan(data, wb)
    with pytest.raises(ValueError, match="changed since review"):
        repair.apply_plan(plan, tmp_path / "plan.json")
    assert (wb / "words_v2.json").read_bytes() == before


def test_source_guard_and_legacy_only_abort(files, tmp_path):
    data, wb, _ = files
    plan = repair.make_plan(data, wb)
    (wb / "words.csv").write_text("english,chinese\nchanged,改变\n")
    with pytest.raises(ValueError, match="Source file changed"):
        repair.apply_plan(plan, tmp_path / "plan.json")
    (data / "legacy").mkdir()
    (data / "legacy" / "learning_data.json").write_text('{"all_words":[{"english":"non"}]}')
    with pytest.raises(ValueError, match="legacy-only"):
        repair.make_plan(data, wb)


def test_affix_distractors_are_allowed_only_for_matching_affix_answers():
    values = ["un-", "re-", "-ness", "plain", "pre-", "bad--"]
    kwargs = {"forbidden": ["non-"], "require_cjk": False, "limit": 12}
    assert questions._clean_distinct_list(values, correct_answer="non-", **kwargs) == ["un-", "re-", "pre-"]
    assert questions._clean_distinct_list(values, correct_answer="ordinary", **kwargs) == ["plain"]
    assert questions._clean_distinct_list(values, correct_answer="-ity", **kwargs) == ["-ness"]
    assert questions._recognition_core_sense("prefix 表示去除") == "表示去除"
    assert questions._recognition_core_sense("suffix: 某种状态") == "某种状态"

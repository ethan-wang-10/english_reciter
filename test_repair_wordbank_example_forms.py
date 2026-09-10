import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import repair_wordbank_example_forms as repair


def entry(word="abate", sentence="The storm abated by morning.", form=""):
    return {"english": word, "level": "GRE", "unknown": {"preserve": [1, 2]},
            "senses": [{"id": word + "#s0", "pos": "verb", "definition_zh": "减弱",
                        "example_en": sentence, "example_form": form}]}


class RepairTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "words_v2.json"
        self.original = [entry(), entry("other", "It is other.", "other")]
        self.path.write_text(json.dumps(self.original, ensure_ascii=False))
        self.path.chmod(0o640)
        for target, value in [("WORDS_INTERPROCESS_LOCKFILE", self.root / "word.lock")]:
            p = patch.object(repair.v, target, value)
            p.start()
            self.addCleanup(p.stop)
        for target, value in [("QUESTION_BANK_LOCK_FILE", self.root / "bank.lock"),
                              ("QUESTION_BANK_FILE", self.root / "bank.json")]:
            p = patch.object(repair.q, target, value)
            p.start()
            self.addCleanup(p.stop)

    def plan(self, **kwargs):
        return repair.plan_repairs(self.path, {"abate"},
                                   reviewed={"abate": {"abate#s0": "abated"}}, **kwargs)

    def test_apply_preserves_records_backup_and_mode(self):
        before = self.path.read_bytes()
        result = repair.apply_plan(self.plan(), self.path)
        expected = copy.deepcopy(self.original)
        expected[0]["senses"][0]["example_form"] = "abated"
        self.assertEqual(json.loads(self.path.read_bytes()), expected)
        self.assertEqual(Path(result["backup"]).read_bytes(), before)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)
        self.assertEqual(Path(result["backup"]).stat().st_mode & 0o777, 0o640)
        self.assertEqual(self.path.stat().st_uid, Path(result["backup"]).stat().st_uid)

    def test_changed_content_and_path_rejected(self):
        plan = self.plan()
        with self.assertRaisesRegex(ValueError, "path mismatch"):
            repair.apply_plan(plan, self.root / "elsewhere")
        self.path.write_bytes(self.path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "content changed"):
            repair.apply_plan(plan, self.path)
        self.assertEqual(list(self.root.glob("*.bak")), [])

    def test_all_field_guards_before_any_write(self):
        original = self.path.read_bytes()
        for field, value in [("sense_id", "wrong"), ("old_form", "different"),
                             ("example_en", "another sentence"), ("new_form", "absent"),
                             ("had_field", False), ("unexpected", True)]:
            with self.subTest(field=field):
                plan = self.plan()
                plan["changes"][0][field] = value
                with self.assertRaises(ValueError):
                    repair.apply_plan(plan, self.path)
                self.assertEqual(self.path.read_bytes(), original)

    def test_published_rechecked_at_apply(self):
        plan = self.plan()
        repair.q.QUESTION_BANK_FILE.write_text(json.dumps({"questions": {"abate": {}}}))
        with self.assertRaisesRegex(ValueError, "published"):
            repair.apply_plan(plan, self.path)
        self.assertEqual(self.plan()["results"][0]["status"], "published_skipped")

    def test_already_valid_unsupported_and_ambiguous(self):
        plan = repair.plan_repairs(self.path, {"other"}, published=set())
        self.assertEqual(plan["results"][0]["status"], "already_valid")
        self.assertEqual(repair.plan_repairs(self.path, {"abate"}, published=set())["changes"], [])
        token = SimpleNamespace(text="abated", lemma_="abate", pos_="VERB")
        plan = repair.plan_repairs(self.path, {"abate"}, lambda _: [token, token], published=set())
        self.assertEqual(plan["results"][0]["status"], "ambiguous")
        plan = repair.plan_repairs(self.path, {"abate"}, lambda _: [token], published=set())
        self.assertEqual(plan["changes"][0]["new_form"], "abated")
        token.pos_ = "NOUN"
        self.assertEqual(repair.plan_repairs(self.path, {"abate"}, lambda _: [token], published=set())["changes"], [])

    def test_missing_form_field_preserved_until_explicit_repair(self):
        rows = copy.deepcopy(self.original)
        del rows[0]["senses"][0]["example_form"]
        self.path.write_text(json.dumps(rows))
        plan = self.plan()
        self.assertFalse(plan["changes"][0]["had_field"])
        repair.apply_plan(plan, self.path)
        self.assertEqual(json.loads(self.path.read_text())[0]["senses"][0]["example_form"], "abated")

    def test_duplicate_and_unselected_changes_rejected(self):
        plan = self.plan()
        plan["changes"].append(copy.deepcopy(plan["changes"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            repair.apply_plan(plan, self.path)
        plan = self.plan()
        plan["selected_keys"] = []
        with self.assertRaisesRegex(ValueError, "unselected"):
            repair.apply_plan(plan, self.path)


if __name__ == "__main__":
    unittest.main()

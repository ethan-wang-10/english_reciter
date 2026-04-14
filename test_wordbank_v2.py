#!/usr/bin/env python3
"""wordbank_v2 模块单测（不依赖 reciter / flask）。"""

import unittest

import wordbank_v2


class TestWordbankV2(unittest.TestCase):
    def test_normalize_english_key(self):
        self.assertEqual(wordbank_v2.normalize_english_key("  In Light OF "), "in light of")

    def test_build_chinese_summary(self):
        senses = [
            {"pos": "noun", "definition_zh": "光"},
            {"pos": "adj", "definition_zh": "轻的"},
        ]
        s = wordbank_v2.build_chinese_summary(senses)
        self.assertIn("光", s)
        self.assertIn("轻的", s)

    def test_finalize_v2_minimal(self):
        raw = {
            "english": "run",
            "senses": [{"pos": "verb", "definition_zh": "跑"}],
            "phonetic": "/rʌn/",
            "example1": "I run.",
            "example1_form": "run",
            "example1_cn": "我跑。",
        }
        fin = wordbank_v2.finalize_v2_entry_from_deepseek(raw)
        self.assertIsNotNone(fin)
        assert fin is not None
        self.assertEqual(fin["english"], "run")
        self.assertEqual(fin["senses"][0]["id"], "run#s0")
        self.assertNotIn("example1", fin)
        row = wordbank_v2.v2_entry_to_flat_csv_row(fin)
        self.assertEqual(row["chinese"], wordbank_v2.build_chinese_summary(fin["senses"]))
        self.assertEqual(row["example1"], "I run.")

    def test_finalize_three_senses_three_examples(self):
        raw = {
            "english": "bat",
            "senses": [
                {
                    "pos": "noun",
                    "definition_zh": "蝙蝠",
                    "example_en": "A bat sleeps.",
                    "example_cn": "蝙蝠睡觉。",
                    "example_form": "",
                },
                {
                    "pos": "noun",
                    "definition_zh": "球棒",
                    "example_en": "Swing the bat.",
                    "example_cn": "挥棒。",
                    "example_form": "",
                },
                {
                    "pos": "verb",
                    "definition_zh": "击打",
                    "example_en": "He bats well.",
                    "example_cn": "他击球好。",
                    "example_form": "",
                },
            ],
            "phonetic": "/bæt/",
            "level": "初中",
        }
        fin = wordbank_v2.finalize_v2_entry_from_deepseek(raw)
        self.assertIsNotNone(fin)
        assert fin is not None
        self.assertNotIn("example1", fin)
        self.assertNotIn("chinese_summary", fin)
        row = wordbank_v2.v2_entry_to_flat_csv_row(fin)
        self.assertEqual(row["example1"], "A bat sleeps.")
        self.assertEqual(row["example2"], "Swing the bat.")
        self.assertEqual(row["example3"], "He bats well.")
        self.assertEqual(row["example3_cn"], "他击球好。")

    def test_slim_v2_roundtrip_and_stale_flat_ignored(self):
        """落盘仅 senses；读取时从 senses 派生扁平行；若文件曾含陈旧 example*，以 senses 为准。"""
        raw = {
            "english": "bat",
            "senses": [
                {
                    "pos": "noun",
                    "definition_zh": "蝙蝠",
                    "example_en": "A bat sleeps.",
                    "example_cn": "蝙蝠睡觉。",
                    "example_form": "",
                },
            ],
            "phonetic": "/bæt/",
            "level": "初中",
        }
        fin = wordbank_v2.finalize_v2_entry_from_deepseek(raw)
        assert fin is not None
        slim = wordbank_v2.strip_redundant_v2_fields_for_storage(fin)
        self.assertNotIn("example1", slim)
        stale = dict(slim)
        stale["example1"] = "WRONG SENTENCE"
        stale["chinese_summary"] = "wrong"
        row = wordbank_v2.v2_entry_to_flat_csv_row(stale)
        self.assertEqual(row["example1"], "A bat sleeps.")
        self.assertIn("蝙蝠", row["chinese"])


if __name__ == "__main__":
    unittest.main()

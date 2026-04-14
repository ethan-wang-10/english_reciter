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
        row = wordbank_v2.v2_entry_to_flat_csv_row(fin)
        self.assertEqual(row["chinese"], fin["chinese_summary"])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""单元测试"""

import unittest
import json
import tempfile
import os
from pathlib import Path
from datetime import date, timedelta

from reciter import (
    Config, Word, ExampleGenerator, WordRepository, WordReciter
)


class TestConfig(unittest.TestCase):
    """测试配置管理"""
    
    def test_default_config(self):
        """测试默认配置"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            config_file = f.name
        
        try:
            config = Config(config_file)
            self.assertEqual(config.MAX_SUCCESS_COUNT, 8)
            self.assertTrue(config.TTS_ENABLED)
            self.assertTrue(config.BACKUP_ENABLED)
            self.assertEqual(config.BACKUP_INTERVAL_DAYS, 7)
        finally:
            os.unlink(config_file)
    
    def test_custom_config(self):
        """测试自定义配置"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            config_file = f.name
            custom_config = {
                "max_success_count": 5,
                "tts_enabled": False,
                "backup_enabled": False
            }
            json.dump(custom_config, f)
        
        try:
            config = Config(config_file)
            self.assertEqual(config.MAX_SUCCESS_COUNT, 5)
            self.assertFalse(config.TTS_ENABLED)
            self.assertFalse(config.BACKUP_ENABLED)
        finally:
            os.unlink(config_file)


class TestWord(unittest.TestCase):
    """测试单词类"""
    
    def test_word_creation(self):
        """测试单词创建"""
        word = Word("apple", "苹果")
        self.assertEqual(word.english, "apple")
        self.assertEqual(word.chinese, "苹果")
        self.assertEqual(word.success_count, 0)
        self.assertEqual(word.review_round, 0)
        self.assertEqual(word.review_count, 0)
    
    def test_word_to_dict(self):
        """测试单词序列化"""
        word = Word("apple", "苹果", success_count=3, review_round=1, review_count=2)
        word_dict = word.to_dict()
        
        self.assertEqual(word_dict['english'], "apple")
        self.assertEqual(word_dict['chinese'], "苹果")
        self.assertEqual(word_dict['success_count'], 3)
        self.assertEqual(word_dict['review_round'], 1)
        self.assertEqual(word_dict['review_count'], 2)
    
    def test_word_from_dict(self):
        """测试从字典创建单词"""
        word_dict = {
            'english': 'banana',
            'chinese': '香蕉',
            'success_count': 2,
            'next_review_date': '2026-01-31',
            'example': 'I like banana._我喜欢香蕉。',
            'review_round': 0,
            'review_count': 1
        }
        word = Word.from_dict(word_dict)
        
        self.assertEqual(word.english, "banana")
        self.assertEqual(word.chinese, "香蕉")
        self.assertEqual(word.success_count, 2)
        self.assertEqual(word.next_review_date, date(2026, 1, 31))
    
    def test_word_from_dict_compatibility(self):
        """测试旧数据兼容性"""
        word_dict = {
            'english': 'orange',
            'chinese': '橙子',
            'success_count': 1,
            'next_review_date': '2026-01-31',
            'example': None
        }
        word = Word.from_dict(word_dict)
        
        self.assertEqual(word.review_round, 0)
        self.assertEqual(word.review_count, 0)


class TestExampleGenerator(unittest.TestCase):
    """测试例句生成器"""
    
    def setUp(self):
        """创建临时例句库"""
        self.temp_db = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        self.temp_db.close()
        self.generator = ExampleGenerator(self.temp_db.name)
    
    def tearDown(self):
        """清理临时文件"""
        os.unlink(self.temp_db.name)
    
    def test_get_example_default(self):
        """测试生成默认例句"""
        example = self.generator.get_example("test", "测试")
        self.assertIn("_", example)
        self.assertIn("test", example.lower())
    
    def test_add_and_get_example(self):
        """测试添加和获取例句"""
        custom_example = "This is a custom test example._这是一个自定义测试例句。"
        self.generator.add_example("test", custom_example)
        
        example = self.generator.get_example("test", "测试")
        self.assertEqual(example, custom_example)
    
    def test_save_local_db(self):
        """测试保存例句库"""
        self.generator.add_example("save", "Save example._保存例句。")
        self.generator.save_local_db()
        
        new_generator = ExampleGenerator(self.temp_db.name)
        self.assertIn("save", new_generator.local_db)
    
    def test_multiple_examples(self):
        """测试多个例句"""
        self.generator.add_example("multi", "Example 1._例句1。")
        self.generator.add_example("multi", "Example 2._例句2。")
        self.generator.add_example("multi", "Example 3._例句3。")
        
        examples = self.generator.local_db["multi"]
        self.assertEqual(len(examples), 3)


class TestWordRepository(unittest.TestCase):
    """测试数据访问层"""
    
    def setUp(self):
        """创建临时数据文件"""
        self.temp_data = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        self.temp_data.close()
        
        self.temp_config = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        config = {
            "data_file": self.temp_data.name,
            "backup_enabled": False
        }
        json.dump(config, self.temp_config)
        self.temp_config.close()
        
        self.config = Config(self.temp_config.name)
        self.repository = WordRepository(self.config)
    
    def tearDown(self):
        """清理临时文件"""
        os.unlink(self.temp_data.name)
        os.unlink(self.temp_config.name)
    
    def test_save_and_load_data(self):
        """测试保存和加载数据"""
        word1 = Word("apple", "苹果", success_count=2, review_round=0, review_count=3)
        word2 = Word("banana", "香蕉", success_count=5, review_round=1, review_count=6)
        mastered = Word("cat", "猫", success_count=8, review_round=2, review_count=10)
        
        self.repository.save_data([word1, word2], [mastered])
        all_words, mastered_words = self.repository.load_data()
        
        self.assertEqual(len(all_words), 2)
        self.assertEqual(len(mastered_words), 1)
        self.assertEqual(all_words[0].english, "apple")
        self.assertEqual(mastered_words[0].english, "cat")
    
    def test_load_empty_data(self):
        """测试加载空数据"""
        all_words, mastered_words = self.repository.load_data()
        self.assertEqual(len(all_words), 0)
        self.assertEqual(len(mastered_words), 0)
    
    def test_load_corrupted_data(self):
        """测试加载损坏的数据"""
        with open(self.temp_data.name, 'w') as f:
            f.write("invalid json")
        
        all_words, mastered_words = self.repository.load_data()
        self.assertEqual(len(all_words), 0)
        self.assertEqual(len(mastered_words), 0)


class TestWordReciter(unittest.TestCase):
    """测试核心复习系统"""
    
    def setUp(self):
        """创建临时测试环境"""
        self.temp_data = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        self.temp_data.close()
        
        self.temp_config = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        config = {
            "data_file": self.temp_data.name,
            "backup_enabled": False,
            "tts_enabled": False
        }
        json.dump(config, self.temp_config)
        self.temp_config.close()
        
        self.config = Config(self.temp_config.name)
    
    def tearDown(self):
        """清理临时文件"""
        os.unlink(self.temp_data.name)
        os.unlink(self.temp_config.name)
    
    def test_initialization(self):
        """测试初始化"""
        reciter = WordReciter(self.config)
        self.assertEqual(len(reciter.all_words), 0)
        self.assertEqual(len(reciter.mastered_words), 0)
        self.assertEqual(reciter.current_review_round, 0)
    
    def test_add_words(self):
        """测试添加单词"""
        reciter = WordReciter(self.config)
        words = [
            ("apple", "苹果"),
            ("banana", "香蕉"),
            ("cat", "猫")
        ]
        reciter.add_words(words)
        
        self.assertEqual(len(reciter.all_words), 3)
        self.assertEqual(reciter.all_words[0].english, "apple")
        self.assertEqual(reciter.all_words[0].success_count, 0)
    
    def test_add_duplicate_words(self):
        """测试添加重复单词"""
        reciter = WordReciter(self.config)
        words = [
            ("apple", "苹果"),
            ("Apple", "苹果"),
            ("APPLE", "苹果")
        ]
        reciter.add_words(words)
        
        self.assertEqual(len(reciter.all_words), 1)

    def test_add_words_skips_whitespace_duplicate(self):
        """去重时忽略英文首尾空格"""
        reciter = WordReciter(self.config)
        words = [
            ("apple", "苹果"),
            ("  Apple ", "苹果2"),
        ]
        r = reciter.add_words(words)
        self.assertEqual(len(reciter.all_words), 1)
        self.assertEqual(reciter.all_words[0].english, "apple")
        self.assertEqual(r['added'], 1)
        self.assertEqual(r['skipped_duplicate'], 1)

    def test_add_words_skips_existing_in_mastered(self):
        """已掌握列表中的词不再加入待复习"""
        reciter = WordReciter(self.config)
        reciter.mastered_words.append(Word("done", "完成"))
        r = reciter.add_words([("Done", "完成2")])
        self.assertEqual(len(reciter.all_words), 0)
        self.assertEqual(r['skipped_duplicate'], 1)
        self.assertEqual(r['added'], 0)

    def test_remove_words_by_english_pending(self):
        """按英文移除待复习单词（不区分大小写）"""
        reciter = WordReciter(self.config)
        reciter.add_words([("apple", "苹果"), ("banana", "香蕉")])
        r = reciter.remove_words_by_english(["Apple", "missing"])
        self.assertEqual(r["removed"], 1)
        self.assertEqual(r["not_found"], ["missing"])
        self.assertEqual(len(reciter.all_words), 1)
        self.assertEqual(reciter.all_words[0].english, "banana")

    def test_remove_words_by_english_mastered(self):
        """按英文移除已掌握单词"""
        reciter = WordReciter(self.config)
        reciter.mastered_words.append(Word("z", "终"))
        r = reciter.remove_words_by_english(["Z"])
        self.assertEqual(r["removed"], 1)
        self.assertEqual(len(reciter.mastered_words), 0)
    
    def test_process_overdue_words(self):
        """逾期单词不再被改成今天，以保留「遗留」可区分性"""
        reciter = WordReciter(self.config)
        yesterday = date.today() - timedelta(days=1)
        word = Word("test", "测试", next_review_date=yesterday)
        reciter.all_words.append(word)
        
        reciter._process_overdue_words()
        self.assertEqual(word.next_review_date, yesterday)

    def test_today_scheduled_first_then_carryover_oldest(self):
        """今日列表：今日排期优先，遗留在后（越早到期越靠前）"""
        reciter = WordReciter(self.config)
        t0 = date.today()
        reciter.all_words = [
            Word("now", "今", next_review_date=t0),
            Word("mid", "中", next_review_date=t0 - timedelta(days=1)),
            Word("old", "旧", next_review_date=t0 - timedelta(days=5)),
        ]
        lst = reciter._get_today_review_list()
        self.assertEqual([w.english for w in lst], ["now", "old", "mid"])
    
    def test_get_today_review_list(self):
        """测试获取今日复习列表"""
        reciter = WordReciter(self.config)
        today_words = [
            Word("apple", "苹果", next_review_date=date.today()),
            Word("banana", "香蕉", next_review_date=date.today())
        ]
        future_word = Word("cat", "猫", next_review_date=date.today() + timedelta(days=7))
        
        reciter.all_words.extend(today_words + [future_word])
        review_list = reciter._get_today_review_list()
        
        self.assertEqual(len(review_list), 2)
    
    def test_update_review_round(self):
        """测试更新复习轮次"""
        reciter = WordReciter(self.config)
        word1 = Word("test1", "测试1", review_round=0)
        word2 = Word("test2", "测试2", review_round=1)
        word3 = Word("test3", "测试3", review_round=0)
        
        reciter.all_words.extend([word1, word2, word3])
        reciter._update_review_round()
        
        self.assertEqual(reciter.current_review_round, 0)
    
    def test_empty_review_list(self):
        """测试空复习列表"""
        reciter = WordReciter(self.config)
        review_list = reciter._get_today_review_list()
        self.assertEqual(len(review_list), 0)

    def test_record_answer_correct_main_pass(self):
        """主轮答对：增加 success_count 与 review_count，并排期"""
        reciter = WordReciter(self.config)
        w = Word("a", "甲", success_count=0, next_review_date=date.today())
        reciter.all_words.append(w)
        reciter.record_answer_correct(w, remedial=False)
        self.assertEqual(w.success_count, 1)
        self.assertEqual(w.review_count, 1)
        self.assertGreaterEqual(w.next_review_date, reciter.today)

    def test_record_answer_correct_remedial(self):
        """错题巩固答对：不增加 success_count，但排期到今日之后（与 Web 一致）"""
        reciter = WordReciter(self.config)
        w = Word("b", "乙", success_count=2, next_review_date=date.today())
        reciter.all_words.append(w)
        reciter.record_answer_correct(w, remedial=True)
        self.assertEqual(w.success_count, 2)
        self.assertEqual(w.review_count, 1)
        self.assertGreater(w.next_review_date, reciter.today)

    def test_record_answer_incorrect(self):
        """答错：仅增加 review_count"""
        reciter = WordReciter(self.config)
        w = Word("c", "丙")
        reciter.all_words.append(w)
        reciter.record_answer_incorrect(w)
        self.assertEqual(w.review_count, 1)

    def test_record_bonus_answer_correct(self):
        """加练答对：只增加复习次数，不改变掌握进度与排期"""
        reciter = WordReciter(self.config)
        w = Word("d", "丁", success_count=0, next_review_date=date.today(), review_count=0)
        nd = w.next_review_date
        reciter.all_words.append(w)
        reciter.record_bonus_answer_correct(w)
        self.assertEqual(w.success_count, 0)
        self.assertEqual(w.review_count, 1)
        self.assertEqual(w.next_review_date, nd)

    def test_get_extra_review_words(self):
        """加练选词：复习次数少优先，同层最多取满 count"""
        reciter = WordReciter(self.config)
        reciter.all_words = [
            Word("a", "甲", review_count=2),
            Word("b", "乙", review_count=0),
        ]
        reciter.mastered_words = [
            Word("c", "丙", review_count=0),
        ]
        picked = reciter.get_extra_review_words(5)
        self.assertEqual(len(picked), 3)
        self.assertEqual({w.english for w in picked}, {"a", "b", "c"})


def run_tests():
    """运行所有测试"""
    unittest.main(argv=[''], verbosity=2, exit=False)


if __name__ == '__main__':
    run_tests()

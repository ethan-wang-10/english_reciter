#!/usr/bin/env python3
"""单元测试"""

import unittest
import json
import tempfile
import os
from unittest.mock import patch
from pathlib import Path
from datetime import date, timedelta

from app_time import china_today
from reciter import (
    Config,
    ExampleGenerator,
    LearningDataLoadError,
    Word,
    WordReciter,
    WordRepository,
    order_by_spelling_similarity,
    spelling_similarity,
)
from review_scheduler import ReviewEventConflict


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
            self.assertEqual(config.DAILY_REVIEW_LIMIT, 120)
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

    def test_invalid_daily_limits_fall_back_to_defaults(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            config_file = f.name
            json.dump({'daily_review_limit': None}, f)

        try:
            config = Config(config_file)
            self.assertEqual(config.DAILY_REVIEW_LIMIT, 120)
        finally:
            os.unlink(config_file)

    def test_daily_limit_is_capped_at_parent_maximum(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            config_file = f.name
            json.dump({'daily_review_limit': 999}, f)

        try:
            config = Config(config_file)
            self.assertEqual(config.DAILY_REVIEW_LIMIT, 300)
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
        word = Word(
            "apple",
            "苹果",
            success_count=3,
            review_round=1,
            review_count=2,
            added_at="2026-07-21T09:30:00+08:00",
        )
        word_dict = word.to_dict()
        
        self.assertEqual(word_dict['english'], "apple")
        self.assertEqual(word_dict['chinese'], "苹果")
        self.assertEqual(word_dict['success_count'], 3)
        self.assertEqual(word_dict['review_round'], 1)
        self.assertEqual(word_dict['review_count'], 2)
        self.assertEqual(word_dict['added_at'], "2026-07-21T09:30:00+08:00")
    
    def test_word_from_dict(self):
        """测试从字典创建单词"""
        word_dict = {
            'english': 'banana',
            'chinese': '香蕉',
            'success_count': 2,
            'next_review_date': '2026-01-31',
            'example': 'I like banana._我喜欢香蕉。',
            'review_round': 0,
            'review_count': 1,
            'added_at': '2026-01-30T18:00:00+08:00',
        }
        word = Word.from_dict(word_dict)
        
        self.assertEqual(word.english, "banana")
        self.assertEqual(word.chinese, "香蕉")
        self.assertEqual(word.success_count, 2)
        self.assertEqual(word.next_review_date, date(2026, 1, 31))
        self.assertEqual(word.added_at, '2026-01-30T18:00:00+08:00')
    
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

    def test_word_from_dict_ignores_future_fields(self):
        """未知逐词字段不能导致整个仓储加载失败。"""
        word_dict = {
            'english': 'future',
            'chinese': '未来',
            'next_review_date': '2026-07-13',
            'future_scheduler': {'version': 99},
        }
        original = dict(word_dict)
        word = Word.from_dict(word_dict)
        self.assertEqual(word.english, 'future')
        self.assertEqual(word_dict, original)

    def test_spelling_similarity_handles_transposition_and_prefix_families(self):
        self.assertGreaterEqual(spelling_similarity('form', 'from'), 2 / 3)
        self.assertGreaterEqual(spelling_similarity('act', 'action'), 2 / 3)
        self.assertEqual(spelling_similarity('book', 'zebra'), 0.0)

    def test_spelling_order_keeps_membership_and_unrelated_order(self):
        words = ['affect', 'banana', 'effect', 'zebra']
        ordered = order_by_spelling_similarity(words, lambda value: value)

        self.assertCountEqual(ordered, words)
        self.assertEqual(abs(ordered.index('affect') - ordered.index('effect')), 1)
        self.assertLess(ordered.index('banana'), ordered.index('zebra'))


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
        sidecar = Path(self.temp_data.name).with_name(
            f'{Path(self.temp_data.name).stem}.learning_state_v2.json'
        )
        if sidecar.exists():
            sidecar.unlink()
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

    def test_learning_state_v2_round_trip(self):
        """新状态保存在根级 sidecar，不污染旧 Word 结构。"""
        self.repository.learning_state_v2 = {
            'version': 1,
            'review_states': {'apple': {'scheduler': {'active': True}}},
            'daily_task': {'task_id': 'task-1', 'items': []},
        }
        self.repository.save_data([Word('apple', '苹果')], [])
        raw = json.loads(Path(self.temp_data.name).read_text(encoding='utf-8'))
        self.assertNotIn('scheduler', raw['all_words'][0])

        repository = WordRepository(self.config)
        repository.load_data()
        self.assertIn('apple', repository.learning_state_v2['review_states'])
        self.assertEqual(repository.learning_state_v2['daily_task']['task_id'], 'task-1')

    def test_sidecar_survives_legacy_main_file_save(self):
        self.repository.learning_state_v2 = {
            'version': 1,
            'review_states': {'apple': {'future_field': 'kept'}},
            'daily_task': None,
            'future_root': {'enabled': True},
        }
        self.repository.save_data([Word('apple', '苹果')], [])
        Path(self.temp_data.name).write_text(
            json.dumps(
                {
                    'all_words': [Word('apple', '苹果').to_dict()],
                    'mastered_words': [],
                },
                ensure_ascii=False,
            ),
            encoding='utf-8',
        )

        repository = WordRepository(self.config)
        repository.load_data()
        self.assertEqual(repository.learning_state_v2['future_root'], {'enabled': True})
        self.assertEqual(
            repository.learning_state_v2['review_states']['apple']['future_field'],
            'kept',
        )

    def test_main_learning_state_takes_precedence_over_stale_sidecar(self):
        sidecar = self.repository._learning_state_sidecar_path()
        stale_state = {
            'version': 1,
            'review_states': {'apple': {'source': 'stale-sidecar'}},
            'daily_task': None,
        }
        sidecar.write_text(json.dumps(stale_state), encoding='utf-8')
        main_data = {
            'all_words': [Word('apple', 'apple').to_dict()],
            'mastered_words': [],
            'learning_state_v2': {
                'version': 1,
                'review_states': {'apple': {'source': 'main'}},
                'daily_task': None,
            },
        }
        Path(self.temp_data.name).write_text(json.dumps(main_data), encoding='utf-8')

        repository = WordRepository(self.config)
        repository.load_data()
        self.assertEqual(
            repository.learning_state_v2['review_states']['apple']['source'],
            'main',
        )

        del main_data['learning_state_v2']
        Path(self.temp_data.name).write_text(json.dumps(main_data), encoding='utf-8')
        fallback_repository = WordRepository(self.config)
        fallback_repository.load_data()
        self.assertEqual(
            fallback_repository.learning_state_v2['review_states']['apple']['source'],
            'stale-sidecar',
        )

    def test_invalid_word_fields_do_not_empty_other_rows(self):
        Path(self.temp_data.name).write_text(
            json.dumps(
                {
                    'all_words': [
                        {
                            'english': 'bad-fields',
                            'chinese': '坏字段',
                            'success_count': 'not-an-int',
                            'next_review_date': 'not-a-date',
                        },
                        Word('valid', '有效').to_dict(),
                    ],
                    'mastered_words': [],
                },
                ensure_ascii=False,
            ),
            encoding='utf-8',
        )
        words, _ = self.repository.load_data()
        self.assertEqual([word.english for word in words], ['bad-fields', 'valid'])
        self.assertEqual(words[0].success_count, 0)
        self.assertEqual(words[0].next_review_date, china_today())
    
    def test_load_empty_data(self):
        """测试加载空数据"""
        all_words, mastered_words = self.repository.load_data()
        self.assertEqual(len(all_words), 0)
        self.assertEqual(len(mastered_words), 0)
    
    def test_load_corrupted_data(self):
        """损坏的数据必须阻止请求，不能伪装成空词库。"""
        with open(self.temp_data.name, 'w') as f:
            f.write("invalid json")

        with self.assertRaises(LearningDataLoadError):
            self.repository.load_data()
        self.assertEqual(Path(self.temp_data.name).read_text(), 'invalid json')

    def test_corrupted_sidecar_does_not_discard_daily_progress(self):
        main_data = {
            'all_words': [Word('apple', '苹果').to_dict()],
            'mastered_words': [],
        }
        Path(self.temp_data.name).write_text(
            json.dumps(main_data, ensure_ascii=False),
            encoding='utf-8',
        )
        sidecar = self.repository._learning_state_sidecar_path()
        sidecar.write_text('{broken', encoding='utf-8')

        with self.assertRaises(LearningDataLoadError):
            self.repository.load_data()
        self.assertEqual(sidecar.read_text(encoding='utf-8'), '{broken')

    def test_save_error_is_not_reported_as_success(self):
        with patch('reciter.os.replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.repository.save_data([Word('a', '甲')], [])


class TestWordReciter(unittest.TestCase):
    """测试核心复习系统"""
    
    def setUp(self):
        """创建临时测试环境"""
        self.temp_data = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        self.temp_data.close()

        self.temp_examples = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        json.dump({}, self.temp_examples)
        self.temp_examples.close()
        
        self.temp_config = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        config = {
            "data_file": self.temp_data.name,
            "example_db": self.temp_examples.name,
            "backup_enabled": False,
            "tts_enabled": False
        }
        json.dump(config, self.temp_config)
        self.temp_config.close()
        
        self.config = Config(self.temp_config.name)
    
    def tearDown(self):
        """清理临时文件"""
        os.unlink(self.temp_data.name)
        sidecar = Path(self.temp_data.name).with_name(
            f'{Path(self.temp_data.name).stem}.learning_state_v2.json'
        )
        if sidecar.exists():
            sidecar.unlink()
        os.unlink(self.temp_examples.name)
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
        yesterday = china_today() - timedelta(days=1)
        word = Word("test", "测试", next_review_date=yesterday)
        reciter.all_words.append(word)
        
        reciter._process_overdue_words()
        self.assertEqual(word.next_review_date, yesterday)

    def test_today_scheduled_first_then_carryover_oldest(self):
        """今日列表：今日排期优先，遗留在后（越早到期越靠前）"""
        reciter = WordReciter(self.config)
        t0 = china_today()
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
            Word("apple", "苹果", next_review_date=china_today()),
            Word("banana", "香蕉", next_review_date=china_today())
        ]
        future_word = Word("cat", "猫", next_review_date=china_today() + timedelta(days=7))
        
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
        w = Word("a", "甲", success_count=0, next_review_date=china_today())
        reciter.all_words.append(w)
        reciter.record_answer_correct(w, remedial=False)
        self.assertEqual(w.success_count, 1)
        self.assertEqual(w.review_count, 1)
        self.assertGreaterEqual(w.next_review_date, reciter.today)

    def test_record_answer_correct_remedial(self):
        """错题巩固答对：不增加 success_count，但排期到今日之后（与 Web 一致）"""
        reciter = WordReciter(self.config)
        w = Word("b", "乙", success_count=2, next_review_date=china_today())
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
        w = Word("d", "丁", success_count=0, next_review_date=china_today(), review_count=0)
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

    def test_bonus_practice_session_only_accepts_issued_words_once(self):
        reciter = WordReciter(self.config)
        reciter.all_words = [Word("alpha", "甲"), Word("beta", "乙")]
        session_id, picked = reciter.create_bonus_practice_session(1)
        issued = picked[0]
        other = "beta" if issued.english == "alpha" else "alpha"

        self.assertIsNotNone(
            reciter.resolve_bonus_practice_word(session_id, issued.english, "event-1")
        )
        self.assertIsNone(
            reciter.resolve_bonus_practice_word(session_id, other, "event-2")
        )
        self.assertTrue(
            reciter.complete_bonus_practice_word(
                session_id, issued.english, "event-1"
            )
        )
        self.assertIsNone(
            reciter.resolve_bonus_practice_word(session_id, issued.english, "event-2")
        )
        self.assertIsNotNone(
            reciter.resolve_bonus_practice_word(session_id, issued.english, "event-1")
        )

    def test_bonus_practice_session_requires_completed_daily_task(self):
        reciter = WordReciter(self.config)
        reciter.all_words = [Word("alpha", "甲", next_review_date=reciter.today)]
        reciter.get_today_learning_plan()

        with self.assertRaisesRegex(ValueError, "今日学习任务"):
            reciter.create_bonus_practice_session(1)

    def test_today_plan_prioritizes_reviews_then_fills_with_new_words(self):
        reciter = WordReciter(self.config)
        reciter.config.DAILY_REVIEW_LIMIT = 3
        today = reciter.today
        reciter.all_words = [
            Word('overdue', '逾期', success_count=2, next_review_date=today - timedelta(days=3)),
            Word('due', '到期', success_count=1, next_review_date=today),
            Word('new-a', '新甲', next_review_date=today),
            Word('new-b', '新乙', next_review_date=today),
            Word('future', '未来', success_count=2, next_review_date=today + timedelta(days=2)),
        ]
        task = reciter.get_today_learning_plan()
        english = [word.english for word in task['words']]
        self.assertEqual(english, ['overdue', 'due', 'new-a'])
        self.assertEqual(len([x for x in english if x.startswith('new-')]), 1)
        self.assertNotIn('new-b', english)
        self.assertNotIn('future', english)
        self.assertEqual(task['plan']['backlog_after_task'], 1)

    def test_new_words_first_overrides_full_overdue_task(self):
        reciter = WordReciter(self.config)
        reciter.config.DAILY_REVIEW_LIMIT = 3
        today = reciter.today
        reciter.all_words = [
            Word(
                f'overdue-{index}',
                f'逾期{index}',
                success_count=1,
                review_count=1,
                next_review_date=today - timedelta(days=1),
            )
            for index in range(3)
        ] + [
            Word(f'new-{index}', f'新词{index}', next_review_date=today)
            for index in range(2)
        ]

        task = reciter.get_today_learning_plan(new_words_first=True)

        self.assertEqual(
            [word.english for word in task['words']],
            ['new-0', 'new-1', 'overdue-0'],
        )
        self.assertEqual(task['plan']['new_word_target'], 2)

    def test_new_words_first_replaces_only_untouched_existing_items(self):
        reciter = WordReciter(self.config)
        reciter.config.DAILY_REVIEW_LIMIT = 3
        today = reciter.today
        overdue = [
            Word(
                f'overdue-{index}',
                f'逾期{index}',
                success_count=1,
                review_count=1,
                next_review_date=today - timedelta(days=1),
            )
            for index in range(3)
        ]
        new_words = [
            Word(f'new-{index}', f'新词{index}', next_review_date=today)
            for index in range(2)
        ]
        reciter.all_words = overdue + new_words
        initial = reciter.get_today_learning_plan()
        started_item = initial['items'][0]
        started_item['attempts'] = 1

        refreshed = reciter.get_today_learning_plan(new_words_first=True)

        self.assertEqual(
            [word.english for word in refreshed['words']],
            ['new-0', 'new-1', 'overdue-0'],
        )
        self.assertIn(started_item, refreshed['items'])
        self.assertNotIn('overdue-1', [word.english for word in refreshed['words']])
        self.assertNotIn('overdue-2', [word.english for word in refreshed['words']])

    def test_new_word_loses_priority_after_first_learning_attempt(self):
        reciter = WordReciter(self.config)
        reciter.config.DAILY_REVIEW_LIMIT = 2
        new_word = Word('new-word', '新词', next_review_date=reciter.today)
        due_word = Word(
            'due-word',
            '复习词',
            success_count=1,
            review_count=1,
            next_review_date=reciter.today,
        )
        reciter.all_words = [due_word, new_word]
        initial = reciter.get_today_learning_plan(new_words_first=True)
        self.assertEqual([word.english for word in initial['words']], ['new-word', 'due-word'])

        new_word.review_count = 1
        refreshed = reciter.get_today_learning_plan(new_words_first=True)

        self.assertEqual([word.english for word in refreshed['words']], ['due-word', 'new-word'])

    def test_today_import_replaces_untouched_regular_new_word(self):
        reciter = WordReciter(self.config)
        reciter.config.DAILY_REVIEW_LIMIT = 2
        reciter.all_words = [
            Word('due', '到期', success_count=1, next_review_date=reciter.today),
            Word('regular-new', '普通新词', next_review_date=reciter.today),
        ]
        initial = reciter.get_today_learning_plan()
        self.assertEqual([word.english for word in initial['words']], ['due', 'regular-new'])

        result = reciter.add_words_from_dicts([
            {'english': 'imported-today', 'chinese': '今日导入'},
        ])
        refreshed = reciter.get_today_learning_plan()

        self.assertEqual(result['added_to_today'], 1)
        self.assertEqual([word.english for word in refreshed['words']], ['due', 'imported-today'])
        imported_item = next(
            item for item in refreshed['items']
            if item['word_key'] == 'imported-today'
        )
        self.assertEqual(imported_item['reason'], 'new')
        self.assertTrue(imported_item['imported_today'])
        self.assertNotIn('regular-new', [item['word_key'] for item in refreshed['items']])

    def test_today_import_does_not_replace_started_new_word(self):
        reciter = WordReciter(self.config)
        reciter.config.DAILY_REVIEW_LIMIT = 1
        reciter.all_words = [Word('started-new', '已开始新词', next_review_date=reciter.today)]
        initial = reciter.get_today_learning_plan()
        initial['items'][0]['attempts'] = 1

        result = reciter.add_words_from_dicts([
            {'english': 'later-import', 'chinese': '稍后导入'},
        ])
        refreshed = reciter.get_today_learning_plan()

        self.assertEqual(result['added_to_today'], 0)
        self.assertEqual([word.english for word in refreshed['words']], ['started-new'])

    def test_today_import_reopens_completed_empty_task_within_new_word_target(self):
        reciter = WordReciter(self.config)
        reciter.config.DAILY_REVIEW_LIMIT = 3
        reciter.learning_state_v2['daily_task'] = {
            'version': 1,
            'task_id': 'completed-empty-task',
            'date': reciter.today.isoformat(),
            'status': 'completed',
            'available_at_creation': 0,
            'new_word_target': 2,
            'items': [],
        }

        result = reciter.add_words_from_dicts([
            {'english': 'same-day-import', 'chinese': '当天导入'},
        ])
        refreshed = reciter.get_today_learning_plan()

        self.assertEqual(result['added_to_today'], 1)
        self.assertEqual([word.english for word in refreshed['words']], ['same-day-import'])
        self.assertEqual(refreshed['plan']['remaining'], 1)

    def test_today_plan_groups_similar_spellings_without_changing_membership(self):
        reciter = WordReciter(self.config)
        reciter.config.DAILY_REVIEW_LIMIT = 4
        reciter.all_words = [
            Word('affect', '影响', success_count=1, next_review_date=reciter.today),
            Word('banana', '香蕉', success_count=1, next_review_date=reciter.today),
            Word('effect', '效果', success_count=1, next_review_date=reciter.today),
            Word('zebra', '斑马', success_count=1, next_review_date=reciter.today),
        ]

        task = reciter.get_today_learning_plan()
        english = [word.english for word in task['words']]

        self.assertCountEqual(english, ['affect', 'banana', 'effect', 'zebra'])
        self.assertEqual(abs(english.index('affect') - english.index('effect')), 1)
        self.assertEqual(task['plan']['remaining'], 4)

    def test_existing_task_clusters_only_untouched_items(self):
        reciter = WordReciter(self.config)
        reciter.all_words = [
            Word('banana', '香蕉', success_count=1, next_review_date=reciter.today),
            Word('affect', '影响', success_count=1, next_review_date=reciter.today),
            Word('zebra', '斑马', success_count=1, next_review_date=reciter.today),
            Word('effect', '效果', success_count=1, next_review_date=reciter.today),
        ]
        reciter.learning_state_v2['daily_task'] = {
            'version': 1,
            'task_id': 'existing-spelling-order',
            'date': reciter.today.isoformat(),
            'status': 'active',
            'available_at_creation': 4,
            'items': [
                {
                    'item_id': f'item-{index}',
                    'word_key': word.english,
                    'scheduled_due_date': reciter.today.isoformat(),
                    'exercise_type': 'spelling',
                    'reason': 'due',
                    'phase': 'main',
                    'status': 'pending',
                    'attempts': 1 if index == 0 else 0,
                }
                for index, word in enumerate(reciter.all_words)
            ],
        }

        task = reciter.get_today_learning_plan()
        english = [word.english for word in task['words']]

        self.assertEqual(english[0], 'banana')
        self.assertEqual(abs(english.index('affect') - english.index('effect')), 1)
        self.assertEqual(
            reciter.learning_state_v2['daily_task']['spelling_cluster_order_version'],
            1,
        )

    def test_today_plan_reserves_sixty_percent_for_reviews(self):
        reciter = WordReciter(self.config)
        reciter.config.DAILY_REVIEW_LIMIT = 10
        today = reciter.today
        reciter.all_words = [
            *[
                Word(f'new-{index:02d}', f'新词{index}', next_review_date=today)
                for index in range(10)
            ],
            *[
                Word(
                    f'due-{index:02d}',
                    f'复习{index}',
                    success_count=1,
                    next_review_date=today,
                )
                for index in range(10)
            ],
        ]

        task = reciter.get_today_learning_plan()
        reasons = [item['reason'] for item in task['items']]
        self.assertEqual(reasons[:6], ['due'] * 6)
        self.assertEqual(reasons[6:], ['new'] * 4)
        self.assertEqual(task['plan']['review_reserve'], 6)
        self.assertEqual(task['plan']['new_word_target'], 10)

    def test_legacy_cold_start_task_balances_core_exercises(self):
        reciter = WordReciter(self.config)
        reciter.config.DAILY_REVIEW_LIMIT = 10
        reciter.all_words = [
            Word(
                f'legacy-{index:02d}',
                f'旧词{index}',
                success_count=2,
                review_count=3,
                next_review_date=reciter.today,
            )
            for index in range(10)
        ]

        task = reciter.get_today_learning_plan()

        exercise_types = [item['exercise_type'] for item in task['items']]
        self.assertEqual(
            exercise_types,
            ['recognition', 'context', 'spelling', 'recognition', 'context'] * 2,
        )
        self.assertEqual(
            task['plan']['exercise_mix'],
            {'recognition': 4, 'context': 4, 'spelling': 2},
        )
        self.assertEqual(task['plan']['calibrations'], {'legacy': 10})
        self.assertTrue(
            all(item.get('calibration_reason') == 'legacy' for item in task['items'])
        )

    def test_new_words_still_start_with_recognition(self):
        reciter = WordReciter(self.config)
        reciter.config.DAILY_REVIEW_LIMIT = 5
        reciter.all_words = [
            Word(f'new-{index}', f'新词{index}', next_review_date=reciter.today)
            for index in range(5)
        ]

        task = reciter.get_today_learning_plan()

        self.assertEqual(
            [item['exercise_type'] for item in task['items']],
            ['recognition'] * 5,
        )
        self.assertEqual(task['plan']['calibrations'], {})

    def test_existing_today_task_only_rebalances_untouched_legacy_items(self):
        reciter = WordReciter(self.config)
        reciter.all_words = [
            Word(
                f'legacy-pending-{index}',
                f'旧词{index}',
                success_count=2,
                review_count=2,
                next_review_date=reciter.today,
            )
            for index in range(6)
        ]
        items = [
            {
                'item_id': f'item-{index}',
                'word_key': reciter.word_state_key(word),
                'scheduled_due_date': reciter.today.isoformat(),
                'exercise_type': 'recognition',
                'question_id': f'question-{index}',
                'reason': 'due',
                'phase': 'main',
                'status': 'pending',
                'attempts': 1 if index == 0 else 0,
            }
            for index, word in enumerate(reciter.all_words)
        ]
        reciter.learning_state_v2['daily_task'] = {
            'version': 1,
            'task_id': 'legacy-existing-task',
            'date': reciter.today.isoformat(),
            'status': 'active',
            'available_at_creation': 6,
            'items': items,
        }

        task = reciter.get_today_learning_plan()

        self.assertEqual(
            [item['exercise_type'] for item in task['items']],
            ['recognition', 'recognition', 'context', 'spelling', 'recognition', 'context'],
        )
        self.assertNotIn('calibration_reason', task['items'][0])
        self.assertEqual(task['items'][0]['question_id'], 'question-0')
        self.assertEqual(task['items'][1]['question_id'], 'question-1')
        self.assertNotIn('question_id', task['items'][2])
        self.assertNotIn('question_id', task['items'][3])
        self.assertEqual(task['plan']['calibrations'], {'legacy': 5})

        restored = reciter.get_today_learning_plan()
        self.assertEqual(
            [item['exercise_type'] for item in restored['items']],
            [item['exercise_type'] for item in task['items']],
        )

    def test_real_core_attempt_disables_legacy_calibration(self):
        reciter = WordReciter(self.config)
        word = Word(
            'already-measured',
            '已测量',
            success_count=2,
            review_count=3,
            next_review_date=reciter.today,
        )
        reciter.all_words = [word]
        reciter.record_mastery_attempt(
            word,
            'spelling',
            True,
            event_id='real-spelling-attempt',
        )

        task = reciter.get_today_learning_plan()

        self.assertEqual(task['plan']['calibrations'], {})
        self.assertNotIn('calibration_reason', task['items'][0])

    def test_automatic_new_word_target_uses_recent_accuracy(self):
        reciter = WordReciter(self.config)
        reciter.config.DAILY_REVIEW_LIMIT = 120
        cases = ((95, 30), (85, 20), (75, 15), (65, 5))
        for correct, expected in cases:
            with self.subTest(correct=correct):
                reciter.learning_state_v2['daily_performance'] = {
                    reciter.today.isoformat(): {'attempts': 100, 'correct': correct},
                }
                self.assertEqual(reciter.automatic_new_word_target(), expected)

        reciter.learning_state_v2['daily_performance'] = {}
        self.assertEqual(reciter.automatic_new_word_target(), 15)

    def test_overdue_backlog_reduces_automatic_new_word_target(self):
        reciter = WordReciter(self.config)
        reciter.config.DAILY_REVIEW_LIMIT = 120
        reciter.learning_state_v2['daily_performance'] = {
            reciter.today.isoformat(): {'attempts': 100, 'correct': 95},
        }
        self.assertEqual(reciter.automatic_new_word_target(overdue_count=60), 5)
        self.assertEqual(reciter.automatic_new_word_target(overdue_count=120), 0)

    def test_low_task_completion_reduces_automatic_new_word_target(self):
        reciter = WordReciter(self.config)
        reciter.config.DAILY_REVIEW_LIMIT = 120
        reciter.learning_state_v2['daily_performance'] = {
            reciter.today.isoformat(): {'attempts': 100, 'correct': 95},
        }
        yesterday = (reciter.today - timedelta(days=1)).isoformat()
        reciter.learning_state_v2['daily_task_history'] = {
            yesterday: {
                'total': 20,
                'completed': 10,
                'attempts': 10,
                'difficult': 0,
            },
        }

        self.assertEqual(reciter.automatic_new_word_target(), 5)

    def test_daily_performance_is_idempotent_and_excludes_remedial(self):
        reciter = WordReciter(self.config)
        word = Word('tracked', '记录', next_review_date=reciter.today)
        reciter.all_words = [word]
        task = reciter.get_today_learning_plan()
        item = task['items'][0]

        first = reciter.apply_scored_review_attempt(
            word,
            exercise_type='spelling',
            correct=False,
            task_item=item,
            event_id='performance-event',
        )
        replay = reciter.apply_scored_review_attempt(
            word,
            exercise_type='spelling',
            correct=False,
            task_item=item,
            event_id='performance-event',
        )

        self.assertEqual(replay, first)
        self.assertEqual(
            reciter.recent_performance_snapshot(),
            {'days': 7, 'attempts': 1, 'correct': 0, 'accuracy': 0.0},
        )

    def test_overdue_unseen_words_stay_new_while_attempted_word_is_weak(self):
        reciter = WordReciter(self.config)
        reciter.config.DAILY_REVIEW_LIMIT = 3
        yesterday = reciter.today - timedelta(days=1)
        attempted = Word('attempted', 'attempted', next_review_date=reciter.today)
        reciter.all_words = [
            Word('unseen-a', 'unseen-a', next_review_date=yesterday),
            Word('unseen-b', 'unseen-b', next_review_date=yesterday),
            attempted,
        ]
        reciter.record_mastery_attempt(
            attempted,
            'spelling',
            False,
            event_id='attempted-once',
        )

        reasons = {
            item['word'].english: item['reason']
            for item in reciter._today_task_candidates()
        }
        self.assertEqual(reasons['unseen-a'], 'new')
        self.assertEqual(reasons['unseen-b'], 'new')
        self.assertEqual(reasons['attempted'], 'weak')

        task = reciter.get_today_learning_plan()
        task_reasons = [item['reason'] for item in task['items']]
        self.assertIn('weak', task_reasons)
        self.assertEqual(task_reasons.count('new'), 2)
        self.assertEqual(task['plan']['backlog_after_task'], 0)

    def test_daily_limit_does_not_create_unlimited_second_batch(self):
        reciter = WordReciter(self.config)
        reciter.config.DAILY_REVIEW_LIMIT = 1
        reciter.all_words = [
            Word('new-a', '新甲', next_review_date=reciter.today),
            Word('new-b', '新乙', next_review_date=reciter.today),
            Word('new-c', '新丙', next_review_date=reciter.today),
        ]
        task = reciter.get_today_learning_plan()
        word = task['words'][0]
        item = task['items'][0]
        reciter.apply_scored_review_attempt(
            word,
            exercise_type=item['exercise_type'],
            correct=True,
            task_item=item,
            event_id='daily-limit',
        )
        after = reciter.get_today_learning_plan()
        self.assertEqual(after['plan']['task_id'], task['plan']['task_id'])
        self.assertEqual(after['words'], [])
        self.assertEqual(after['plan']['backlog_after_task'], 2)

    def test_completed_daily_limit_survives_reload_without_second_batch(self):
        reciter = WordReciter(self.config)
        reciter.config.DAILY_REVIEW_LIMIT = 1
        reciter.all_words = [
            Word('new-a', '新甲', next_review_date=reciter.today),
            Word('new-b', '新乙', next_review_date=reciter.today),
        ]
        task = reciter.get_today_learning_plan()
        word = task['words'][0]
        item = task['items'][0]
        reciter.apply_scored_review_attempt(
            word,
            exercise_type=item['exercise_type'],
            correct=True,
            task_item=item,
            event_id='daily-limit-reload',
        )
        reciter.save_learning_data(backup=False)

        reloaded = WordReciter(self.config)
        after = reloaded.get_today_learning_plan()
        self.assertEqual(after['plan']['task_id'], task['plan']['task_id'])
        self.assertEqual(after['words'], [])
        self.assertEqual(after['plan']['total'], 1)
        self.assertEqual(after['plan']['completed'], 1)
        self.assertEqual(after['plan']['remaining'], 0)
        self.assertEqual(after['plan']['backlog_after_task'], 1)

    def test_corrupt_today_task_items_are_rebuilt(self):
        for corrupt_items in ({'not': 'a list'}, [{'word_key': 'missing-word'}, None]):
            with self.subTest(corrupt_items=corrupt_items):
                reciter = WordReciter(self.config)
                reciter.all_words = [
                    Word('recover-task', '恢复任务', next_review_date=reciter.today)
                ]
                reciter.learning_state_v2['daily_task'] = {
                    'version': 1,
                    'task_id': 'corrupt-task',
                    'date': reciter.today.isoformat(),
                    'status': 'active',
                    'items': corrupt_items,
                }

                rebuilt = reciter.get_today_learning_plan()

                self.assertNotEqual(rebuilt['plan']['task_id'], 'corrupt-task')
                self.assertEqual([word.english for word in rebuilt['words']], ['recover-task'])

    def test_completed_empty_task_does_not_create_another_same_day_batch(self):
        reciter = WordReciter(self.config)
        reciter.all_words = [Word('later-import', '稍后导入', next_review_date=reciter.today)]
        reciter.learning_state_v2['daily_task'] = {
            'version': 1,
            'task_id': 'completed-empty-task',
            'date': reciter.today.isoformat(),
            'status': 'completed',
            'available_at_creation': 0,
            'items': [],
        }

        plan = reciter.get_today_learning_plan()

        self.assertEqual(plan['plan']['task_id'], 'completed-empty-task')
        self.assertEqual(plan['words'], [])

    def test_future_weak_word_can_enter_today_plan(self):
        reciter = WordReciter(self.config)
        word = Word(
            'weak',
            '薄弱',
            success_count=2,
            next_review_date=reciter.today + timedelta(days=5),
        )
        reciter.all_words = [word]
        for index in range(3):
            reciter.record_mastery_attempt(
                word,
                'listening',
                False,
                event_id=f'weak-{index}',
            )
        task = reciter.get_today_learning_plan()
        self.assertEqual([w.english for w in task['words']], ['weak'])
        self.assertEqual(task['items'][0]['reason'], 'weak')

    def test_pending_future_weak_item_survives_reload_before_answering(self):
        reciter = WordReciter(self.config)
        word = Word(
            'weak-refresh',
            '刷新薄弱',
            success_count=2,
            next_review_date=reciter.today + timedelta(days=5),
        )
        reciter.all_words = [word]
        for index in range(3):
            reciter.record_mastery_attempt(
                word,
                'listening',
                False,
                event_id=f'weak-refresh-{index}',
            )
        task = reciter.get_today_learning_plan()
        reciter.save_learning_data(backup=False)

        reloaded = WordReciter(self.config)
        restored = reloaded.get_today_learning_plan()
        self.assertEqual(restored['plan']['task_id'], task['plan']['task_id'])
        self.assertEqual([w.english for w in restored['words']], ['weak-refresh'])
        self.assertEqual(restored['items'][0]['reason'], 'weak')
        self.assertEqual(restored['items'][0]['attempts'], 0)
        self.assertEqual(restored['plan']['remaining'], 1)

    def test_today_task_survives_save_and_reload(self):
        reciter = WordReciter(self.config)
        reciter.all_words = [Word('persist', '持久', next_review_date=reciter.today)]
        task = reciter.get_today_learning_plan()
        task_id = task['plan']['task_id']
        reciter.save_learning_data(backup=False)

        reloaded = WordReciter(self.config)
        restored = reloaded.get_today_learning_plan()
        self.assertEqual(restored['plan']['task_id'], task_id)
        self.assertEqual([w.english for w in restored['words']], ['persist'])

    def test_completed_task_item_does_not_return_after_reload(self):
        reciter = WordReciter(self.config)
        word = Word('done', '完成', next_review_date=reciter.today)
        reciter.all_words = [word]
        task = reciter.get_today_learning_plan()
        item = task['items'][0]
        event_id = 'done-event'
        self.assertTrue(
            reciter.record_mastery_attempt(
                word,
                item['exercise_type'],
                True,
                event_id=event_id,
            )
        )
        reciter.record_daily_task_attempt(item, event_id)
        reciter.record_answer_correct(word)
        reciter.complete_daily_task_item(item, event_id)
        reciter.save_learning_data(backup=False)

        reloaded = WordReciter(self.config)
        restored = reloaded.get_today_learning_plan()
        self.assertEqual(restored['words'], [])
        self.assertGreater(reloaded.find_word('done').next_review_date, reloaded.today)

    def test_scored_task_attempt_is_idempotent(self):
        reciter = WordReciter(self.config)
        word = Word('once', '一次', next_review_date=reciter.today)
        reciter.all_words = [word]
        task = reciter.get_today_learning_plan()
        item = task['items'][0]
        first = reciter.apply_scored_review_attempt(
            word,
            exercise_type=item['exercise_type'],
            correct=True,
            task_item=item,
            event_id='same-event',
        )
        duplicate = reciter.apply_scored_review_attempt(
            word,
            exercise_type=item['exercise_type'],
            correct=True,
            task_item=item,
            event_id='same-event',
        )
        self.assertTrue(first['recorded'])
        self.assertEqual(duplicate, first)
        self.assertEqual(word.success_count, 1)
        self.assertEqual(word.review_count, 1)
        self.assertEqual(item['attempts'], 1)
        self.assertEqual(item['status'], 'completed')

    def test_completed_event_replays_exactly_after_reload(self):
        reciter = WordReciter(self.config)
        word = Word('reload-replay', '重载重放', next_review_date=reciter.today)
        reciter.all_words = [word]
        task = reciter.get_today_learning_plan()
        item = task['items'][0]
        first = reciter.apply_scored_review_attempt(
            word,
            exercise_type=item['exercise_type'],
            correct=True,
            task_item=item,
            event_id='reload-replay-event',
        )
        reciter.save_learning_data(backup=False)

        reloaded = WordReciter(self.config)
        restored_word = reloaded.find_word('reload-replay')
        restored_item = reloaded.learning_state_v2['daily_task']['items'][0]
        replay = reloaded.apply_scored_review_attempt(
            restored_word,
            exercise_type=restored_item['exercise_type'],
            correct=True,
            task_item=restored_item,
            event_id='reload-replay-event',
        )

        self.assertEqual(replay, first)
        self.assertEqual(restored_word.success_count, 1)
        self.assertEqual(restored_word.review_count, 1)
        self.assertEqual(restored_item['attempts'], 1)

    def test_bonus_event_replays_exactly_after_reload(self):
        reciter = WordReciter(self.config)
        word = Word('bonus-reload', '加练重放', next_review_date=reciter.today)
        reciter.all_words = [word]
        first = reciter.apply_scored_review_attempt(
            word,
            exercise_type='spelling',
            correct=True,
            event_id='bonus-reload-event',
            bonus_practice=True,
        )
        reciter.save_learning_data(backup=False)

        reloaded = WordReciter(self.config)
        restored_word = reloaded.find_word('bonus-reload')
        replay = reloaded.apply_scored_review_attempt(
            restored_word,
            exercise_type='spelling',
            correct=True,
            event_id='bonus-reload-event',
            bonus_practice=True,
        )

        self.assertEqual(replay, first)
        self.assertEqual(restored_word.review_count, 1)

    def test_earlier_wrong_event_replays_original_result_after_later_attempt(self):
        reciter = WordReciter(self.config)
        word = Word('older-replay', '较早重放', next_review_date=reciter.today)
        reciter.all_words = [word]
        item = reciter.get_today_learning_plan()['items'][0]
        first = reciter.apply_scored_review_attempt(
            word,
            exercise_type=item['exercise_type'],
            correct=False,
            task_item=item,
            event_id='older-wrong-event',
        )
        reciter.apply_scored_review_attempt(
            word,
            exercise_type=item['exercise_type'],
            correct=False,
            task_item=item,
            event_id='newer-wrong-event',
        )

        replay = reciter.apply_scored_review_attempt(
            word,
            exercise_type=item['exercise_type'],
            correct=False,
            task_item=item,
            event_id='older-wrong-event',
        )

        self.assertEqual(replay, first)
        self.assertEqual(item['attempts'], 2)
        self.assertEqual(word.review_count, 2)

    def test_cli_daily_review_uses_persistent_daily_task_limit(self):
        reciter = WordReciter(self.config)
        reciter.config.DAILY_REVIEW_LIMIT = 1
        reciter.all_words = [
            Word('cli-a', '甲', next_review_date=reciter.today),
            Word('cli-b', '乙', next_review_date=reciter.today),
        ]

        class FakeTable:
            field_names = []

            def add_row(self, _row):
                return None

        def answer_correct(_word, remedial=False):
            self.assertFalse(remedial)
            reciter._last_practice_wrong_attempts = 0
            return True

        with (
            patch.object(reciter, '_practice_word', side_effect=answer_correct),
            patch('reciter.PrettyTable', FakeTable),
            patch('builtins.print'),
        ):
            reciter.daily_review()
            reciter.daily_review()

        task = reciter.learning_state_v2['daily_task']
        self.assertEqual(len(task['items']), 1)
        self.assertEqual(task['items'][0]['status'], 'completed')
        self.assertEqual(sum(word.success_count for word in reciter.all_words), 1)
        untouched = reciter.find_word('cli-b')
        self.assertEqual(untouched.success_count, 0)

    def test_cli_remedial_round_reschedules_each_three_failed_attempts(self):
        reciter = WordReciter(self.config)
        reciter.config.DAILY_REVIEW_LIMIT = 1
        word = Word('cli-remedial', '命令行巩固', next_review_date=reciter.today)
        reciter.all_words = [word]
        outcomes = iter(((False, 3), (False, 3), (True, 0)))

        class FakeTable:
            field_names = []

            def add_row(self, _row):
                return None

        def practice(_word, remedial=False):
            success, wrong_attempts = next(outcomes)
            reciter._last_practice_wrong_attempts = wrong_attempts
            if success:
                self.assertTrue(remedial)
            return success

        with (
            patch.object(reciter, '_practice_word', side_effect=practice),
            patch('reciter.PrettyTable', FakeTable),
            patch('builtins.print'),
        ):
            reciter.daily_review()

        state = reciter.get_review_state(word)
        item = reciter.learning_state_v2['daily_task']['items'][0]
        self.assertEqual(item['attempts'], 7)
        self.assertEqual(item['status'], 'completed')
        self.assertEqual(state['scheduler']['lapses'], 2)
        self.assertGreater(word.next_review_date, reciter.today)

    def test_same_task_event_with_different_outcome_conflicts_without_mutation(self):
        reciter = WordReciter(self.config)
        word = Word('conflict', 'conflict', next_review_date=reciter.today)
        reciter.all_words = [word]
        task = reciter.get_today_learning_plan()
        item = task['items'][0]
        reciter.apply_scored_review_attempt(
            word,
            exercise_type=item['exercise_type'],
            correct=False,
            task_item=item,
            event_id='same-id-different-answer',
        )
        before_word = (word.success_count, word.review_count, word.next_review_date)
        before_item = json.loads(json.dumps(item))
        before_state = json.loads(json.dumps(reciter.get_review_state(word)))

        with self.assertRaises(ReviewEventConflict):
            reciter.apply_scored_review_attempt(
                word,
                exercise_type=item['exercise_type'],
                correct=True,
                task_item=item,
                event_id='same-id-different-answer',
            )

        self.assertEqual(
            (word.success_count, word.review_count, word.next_review_date),
            before_word,
        )
        self.assertEqual(item, before_item)
        self.assertEqual(reciter.get_review_state(word), before_state)

    def test_same_event_with_changed_elapsed_time_conflicts(self):
        reciter = WordReciter(self.config)
        word = Word('elapsed-conflict', '耗时冲突', next_review_date=reciter.today)
        reciter.all_words = [word]
        item = reciter.get_today_learning_plan()['items'][0]
        reciter.apply_scored_review_attempt(
            word,
            exercise_type=item['exercise_type'],
            correct=False,
            task_item=item,
            event_id='elapsed-conflict-event',
            elapsed_ms=100,
        )

        with self.assertRaises(ReviewEventConflict):
            reciter.apply_scored_review_attempt(
                word,
                exercise_type=item['exercise_type'],
                correct=False,
                task_item=item,
                event_id='elapsed-conflict-event',
                elapsed_ms=200,
            )

    def test_non_finite_elapsed_time_is_normalized(self):
        reciter = WordReciter(self.config)
        word = Word('elapsed-infinity', '无限耗时', next_review_date=reciter.today)
        reciter.all_words = [word]
        item = reciter.get_today_learning_plan()['items'][0]
        first = reciter.apply_scored_review_attempt(
            word,
            exercise_type=item['exercise_type'],
            correct=False,
            task_item=item,
            event_id='elapsed-infinity-event',
            elapsed_ms=float('inf'),
        )
        replay = reciter.apply_scored_review_attempt(
            word,
            exercise_type=item['exercise_type'],
            correct=False,
            task_item=item,
            event_id='elapsed-infinity-event',
            elapsed_ms=0,
        )

        self.assertEqual(replay, first)
        self.assertEqual(item['attempts'], 1)

    def test_completed_task_accepts_cached_earlier_event_replay(self):
        reciter = WordReciter(self.config)
        word = Word('completed-replay', '完成后重放', next_review_date=reciter.today)
        reciter.all_words = [word]
        task = reciter.get_today_learning_plan()
        item = task['items'][0]
        first = reciter.apply_scored_review_attempt(
            word,
            exercise_type=item['exercise_type'],
            correct=False,
            task_item=item,
            event_id='completed-earlier-event',
        )
        reciter.apply_scored_review_attempt(
            word,
            exercise_type=item['exercise_type'],
            correct=True,
            task_item=item,
            event_id='completed-latest-event',
        )
        before = (item['attempts'], word.success_count, word.review_count)

        resolved = reciter.resolve_daily_task_item(
            task['plan']['task_id'],
            item['item_id'],
            word.english,
            'completed-earlier-event',
        )
        replay = reciter.apply_scored_review_attempt(
            word,
            exercise_type=item['exercise_type'],
            correct=False,
            task_item=resolved,
            event_id='completed-earlier-event',
        )

        self.assertIs(resolved, item)
        self.assertEqual(replay, first)
        self.assertEqual((item['attempts'], word.success_count, word.review_count), before)

    def test_exact_retry_of_third_correct_attempt_returns_cached_result(self):
        reciter = WordReciter(self.config)
        word = Word('third-correct', 'third-correct', next_review_date=reciter.today)
        reciter.all_words = [word]
        task = reciter.get_today_learning_plan()
        item = task['items'][0]
        item['exercise_type'] = 'spelling'
        for index in range(2):
            reciter.apply_scored_review_attempt(
                word,
                exercise_type='spelling',
                correct=False,
                task_item=item,
                event_id=f'third-correct-wrong-{index}',
            )
        first = reciter.apply_scored_review_attempt(
            word,
            exercise_type='spelling',
            correct=True,
            task_item=item,
            event_id='third-correct-event',
        )
        before = (item['attempts'], word.success_count, word.review_count)

        replay = reciter.apply_scored_review_attempt(
            word,
            exercise_type='spelling',
            correct=True,
            task_item=item,
            event_id='third-correct-event',
        )

        self.assertEqual(replay, first)
        self.assertEqual(before, (3, 1, 3))
        self.assertEqual((item['attempts'], word.success_count, word.review_count), before)

    def test_exact_retry_of_third_incorrect_attempt_does_not_repeat_lapse(self):
        reciter = WordReciter(self.config)
        word = Word('third-wrong', 'third-wrong', next_review_date=reciter.today)
        reciter.all_words = [word]
        task = reciter.get_today_learning_plan()
        item = task['items'][0]
        item['exercise_type'] = 'spelling'
        for index in range(2):
            reciter.apply_scored_review_attempt(
                word,
                exercise_type='spelling',
                correct=False,
                task_item=item,
                event_id=f'third-wrong-{index}',
            )
        first = reciter.apply_scored_review_attempt(
            word,
            exercise_type='spelling',
            correct=False,
            task_item=item,
            event_id='third-wrong-event',
        )
        state = reciter.get_review_state(word)
        before = (item['attempts'], word.review_count, state['scheduler']['lapses'])

        replay = reciter.apply_scored_review_attempt(
            word,
            exercise_type='spelling',
            correct=False,
            task_item=item,
            event_id='third-wrong-event',
        )

        self.assertEqual(replay, first)
        self.assertEqual(before, (3, 3, 1))
        self.assertEqual(
            (item['attempts'], word.review_count, state['scheduler']['lapses']),
            before,
        )

    def test_second_failed_semantic_attempt_schedules_again(self):
        reciter = WordReciter(self.config)
        word = Word('semantic-retry', '语义重试', next_review_date=reciter.today)
        reciter.all_words = [word]
        item = reciter.get_today_learning_plan()['items'][0]
        item['exercise_type'] = 'context'

        first = reciter.apply_scored_review_attempt(
            word,
            exercise_type='context',
            correct=False,
            task_item=item,
            event_id='semantic-wrong-1',
        )
        state = reciter.get_review_state(word)
        self.assertEqual(first['attempt_limit'], 2)
        self.assertFalse(first['final_attempt'])
        self.assertEqual(item['phase'], 'main')
        self.assertEqual(state['scheduler']['lapses'], 0)

        second = reciter.apply_scored_review_attempt(
            word,
            exercise_type='context',
            correct=False,
            task_item=item,
            event_id='semantic-wrong-2',
        )

        self.assertEqual(second['attempt_number'], 2)
        self.assertTrue(second['final_attempt'])
        self.assertEqual(item['phase'], 'remedial')
        state = reciter.get_review_state(word)
        self.assertEqual(state['scheduler']['lapses'], 1)
        self.assertEqual(word.next_review_date, reciter.today + timedelta(days=1))

    def test_third_failed_spelling_attempt_schedules_again_but_keeps_task_pending(self):
        reciter = WordReciter(self.config)
        word = Word('retry', '重试', next_review_date=reciter.today)
        reciter.all_words = [word]
        task = reciter.get_today_learning_plan()
        item = task['items'][0]
        item['exercise_type'] = 'spelling'
        results = []
        for index in range(3):
            results.append(reciter.apply_scored_review_attempt(
                word,
                exercise_type='spelling',
                correct=False,
                task_item=item,
                event_id=f'wrong-{index}',
            ))
        state = reciter.get_review_state(word)
        self.assertEqual([result['final_attempt'] for result in results], [False, False, True])
        self.assertTrue(all(result['attempt_limit'] == 3 for result in results))
        self.assertEqual(item['attempts'], 3)
        self.assertEqual(item['status'], 'pending')
        self.assertEqual(item['phase'], 'remedial')
        self.assertEqual(state['scheduler']['lapses'], 1)
        self.assertEqual(word.next_review_date, reciter.today + timedelta(days=1))

    def test_reloaded_remedial_item_cannot_advance_main_progress(self):
        reciter = WordReciter(self.config)
        word = Word('refresh-remedial', '刷新巩固', success_count=3, next_review_date=reciter.today)
        reciter.all_words = [word]
        task = reciter.get_today_learning_plan()
        item = task['items'][0]
        for index in range(3):
            reciter.apply_scored_review_attempt(
                word,
                exercise_type=item['exercise_type'],
                correct=False,
                task_item=item,
                event_id=f'refresh-wrong-{index}',
            )
        reciter.save_learning_data(backup=False)

        reloaded = WordReciter(self.config)
        restored = reloaded.get_today_learning_plan()
        restored_word = restored['words'][0]
        restored_item = restored['items'][0]
        result = reloaded.apply_scored_review_attempt(
            restored_word,
            exercise_type=restored_item['exercise_type'],
            correct=True,
            task_item=restored_item,
            event_id='refresh-correct',
            remedial=False,
        )
        self.assertTrue(result['remedial'])
        self.assertEqual(restored_word.success_count, 3)

    def test_bonus_and_remedial_do_not_change_multidimensional_mastery(self):
        reciter = WordReciter(self.config)
        bonus_word = Word('bonus', '加练', next_review_date=reciter.today)
        reciter.all_words = [bonus_word]
        before_bonus = json.loads(json.dumps(reciter.get_review_state(bonus_word)['mastery']))
        reciter.apply_scored_review_attempt(
            bonus_word,
            exercise_type='spelling',
            correct=True,
            event_id='bonus-event',
            bonus_practice=True,
        )
        self.assertEqual(reciter.get_review_state(bonus_word)['mastery'], before_bonus)

        remedial_word = Word('remedial', '巩固', next_review_date=reciter.today)
        reciter.all_words.append(remedial_word)
        task = reciter._create_today_task()
        item = next(x for x in task['items'] if x['word_key'] == 'remedial')
        item['phase'] = 'remedial'
        item['attempts'] = 3
        before_remedial = json.loads(json.dumps(reciter.get_review_state(remedial_word)['mastery']))
        result = reciter.apply_scored_review_attempt(
            remedial_word,
            exercise_type=item['exercise_type'],
            correct=True,
            task_item=item,
            event_id='remedial-event',
        )
        self.assertTrue(result['remedial'])
        self.assertEqual(reciter.get_review_state(remedial_word)['mastery'], before_remedial)

    def test_multidimensional_threshold_controls_mastery(self):
        reciter = WordReciter(self.config)
        not_ready = Word(
            'not-ready',
            '未准备',
            success_count=reciter.config.MAX_SUCCESS_COUNT - 1,
            next_review_date=reciter.today,
        )
        reciter.all_words = [not_ready]
        for index in range(2):
            reciter.record_mastery_attempt(
                not_ready,
                'spelling',
                True,
                event_id=f'spelling-only-{index}',
            )
        reciter.apply_scored_review_attempt(
            not_ready,
            exercise_type='spelling',
            correct=True,
            event_id='still-no-listening',
        )
        self.assertIn(not_ready, reciter.all_words)
        self.assertNotIn(not_ready, reciter.mastered_words)

        ready = Word(
            'ready',
            '准备',
            success_count=reciter.config.MAX_SUCCESS_COUNT - 1,
            next_review_date=reciter.today,
        )
        reciter.all_words.append(ready)
        for exercise_type, attempts in (('recognition', 3), ('context', 3), ('spelling', 2)):
            for index in range(attempts):
                reciter.record_mastery_attempt(
                    ready,
                    exercise_type,
                    True,
                    event_id=f'{exercise_type}-{index}',
                )
        result = reciter.apply_scored_review_attempt(
            ready,
            exercise_type='spelling',
            correct=True,
            event_id='master-ready',
        )
        self.assertTrue(result['mastered_now'])
        self.assertIn(ready, reciter.mastered_words)

    def test_listening_is_optional_for_gaokao_mastery(self):
        reciter = WordReciter(self.config)
        no_audio = Word(
            'no-audio',
            'no-audio',
            success_count=reciter.config.MAX_SUCCESS_COUNT - 1,
            next_review_date=reciter.today,
        )
        with_audio = Word(
            'with-audio',
            'with-audio',
            success_count=reciter.config.MAX_SUCCESS_COUNT - 1,
            next_review_date=reciter.today,
        )
        reciter.all_words = [no_audio, with_audio]
        for word, prefix in ((no_audio, 'no-audio'), (with_audio, 'with-audio')):
            for exercise_type, attempts in (('recognition', 3), ('context', 3), ('spelling', 2)):
                for index in range(attempts):
                    reciter.record_mastery_attempt(
                        word,
                        exercise_type,
                        True,
                        event_id=f'{prefix}-{exercise_type}-warmup-{index}',
                    )

        no_audio_result = reciter.apply_scored_review_attempt(
            no_audio,
            exercise_type='spelling',
            correct=True,
            event_id='no-audio-spelling',
            audio_available=False,
        )
        with_audio_result = reciter.apply_scored_review_attempt(
            with_audio,
            exercise_type='spelling',
            correct=True,
            event_id='with-audio-spelling',
            audio_available=True,
        )

        self.assertTrue(no_audio_result['mastered_now'])
        self.assertIn(no_audio, reciter.mastered_words)
        self.assertTrue(with_audio_result['mastered_now'])
        self.assertIn(with_audio, reciter.mastered_words)
        self.assertEqual(
            reciter.review_state_payload(with_audio)['mastery']['by_type']['listening']['attempts'],
            0,
        )

    def test_missing_semantic_questions_do_not_block_spelling_mastery(self):
        reciter = WordReciter(self.config)
        word = Word('basic-only', '仅基础题', next_review_date=reciter.today)
        reciter.all_words = [word]
        self.assertTrue(reciter.mark_exercise_unavailable(word, 'recognition'))
        self.assertTrue(reciter.mark_exercise_unavailable(word, 'context'))

        results = [
            reciter.apply_scored_review_attempt(
                word,
                exercise_type='spelling',
                correct=True,
                event_id=f'basic-spelling-{index}',
            )
            for index in range(8)
        ]

        self.assertTrue(all(not result['mastered_now'] for result in results[:-1]))
        self.assertIn('继续完成拼写目标', results[0]['message'])
        self.assertTrue(results[-1]['mastered_now'])
        self.assertIn(word, reciter.mastered_words)
        payload = reciter.review_state_payload(word)
        self.assertFalse(payload['mastery']['by_type']['recognition']['available'])
        self.assertEqual(
            payload['mastery']['overall_percent'],
            payload['mastery']['by_type']['spelling']['percent'],
        )

    def test_multidimensional_mastery_no_longer_requires_legacy_success_count(self):
        reciter = WordReciter(self.config)
        word = Word('direct-mastery', '直接掌握', next_review_date=reciter.today)
        reciter.all_words = [word]
        for exercise_type, attempts in (('recognition', 3), ('context', 2), ('spelling', 2)):
            for index in range(attempts):
                reciter.record_mastery_attempt(
                    word,
                    exercise_type,
                    True,
                    event_id=f'direct-{exercise_type}-{index}',
                )

        result = reciter.apply_scored_review_attempt(
            word,
            exercise_type='context',
            correct=True,
            event_id='direct-final-context',
        )

        self.assertTrue(result['mastered_now'])
        self.assertEqual(word.success_count, 1)
        self.assertIn(word, reciter.mastered_words)

    def test_existing_listening_task_downgrades_before_render_without_reliable_audio(self):
        reciter = WordReciter(self.config)
        word = Word(
            'audio-maintenance',
            '音频保持',
            success_count=reciter.config.MAX_SUCCESS_COUNT,
            next_review_date=reciter.today,
        )
        reciter.mastered_words = [word]
        reciter.learning_state_v2['daily_task'] = {
            'version': 1,
            'task_id': 'audio-task',
            'date': reciter.today.isoformat(),
            'items': [{
                'item_id': 'audio-item',
                'word_key': reciter.word_state_key(word),
                'scheduled_due_date': reciter.today.isoformat(),
                'exercise_type': 'listening',
                'reason': 'maintenance',
                'phase': 'main',
                'status': 'pending',
                'attempts': 0,
            }],
        }

        task = reciter.get_today_learning_plan(listening_available=False)

        self.assertNotEqual(task['items'][0]['exercise_type'], 'listening')

    def test_failed_maintenance_enters_reinforcement_and_can_recover(self):
        reciter = WordReciter(self.config)
        word = Word(
            'recover-memory',
            '恢复记忆',
            success_count=reciter.config.MAX_SUCCESS_COUNT,
            next_review_date=reciter.today,
        )
        reciter.mastered_words = [word]
        for exercise_type, attempts in (('recognition', 3), ('context', 3), ('spelling', 2)):
            for index in range(attempts):
                reciter.record_mastery_attempt(
                    word,
                    exercise_type,
                    True,
                    event_id=f'recover-warmup-{exercise_type}-{index}',
                )
        item = reciter.get_today_learning_plan()['items'][0]
        item['exercise_type'] = 'recognition'
        for index in range(3):
            reciter.apply_scored_review_attempt(
                word,
                exercise_type='recognition',
                correct=False,
                task_item=item,
                event_id=f'recover-wrong-{index}',
            )

        state = reciter.get_review_state(word)
        self.assertEqual(state['memory_status'], 'reinforcement')

        for index in range(4):
            reciter.apply_scored_review_attempt(
                word,
                exercise_type='recognition',
                correct=True,
                event_id=f'recover-correct-{index}',
            )

        self.assertEqual(reciter.get_review_state(word)['memory_status'], 'stable')

    def test_due_mastered_word_returns_for_maintenance(self):
        reciter = WordReciter(self.config)
        word = Word(
            'remember',
            '记住',
            success_count=reciter.config.MAX_SUCCESS_COUNT,
            next_review_date=reciter.today,
        )
        reciter.mastered_words = [word]
        task = reciter.get_today_learning_plan()
        self.assertEqual([w.english for w in task['words']], ['remember'])
        self.assertEqual(task['items'][0]['reason'], 'maintenance')

    def test_legacy_mastered_word_loaded_from_disk_enters_maintenance(self):
        word = Word(
            'legacy-remember',
            '旧版记住',
            success_count=self.config.MAX_SUCCESS_COUNT,
            next_review_date=china_today(),
        )
        Path(self.temp_data.name).write_text(
            json.dumps(
                {
                    'all_words': [],
                    'mastered_words': [word.to_dict()],
                },
                ensure_ascii=False,
            ),
            encoding='utf-8',
        )

        reciter = WordReciter(self.config)
        task = reciter.get_today_learning_plan()
        restored_word = task['words'][0]
        self.assertEqual(restored_word.english, 'legacy-remember')
        self.assertTrue(reciter.get_review_state(restored_word)['scheduler']['active'])
        self.assertEqual(task['items'][0]['reason'], 'maintenance')


def run_tests():
    """运行所有测试"""
    unittest.main(argv=[''], verbosity=2, exit=False)


if __name__ == '__main__':
    run_tests()

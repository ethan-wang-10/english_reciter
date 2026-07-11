#!/usr/bin/env python3
"""测试统计功能的脚本"""

from pathlib import Path

from reciter import Config, WordReciter, Word
from app_time import china_today


def test_statistics(tmp_path: Path):
    """测试统计功能"""
    print("🧪 测试统计功能...")
    
    # 创建一个临时的测试单词列表
    test_words = [
        Word("apple", "苹果"),
        Word("banana", "香蕉"),
        Word("orange", "橙子"),
        Word("grape", "葡萄"),
        Word("pear", "梨")
    ]
    
    # 设置不同的复习状态
    test_words[0].success_count = 3  # 已复习3次
    test_words[1].success_count = 1  # 已复习1次
    test_words[2].success_count = 0  # 新单词
    test_words[3].success_count = 5  # 已复习5次
    test_words[4].success_count = 2  # 已复习2次
    
    # 设置复习日期为今天
    today = china_today()
    for word in test_words:
        word.next_review_date = today
    
    # 所有可能写入的路径都放进 pytest 临时目录，避免污染项目根目录。
    config = Config(str(tmp_path / "missing-config.json"))
    config.DATA_FILE = str(tmp_path / "learning_data.json")
    config.EXAMPLE_DB = str(tmp_path / "word_examples.json")
    config.BACKUP_ENABLED = False
    reciter = WordReciter(config)
    reciter.all_words = test_words
    
    # 模拟复习过程
    print("\n📚 模拟复习过程...")
    
    # 模拟正确复习
    print("✅ 模拟正确复习...")
    for word in test_words[:3]:  # 前3个单词正确
        word.success_count += 1
        word.review_count += 1
        print(f"  正确复习: {word.english}")
    
    # 模拟错误复习
    print("❌ 模拟错误复习...")
    for word in test_words[3:]:  # 后2个单词错误
        word.review_count += 1
        print(f"  错误复习: {word.english}")
    
    # 模拟掌握单词
    print("🎉 模拟掌握单词...")
    mastered_word = test_words[0]
    mastered_word.success_count = 8  # 达到掌握条件
    reciter.mastered_words.append(mastered_word)
    reciter.all_words.remove(mastered_word)
    print(f"  已掌握: {mastered_word.english}")
    
    all_results = reciter.all_words + reciter.mastered_words
    assert len(all_results) == 5
    assert sum(word.review_count for word in all_results) == 5
    assert len(reciter.all_words) == 4
    assert reciter.mastered_words == [mastered_word]
    assert not (tmp_path / "word_examples.json").exists()
    
    print("\n✅ 统计功能测试完成！")

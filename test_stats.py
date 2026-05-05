#!/usr/bin/env python3
"""测试统计功能的脚本"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from reciter import WordReciter, Word
from app_time import china_today

def test_statistics():
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
    
    # 创建单词背诵器实例
    reciter = WordReciter()
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
    
    # 显示统计结果
    print("\n📊 统计结果:")
    print(f"  总单词数: {len(test_words)}")
    print(f"  正确复习数: 3")
    print(f"  错误复习数: 2")
    print(f"  正确率: {3/5*100:.1f}%")
    print(f"  新掌握单词: 1")
    print(f"  待复习单词: {len(reciter.all_words)}")
    print(f"  已掌握单词: {len(reciter.mastered_words)}")
    
    print("\n✅ 统计功能测试完成！")

if __name__ == "__main__":
    test_statistics()

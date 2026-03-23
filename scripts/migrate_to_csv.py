#!/usr/bin/env python3
"""
[历史] 将旧版 primary/junior/senior JSON 一次性迁移为 words.csv。
当前主词库仅以 static/wordbanks/words.csv 为准；日常更新请直接编辑 CSV 后 git 提交部署。
CSV 字段: english,chinese,level,phonetic,example1,example1_form,example1_cn,example2,example2_form,example2_cn
"""
import json
import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
WB_DIR = ROOT / "static" / "wordbanks"
OUT_CSV = WB_DIR / "words.csv"

LEVEL_MAP = {
    "primary": "小学",
    "junior": "初中",
    "senior": "高中",
}


def parse_example(example_str: str, word_english: str):
    """
    解析例句字符串（格式多样）：
    - "I like apples. 我喜欢苹果"   (空格分中英)
    - "I like apples._我喜欢苹果"   (下划线分中英)
    返回 (en_sentence, cn_sentence)
    """
    if not example_str:
        return "", ""
    if "_" in example_str:
        parts = example_str.split("_", 1)
        return parts[0].strip(), parts[1].strip()
    # 试着按中文字符的位置分割
    # 找第一个中文字符的位置
    match = re.search(r'[\u4e00-\u9fff]', example_str)
    if match:
        pos = match.start()
        return example_str[:pos].strip(), example_str[pos:].strip()
    return example_str.strip(), ""


def infer_form(word: str, sentence: str) -> str:
    """推断单词在句子中出现的变形形式（大小写不敏感）"""
    if not sentence or not word:
        return ""
    pattern = re.compile(r'\b(' + re.escape(word) + r'\w*)\b', re.IGNORECASE)
    m = pattern.search(sentence)
    if m:
        found = m.group(1)
        if found.lower() != word.lower():
            return found
    return ""


def migrate():
    rows = []
    seen = set()

    for phase_id, level_label in LEVEL_MAP.items():
        p = WB_DIR / f"{phase_id}.json"
        if not p.exists():
            print(f"警告: {p} 不存在，跳过")
            continue
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        words = data.get("words", [])
        print(f"处理 {phase_id} ({level_label}): {len(words)} 词")
        for w in words:
            en = str(w.get("english", "")).strip()
            zh = str(w.get("chinese", "")).strip()
            ex_raw = str(w.get("example", "")).strip()
            if not en:
                continue
            key = en.lower()
            if key in seen:
                continue
            seen.add(key)
            en_sent, cn_sent = parse_example(ex_raw, en)
            form1 = infer_form(en, en_sent)
            rows.append({
                "english": en,
                "chinese": zh,
                "level": level_label,
                "phonetic": "",
                "example1": en_sent,
                "example1_form": form1,
                "example1_cn": cn_sent,
                "example2": "",
                "example2_form": "",
                "example2_cn": "",
            })

    fieldnames = ["english", "chinese", "level", "phonetic",
                  "example1", "example1_form", "example1_cn",
                  "example2", "example2_form", "example2_cn"]
    with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n迁移完成: {len(rows)} 词 → {OUT_CSV}")


if __name__ == "__main__":
    migrate()

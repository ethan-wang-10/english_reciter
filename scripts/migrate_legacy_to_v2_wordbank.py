#!/usr/bin/env python3
"""
从老词库 words.csv 批量调用 DeepSeek 生成新词库 words_v2.json（默认跳过已存在键）。

用法（在项目根目录）：
  python scripts/migrate_legacy_to_v2_wordbank.py [--dry-run] [--limit N]

需配置环境变量 DEEPSEEK_API_KEY；与 simple_web_app 共用 DeepSeek 配置。

落盘：每成功一批 DeepSeek 后立即 ``append_words_v2_entries`` 写回磁盘；
中途 API 失败或进程退出时，先前批次已保存，不会整次丢失。
若 ``words_v2.json`` 无法解析或根节点不是数组，追加会报错并中止（避免误覆盖）。
"""

from __future__ import annotations

import argparse
import os
import sys

# 保证可导入项目根模块
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main() -> None:
    parser = argparse.ArgumentParser(description="老词库 → words_v2.json 批量生成")
    parser.add_argument("--dry-run", action="store_true", help="仅打印计划，不写文件、不调 API")
    parser.add_argument("--limit", type=int, default=0, help="最多处理条数（0 表示不限制）")
    args = parser.parse_args()

    os.chdir(ROOT)

    import simple_web_app as swa
    import wordbank_v2

    if not args.dry_run and not swa.get_deepseek_api_key():
        print("错误: 未配置 DEEPSEEK_API_KEY", file=sys.stderr)
        sys.exit(1)

    rows = swa.load_words_csv()
    keys_ordered: list[str] = []
    seen: set[str] = set()
    for row in rows:
        k = wordbank_v2.normalize_english_key(row.get("english", ""))
        if not k or k in seen:
            continue
        seen.add(k)
        keys_ordered.append(k)

    existing_v2 = wordbank_v2.get_v2_english_key_set()
    to_do = [k for k in keys_ordered if k not in existing_v2]
    if args.limit and args.limit > 0:
        to_do = to_do[: args.limit]

    print(f"老词库去重后: {len(keys_ordered)}，新库已有: {len(existing_v2)}，待生成: {len(to_do)}")
    if not to_do:
        print("无待处理词条。")
        return

    if args.dry_run:
        print("dry-run，前 20 个:", to_do[:20])
        return

    wordbank_so_far = set(swa.get_wordbank_english_set())
    total_written = 0
    failed_batches = 0
    batch_size = swa.DEEPSEEK_VOCAB_BATCH_WORDS

    for i in range(0, len(to_do), batch_size):
        batch = to_do[i : i + batch_size]
        batch_lower = {b.lower() for b in batch}
        entries = swa.deepseek_generate_word_entries_v2(batch, level="")
        if entries is None:
            failed_batches += 1
            print("批次失败（无返回）:", batch[:5], "...")
            continue
        rows_out, success = swa.accumulate_valid_deepseek_v2_entries(
            entries,
            level_hint="",
            v2_so_far=wordbank_so_far,
            batch_lower=batch_lower,
        )
        miss = [b for b in batch if b.lower() not in success]
        if miss:
            print("本批未覆盖:", miss[:10])
        if rows_out:
            n, skipped = wordbank_v2.append_words_v2_entries(rows_out)
            wordbank_v2.invalidate_words_v2_cache()
            wordbank_so_far = set(swa.get_wordbank_english_set())
            total_written += n
            print(
                f"本批已落盘：写入 {n} 条（本批有效 {len(rows_out)}，跳过重复键 {len(skipped)}）",
            )

    print(f"完成。累计写入约 {total_written} 条；失败批次数 {failed_batches}")


if __name__ == "__main__":
    main()

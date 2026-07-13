#!/usr/bin/env python3
"""Incrementally generate the private Gaokao question bank.

Examples:
  python scripts/generate_gaokao_questions.py --level 高中 --dry-run
  python scripts/generate_gaokao_questions.py --level 高中 --batch-size 5
  python scripts/generate_gaokao_questions.py --level 高中 --limit 100

The question bank is updated atomically after every batch. Re-running skips
words that already have both question types, so interrupted runs resume safely.
Failed words remain pending and are retried on the next run.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import gaokao_questions as questions  # noqa: E402
import simple_web_app as web  # noqa: E402


def _chat(messages: list[dict], max_tokens: int):
    return web._deepseek_chat(messages, max_tokens=max_tokens)


def _sources(level: str) -> list[dict]:
    rows, _ = web.merge_wordbank_rows_for_search(level)
    out = []
    seen = set()
    for row in rows:
        source = questions.source_from_wordbank_row(row)
        if not source or source["english"] in seen:
            continue
        seen.add(source["english"])
        out.append(source)
    out.sort(key=lambda row: row["english"])
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="可重入地生成英文识义与语境选词题库",
    )
    parser.add_argument("--level", default="高中", help="词库级别，默认：高中")
    parser.add_argument("--limit", type=int, default=0, help="本次最多处理多少个缺题单词，0 为不限")
    parser.add_argument("--batch-size", type=int, default=5, help="每次 AI 请求包含的单词数，默认：5")
    parser.add_argument("--pause", type=float, default=0.0, help="批次间暂停秒数")
    parser.add_argument("--force", action="store_true", help="重新生成已有题目")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不调用 AI、不写文件")
    args = parser.parse_args()

    batch_size = max(1, min(20, args.batch_size))
    all_sources = _sources(args.level.strip())
    pending = questions.missing_sources(all_sources, force=args.force)
    if args.limit > 0:
        pending = pending[: args.limit]

    bank = questions.load_bank()
    print(
        f"题库={questions.QUESTION_BANK_FILE} 级别={args.level or '全部'} "
        f"可用词={len(all_sources)} 已完成={len(bank.get('questions', {}))} "
        f"本次待生成={len(pending)}"
    )
    if args.dry_run:
        for source in pending[:50]:
            print(source["english"])
        if len(pending) > 50:
            print(f"... 另有 {len(pending) - 50} 个")
        return 0
    if not pending:
        return 0
    if not web.get_deepseek_api_key():
        print("未配置 DeepSeek API Key，无法生成题库", file=sys.stderr)
        return 2

    generated = 0
    failed = 0
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        try:
            result = questions.generate_and_persist(batch, _chat, force=args.force)
        except KeyboardInterrupt:
            print("\n已中断；此前成功批次已保存，下次执行会继续缺失项。", file=sys.stderr)
            return 130
        except Exception as exc:
            errors = {source["english"]: f"batch exception: {exc}" for source in batch}
            questions.persist_generation_result({}, errors)
            result = {
                "generated": 0,
                "failed": len(batch),
                "generated_words": [],
                "failed_words": sorted(errors),
            }
        generated += int(result.get("generated") or 0)
        failed += int(result.get("failed") or 0)
        done = min(start + len(batch), len(pending))
        print(
            f"[{done}/{len(pending)}] generated={result.get('generated', 0)} "
            f"failed={result.get('failed', 0)} "
            f"ok={','.join(result.get('generated_words') or []) or '-'} "
            f"fail={','.join(result.get('failed_words') or []) or '-'}",
            flush=True,
        )
        if args.pause > 0 and done < len(pending):
            time.sleep(args.pause)

    print(f"完成：成功 {generated}，失败 {failed}；失败项下次运行会自动重试。")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

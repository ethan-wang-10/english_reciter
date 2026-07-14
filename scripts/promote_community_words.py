#!/usr/bin/env python3
"""可重入地补全社区候选词条，并提升到正式词库 words_v2.json。

社区词库始终保留原始贡献和提升状态。只有 AI 返回通过 V2 校验、且确认已原子追加
到 words_v2.json 后，候选词条才会标记为 promoted。中断或失败后可直接重跑。

示例：
  python3 scripts/promote_community_words.py --dry-run
  python3 scripts/promote_community_words.py --batch-size 5 --pause 2
  python3 scripts/promote_community_words.py --limit 100
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _short_error(message: object) -> str:
    text = " ".join(str(message or "").split())
    return text[:500] or "未知错误"


def _promotion_update(
    *,
    status: str,
    attempted_at: str,
    word_key: str = "",
    error: str = "",
    attempted: bool = True,
) -> dict:
    if status == "promoted":
        update = {
            "status": "promoted",
            "attempt_increment": 1 if attempted else 0,
            "last_error": None,
            "promoted_at": attempted_at,
            "promoted_word_key": word_key,
        }
        if attempted:
            update["last_attempt_at"] = attempted_at
        return update
    return {
        "status": "failed",
        "attempt_increment": 1 if attempted else 0,
        "last_attempt_at": attempted_at,
        "last_error": _short_error(error),
    }


def promote_community_words(
    web,
    wordbank_v2,
    *,
    limit: int = 0,
    batch_size: int = 5,
    pause: float = 0.0,
    force: bool = False,
    dry_run: bool = False,
    print_fn: Callable[..., None] = print,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict:
    """执行一次提升；依赖以参数传入，便于用临时文件做隔离测试。"""
    community_file = Path(web.COMMUNITY_WB_FILE)
    print_fn(f"社区文件={community_file}")
    print_fn(f"正式词库={wordbank_v2.WORDS_V2_FILE}")
    if not community_file.exists():
        print_fn("社区文件不存在：当前没有可提升的社区候选词条。")
        return {
            "selected": 0,
            "promoted_existing": 0,
            "promoted_generated": 0,
            "failed": 0,
            "source_missing": True,
            "exit_code": 0,
        }

    snapshot = web.read_community_wordbank_snapshot()
    candidates: list[tuple[str, dict]] = []
    seen: set[str] = set()
    for raw in snapshot.get("words") or []:
        if not isinstance(raw, dict):
            continue
        key = wordbank_v2.normalize_english_key(raw.get("english", ""))
        status = str(raw.get("status") or "pending").strip().lower()
        if not key or key in seen:
            continue
        if not force and status not in {"pending", "failed"}:
            continue
        seen.add(key)
        candidates.append((key, raw))

    if limit > 0:
        candidates = candidates[:limit]
    print_fn(f"社区词条={len(snapshot.get('words') or [])} 本次候选={len(candidates)}")
    if dry_run:
        for key, _ in candidates[:50]:
            print_fn(key)
        if len(candidates) > 50:
            print_fn(f"... 另有 {len(candidates) - 50} 个")
        return {
            "selected": len(candidates),
            "promoted_existing": 0,
            "promoted_generated": 0,
            "failed": 0,
            "exit_code": 0,
        }
    if not candidates:
        return {
            "selected": 0,
            "promoted_existing": 0,
            "promoted_generated": 0,
            "failed": 0,
            "exit_code": 0,
        }

    existing_v2 = wordbank_v2.get_v2_english_key_set()
    already = [key for key, _ in candidates if key in existing_v2]
    if already:
        now = web.china_now_iso(timespec="seconds")
        web.merge_community_promotion_updates(
            {
                key: _promotion_update(
                    status="promoted",
                    attempted_at=now,
                    word_key=key,
                    attempted=False,
                )
                for key in already
            }
        )
        print_fn(f"已有正式词条，已同步提升状态：{len(already)}")

    to_generate = [key for key, _ in candidates if key not in existing_v2]
    if not to_generate:
        return {
            "selected": len(candidates),
            "promoted_existing": len(already),
            "promoted_generated": 0,
            "failed": 0,
            "exit_code": 0,
        }
    if not web.get_deepseek_api_key():
        print_fn("未配置 DeepSeek API Key，尚未提升的词条保持待重试状态", file=sys.stderr)
        return {
            "selected": len(candidates),
            "promoted_existing": len(already),
            "promoted_generated": 0,
            "failed": len(to_generate),
            "exit_code": 2,
        }

    max_batch = max(1, int(getattr(web, "DEEPSEEK_VOCAB_BATCH_WORDS", 5)))
    batch_size = max(1, min(int(batch_size), max_batch))
    promoted_generated = 0
    failed = 0

    for start in range(0, len(to_generate), batch_size):
        batch = to_generate[start : start + batch_size]
        requested = set(batch)
        attempted_at = web.china_now_iso(timespec="seconds")
        batch_errors = {key: "AI 未返回该词的有效完整词条" for key in batch}
        promoted_keys: set[str] = set()
        append_error = ""

        try:
            raw_entries = web.deepseek_generate_word_entries_v2(batch, level="")
        except KeyboardInterrupt:
            print_fn("\n已中断；此前成功批次已保存，下次执行会继续。", file=sys.stderr)
            raise
        except Exception as exc:
            raw_entries = None
            append_error = f"AI 调用异常: {_short_error(exc)}"

        if raw_entries is not None:
            # 社区提升只允许写入本批请求键，拒绝模型额外返回的词，避免污染正式词库。
            requested_entries = [
                entry
                for entry in raw_entries
                if isinstance(entry, dict)
                and wordbank_v2.normalize_english_key(entry.get("english", "")) in requested
            ]
            validation_seen = set(existing_v2)
            rows_out, validated = web.accumulate_valid_deepseek_v2_entries(
                requested_entries,
                level_hint="",
                v2_so_far=validation_seen,
                batch_lower=requested,
            )
            if rows_out:
                try:
                    wordbank_v2.append_words_v2_entries(rows_out)
                    wordbank_v2.invalidate_words_v2_cache()
                    existing_v2 = wordbank_v2.get_v2_english_key_set()
                    promoted_keys = {key for key in validated if key in existing_v2}
                except Exception as exc:
                    append_error = f"写入 words_v2.json 失败: {_short_error(exc)}"
            for key in promoted_keys:
                batch_errors.pop(key, None)

        if append_error:
            batch_errors = {key: append_error for key in batch if key not in promoted_keys}

        updates = {
            key: _promotion_update(
                status="promoted",
                attempted_at=attempted_at,
                word_key=key,
            )
            for key in promoted_keys
        }
        updates.update(
            {
                key: _promotion_update(
                    status="failed",
                    attempted_at=attempted_at,
                    error=error,
                )
                for key, error in batch_errors.items()
            }
        )
        web.merge_community_promotion_updates(updates)

        promoted_generated += len(promoted_keys)
        failed += len(batch_errors)
        done = min(start + len(batch), len(to_generate))
        print_fn(
            f"[{done}/{len(to_generate)}] promoted={len(promoted_keys)} "
            f"failed={len(batch_errors)} "
            f"ok={','.join(sorted(promoted_keys)) or '-'} "
            f"fail={','.join(sorted(batch_errors)) or '-'}",
            flush=True,
        )
        if pause > 0 and done < len(to_generate):
            sleep_fn(pause)

    print_fn(
        f"完成：已有正式词条 {len(already)}，本次生成提升 {promoted_generated}，失败 {failed}。"
    )
    return {
        "selected": len(candidates),
        "promoted_existing": len(already),
        "promoted_generated": promoted_generated,
        "failed": failed,
        "exit_code": 0 if failed == 0 else 2,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="补全并校验社区候选词条，成功后提升到 words_v2.json",
    )
    parser.add_argument("--dry-run", action="store_true", help="只列出候选，不调用 AI、不修改数据")
    parser.add_argument("--limit", type=int, default=0, help="本次最多处理多少条，0 为不限")
    parser.add_argument("--batch-size", type=int, default=5, help="每次 AI 请求词数，默认 5")
    parser.add_argument("--pause", type=float, default=0.0, help="批次之间暂停秒数")
    parser.add_argument(
        "--data-dir",
        default="",
        help="Web 服务使用的用户数据目录；默认读取 ENGLISH_RECITER_DATA_DIR 或 ./user_data_simple",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="也检查已标记 promoted 的社区词条；不会覆盖 words_v2.json 同键",
    )
    args = parser.parse_args()

    if args.data_dir:
        os.environ["ENGLISH_RECITER_DATA_DIR"] = str(Path(args.data_dir).expanduser().resolve())

    import simple_web_app as web
    import wordbank_v2

    try:
        result = promote_community_words(
            web,
            wordbank_v2,
            limit=max(0, args.limit),
            batch_size=max(1, args.batch_size),
            pause=max(0.0, args.pause),
            force=args.force,
            dry_run=args.dry_run,
        )
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"提升中止：{_short_error(exc)}", file=sys.stderr)
        return 1
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Generate prompt-checked questions, with optional legacy candidate audit.

Examples:
  python scripts/generate_gaokao_questions.py --stage generate --level 高中 --batch-size 10
  python scripts/generate_gaokao_questions.py --stage generate --level '' --refresh-prompt-version
  python scripts/generate_gaokao_questions.py --stage audit --audit-batch-size 10

Generation uses one AI call per batch, applies deterministic validation, and
publishes records tagged with the current prompt quality version. The audit
stage remains available for candidates created by older deployments.
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


def _audit_chat(messages: list[dict], max_tokens: int):
    return web._deepseek_chat(messages, max_tokens=max_tokens, temperature=0.0)


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


def _run_generation(
    pending: list[dict],
    *,
    batch_size: int,
    pause: float,
    force: bool,
    refresh_prompt: bool,
) -> tuple[int, int]:
    generated = 0
    failed = 0
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        try:
            result = questions.generate_prompt_checked_and_persist(
                batch,
                _chat,
                force=force,
                refresh_prompt=refresh_prompt,
            )
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            errors = {source["english"]: f"batch exception: {exc}" for source in batch}
            questions.persist_prompt_checked_result({}, errors)
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
            f"[generate {done}/{len(pending)}] "
            f"published={result.get('generated', 0)} failed={result.get('failed', 0)} "
            f"ok={','.join(result.get('generated_words') or []) or '-'} "
            f"fail={','.join(result.get('failed_words') or []) or '-'}",
            flush=True,
        )
        if pause > 0 and done < len(pending):
            time.sleep(pause)
    return generated, failed


def _run_audit(
    pending: dict[str, dict],
    *,
    batch_size: int,
    pause: float,
) -> tuple[int, int, int]:
    approved = 0
    rejected = 0
    retry = 0
    items = list(pending.items())
    for start in range(0, len(items), batch_size):
        batch = dict(items[start : start + batch_size])
        try:
            accepted_rows, rejected_rows, retry_rows = questions.audit_question_records(
                batch,
                _audit_chat,
            )
            questions.persist_audit_result(accepted_rows, rejected_rows, retry_rows)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            accepted_rows = {}
            rejected_rows = {}
            retry_rows = {key: f"audit batch exception: {exc}" for key in batch}
            questions.persist_audit_result(accepted_rows, rejected_rows, retry_rows)
        approved += len(accepted_rows)
        rejected += len(rejected_rows)
        retry += len(retry_rows)
        done = min(start + len(batch), len(items))
        print(
            f"[audit {done}/{len(items)}] approved={len(accepted_rows)} "
            f"rejected={len(rejected_rows)} retry={len(retry_rows)} "
            f"ok={','.join(sorted(accepted_rows)) or '-'} "
            f"reject={','.join(sorted(rejected_rows)) or '-'}",
            flush=True,
        )
        if pause > 0 and done < len(items):
            time.sleep(pause)
    return approved, rejected, retry


def main() -> int:
    parser = argparse.ArgumentParser(
        description="单次 AI 批量生成并发布英文识义与语境选词题库",
    )
    parser.add_argument(
        "--stage",
        choices=("generate", "audit", "all"),
        default="generate",
        help="generate 单次生成并直接发布；audit 审查旧候选；all 先生成再处理旧候选",
    )
    parser.add_argument("--level", default="高中", help="词库级别，默认：高中")
    parser.add_argument("--limit", type=int, default=0, help="本阶段最多处理多少题，0 为不限")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="每次生成请求包含的单词数，默认：10，最大：10",
    )
    parser.add_argument(
        "--audit-batch-size",
        type=int,
        default=8,
        help="每次集中审查的候选题数，默认：8，最大：10",
    )
    parser.add_argument("--pause", type=float, default=0.0, help="批次间暂停秒数")
    parser.add_argument("--force", action="store_true", help="重新生成已有正式题或候选题")
    parser.add_argument(
        "--refresh-prompt-version",
        action="store_true",
        help="仅重建尚未使用当前 Prompt 版本发布的题，可配合 limit 断点续跑",
    )
    parser.add_argument("--dry-run", action="store_true", help="只统计，不调用 AI、不写文件")
    args = parser.parse_args()

    batch_size = max(1, min(10, args.batch_size))
    audit_batch_size = max(1, min(10, args.audit_batch_size))
    all_sources = _sources(args.level.strip())
    pending_generation = (
        questions.sources_needing_prompt_refresh(all_sources)
        if args.refresh_prompt_version
        else questions.missing_sources(all_sources, force=args.force)
    )
    pending_audit = questions.pending_candidate_records()
    if args.limit > 0 and args.stage in {"generate", "all"}:
        pending_generation = pending_generation[: args.limit]
    if args.limit > 0 and args.stage == "audit":
        pending_audit = dict(list(pending_audit.items())[: args.limit])
    if args.stage == "generate":
        pending_audit = {}
    elif args.stage == "audit":
        pending_generation = []

    bank = questions.load_bank()
    print(
        f"题库={questions.QUESTION_BANK_FILE} 级别={args.level or '全部'} "
        f"可用词={len(all_sources)} 已发布={questions.approved_question_count(bank)} "
        f"Prompt版本={questions.GENERATION_PROMPT_VERSION} "
        f"候选={len(bank.get('candidates', {}))} "
        f"本次待生成={len(pending_generation)} 本次待审查={len(pending_audit)}"
    )
    if args.dry_run:
        if args.stage in {"generate", "all"}:
            for source in pending_generation[:50]:
                print(f"generate {source['english']}")
        if args.stage in {"audit", "all"}:
            for key in list(pending_audit)[:50]:
                print(f"audit {key}")
        return 0

    generation_work = args.stage in {"generate", "all"} and bool(pending_generation)
    audit_work = args.stage in {"audit", "all"} and bool(pending_audit)
    if not generation_work and not audit_work:
        return 0
    if not web.get_deepseek_api_key():
        print("未配置 DeepSeek API Key，无法生成或审查题库", file=sys.stderr)
        return 2

    generated = 0
    generation_failed = 0
    approved = 0
    rejected = 0
    audit_retry = 0
    try:
        if args.stage in {"generate", "all"}:
            generated, generation_failed = _run_generation(
                pending_generation,
                batch_size=batch_size,
                pause=args.pause,
                force=args.force,
                refresh_prompt=args.refresh_prompt_version,
            )
        if args.stage == "all":
            pending_audit = questions.pending_candidate_records(limit=max(0, args.limit))
        if args.stage in {"audit", "all"}:
            approved, rejected, audit_retry = _run_audit(
                pending_audit,
                batch_size=audit_batch_size,
                pause=args.pause,
            )
    except KeyboardInterrupt:
        print("\n已中断；候选和已发布题目均已原子保存，下次可继续。", file=sys.stderr)
        return 130

    print(
        f"完成：生成并发布 {generated}，生成失败 {generation_failed}，"
        f"旧候选审查通过 {approved}，语义拒绝 {rejected}，待重试 {audit_retry}。"
    )
    return 0 if generation_failed == 0 and rejected == 0 and audit_retry == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

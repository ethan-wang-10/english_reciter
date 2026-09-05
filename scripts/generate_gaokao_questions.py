#!/usr/bin/env python3
"""Generate and independently audit Gaokao vocabulary questions.

Examples:
  python scripts/generate_gaokao_questions.py --stage generate --level 高中 --batch-size 10
  python scripts/generate_gaokao_questions.py --stage generate --level '' --refresh-prompt-version
  python scripts/generate_gaokao_questions.py --stage audit --audit-batch-size 10

Generation uses one request for each candidate batch of up to ten words, then
separate recognition/context blind audits and a feedback audit before publishing. Import-created candidates can be
audited later in off-peak batches with the audit stage.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import gaokao_questions as questions  # noqa: E402
import simple_web_app as web  # noqa: E402


def _sources(level: str) -> list[dict]:
    return web.gaokao_question_sources(level)


def _run_generation(
    pending: list[dict],
    *,
    batch_size: int,
    pause: float,
    force: bool,
    refresh_prompt: bool,
    debug_generation: bool,
) -> tuple[int, int]:
    def report(done: int, total: int, result: dict) -> None:
        print(
            f"[generate {done}/{total}] "
            f"published={result.get('generated', 0)} failed={result.get('failed', 0)} "
            f"ok={','.join(result.get('generated_words') or []) or '-'} "
            f"fail={','.join(result.get('failed_words') or []) or '-'}",
            flush=True,
        )
        failure_errors = result.get("failure_errors") or {}
        if failure_errors:
            reason_counts: dict[str, int] = {}
            for reason in failure_errors.values():
                text = str(reason or "generation failed")
                reason_counts[text] = reason_counts.get(text, 0) + 1
            summary = " | ".join(
                f"{count}x {reason}"
                for reason, count in sorted(reason_counts.items())
            )
            print(f"[generate errors] {summary}", flush=True)

    def diagnostic(event: dict) -> None:
        print("[generate diagnostic]", flush=True)
        print(
            json.dumps(event, ensure_ascii=False, indent=2, sort_keys=True),
            flush=True,
        )

    result = web.generate_gaokao_question_batches(
        pending,
        batch_size=batch_size,
        pause=pause,
        force=force,
        refresh_prompt=refresh_prompt,
        progress=report,
        diagnostic=diagnostic if debug_generation else None,
    )
    return int(result["generated"]), int(result["failed"])


def _run_audit(
    pending: dict[str, dict],
    *,
    batch_size: int,
    pause: float,
) -> tuple[int, int, int]:
    def report(done: int, total: int, result: dict) -> None:
        print(
            f"[audit {done}/{total}] approved={result.get('approved', 0)} "
            f"rejected={result.get('rejected', 0)} retry={result.get('retry', 0)} "
            f"ok={','.join(result.get('approved_words') or []) or '-'} "
            f"reject={','.join(result.get('rejected_words') or []) or '-'}",
            flush=True,
        )

    result = web.audit_gaokao_candidate_pool_batches(
        pending,
        batch_size=batch_size,
        pause=pause,
        progress=report,
    )
    return int(result["approved"]), int(result["rejected"]), int(result["retry"])


def main() -> int:
    parser = argparse.ArgumentParser(
        description="批量生成、盲审并校对英文识义与语境选词题库",
    )
    parser.add_argument(
        "--stage",
        choices=("generate", "audit", "all"),
        default="generate",
        help="generate 生成、独立审计并发布；audit 审查异步候选；all 依次执行两者",
    )
    parser.add_argument("--level", default="高中", help="词库级别，默认：高中")
    parser.add_argument(
        "--limit",
        type=int,
        default=30,
        help="本阶段最多处理多少题，默认 30；显式传 0 为不限",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=questions.GENERATION_REQUEST_WORDS,
        help="兼容旧命令；生成请求固定为每批 10 个",
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
        help="仅重建尚未通过当前生成与独立审计版本的题，可配合 limit 断点续跑",
    )
    parser.add_argument("--dry-run", action="store_true", help="只统计，不调用 AI、不写文件")
    parser.add_argument(
        "--debug-generation",
        action="store_true",
        help="输出完整生成 Prompt、AI 原始响应和逐词校验诊断；内容较多，仅用于手工排查",
    )
    args = parser.parse_args()

    if args.batch_size != questions.GENERATION_REQUEST_WORDS:
        print(
            f"忽略 --batch-size {args.batch_size}；高考题生成固定每批 "
            f"{questions.GENERATION_REQUEST_WORDS} 个。",
            file=sys.stderr,
        )
    batch_size = questions.GENERATION_REQUEST_WORDS
    audit_batch_size = max(1, min(10, args.audit_batch_size))
    all_sources = _sources(args.level.strip())
    pending_generation = (
        questions.sources_needing_prompt_refresh(all_sources)
        if args.refresh_prompt_version
        else all_sources if args.force else questions.sources_needing_prompt_refresh(all_sources)
    )
    pending_audit = questions.pending_candidate_pools()
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
        f"审计版本={questions.AUDIT_VERSION} "
        f"待审候选={len(pending_audit)} "
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

    with web.gaokao_backfill.generation_job_lock(blocking=False) as acquired:
        if not acquired:
            print("已有后台或手工补题任务正在运行，请稍后重试。", file=sys.stderr)
            return 3

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
                    debug_generation=args.debug_generation,
                )
            if args.stage == "all":
                pending_audit = questions.pending_candidate_pools(limit=max(0, args.limit))
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
        f"完成：生成并审计发布 {generated}，生成或审计失败 {generation_failed}，"
        f"异步候选审查通过 {approved}，语义拒绝 {rejected}，待重试 {audit_retry}。"
    )
    return 0 if generation_failed == 0 and rejected == 0 and audit_retry == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

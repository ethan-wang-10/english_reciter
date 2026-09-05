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
from contextlib import contextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import gaokao_questions as questions  # noqa: E402
import simple_web_app as web  # noqa: E402


def _sources(level: str) -> list[dict]:
    return web.gaokao_question_sources(level)


class _Diagnostics:
    def __init__(self, debug: bool = False):
        self.debug = debug
        self.generation_requests = 0
        self.audit_requests = 0
        self.cached_items = 0

    def __call__(self, event: dict) -> None:
        if event.get("event") == "request":
            self.generation_requests += 1
        elif event.get("event") == "audit_request":
            self.audit_requests += 1
        elif event.get("event") == "audit_cache_hit":
            self.cached_items += int(event.get("item_count") or 0)
        if self.debug:
            print(json.dumps(event, ensure_ascii=False, indent=2, sort_keys=True), flush=True)

    def report(self) -> None:
        print(
            f"[requests] generation={self.generation_requests} audit={self.audit_requests} "
            f"cached_item_stages={self.cached_items}", flush=True,
        )


def _pending_audits(sources: list[dict], limit: int = 0) -> dict[str, dict]:
    pending = {}
    for start in range(0, len(sources), 200):
        batch = {source["english"]: source for source in sources[start:start + 200]}
        keys = list(batch)
        for key, pool in questions.pending_candidate_pools(word_keys=keys).items():
            if pool["source"].get("source_hash") != batch[key].get("source_hash"):
                continue
            pending[key] = pool
            if limit > 0 and len(pending) >= limit:
                return pending
    return pending


def _refresh_actions(sources: list[dict], *, force: bool, refresh: bool) -> dict[str, str]:
    actions = {}
    for start in range(0, len(sources), 200):
        batch = sources[start:start + 200]
        pending = {} if force else questions.pending_candidate_pools(
            word_keys=[source["english"] for source in batch],
        )
        pending = {
            source["english"]: pending[source["english"]] for source in batch
            if source["english"] in pending
            and pending[source["english"]]["source"].get("source_hash") == source.get("source_hash")
        }
        reusable = questions.reusable_refresh_pools([
            source for source in batch if source["english"] not in pending
        ]) if refresh and not force else {}
        for source in batch:
            key = source["english"]
            actions[key] = "resume" if key in pending else "reaudit" if key in reusable else "generate"
    return actions


class _PeakHoursPause(KeyboardInterrupt):
    pass


@contextmanager
def _off_peak_requests(enabled: bool):
    originals = (web._gaokao_generation_chat, web._gaokao_audit_chat)

    def guarded(chat):
        def call(messages, max_tokens):
            if enabled and not web.gaokao_backfill.is_deepseek_off_peak():
                raise _PeakHoursPause()
            return chat(messages, max_tokens)
        return call

    if enabled:
        web._gaokao_generation_chat, web._gaokao_audit_chat = map(guarded, originals)
    try:
        yield
    finally:
        web._gaokao_generation_chat, web._gaokao_audit_chat = originals


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
            f"reused={result.get('reused_questions', 0)} resumed={result.get('resumed_candidates', 0)} "
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

    diagnostic = _Diagnostics(debug_generation)
    result = web.generate_gaokao_question_batches(
        pending,
        batch_size=batch_size,
        pause=pause,
        force=force,
        refresh_prompt=refresh_prompt,
        progress=report,
        diagnostic=diagnostic,
    )
    diagnostic.report()
    return int(result["generated"]), int(result["failed"])


def _run_audit(
    pending: dict[str, dict],
    *,
    batch_size: int,
    pause: float,
    debug_generation: bool = False,
) -> tuple[int, int, int]:
    def report(done: int, total: int, result: dict) -> None:
        print(
            f"[audit {done}/{total}] approved={result.get('approved', 0)} "
            f"rejected={result.get('rejected', 0)} retry={result.get('retry', 0)} "
            f"ok={','.join(result.get('approved_words') or []) or '-'} "
            f"reject={','.join(result.get('rejected_words') or []) or '-'}",
            flush=True,
        )

    diagnostic = _Diagnostics(debug_generation)
    result = web.audit_gaokao_candidate_pool_batches(
        pending,
        batch_size=batch_size,
        pause=pause,
        progress=report,
        diagnostic=diagnostic,
    )
    diagnostic.report()
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
        help="本次最多处理多少个词（all 阶段共用限额），默认 30；显式传 0 为不限",
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
        default=10,
        help="每次集中审查的候选题数，默认及最大：10",
    )
    parser.add_argument("--pause", type=float, default=0.0, help="批次间暂停秒数")
    parser.add_argument("--force", action="store_true", help="重新生成已有正式题或候选题")
    parser.add_argument(
        "--refresh-prompt-version",
        action="store_true",
        help="仅刷新旧版本题，优先复用旧内容重审，不合格再生成；配合 force 可强制重生成",
    )
    parser.add_argument("--off-peak-only", action="store_true", help="只在低峰发起模型请求；进入高峰时保留进度退出")
    parser.add_argument("--dry-run", action="store_true", help="只规划，不调用 AI、不改写题目")
    parser.add_argument(
        "--debug-generation",
        action="store_true",
        help="输出完整生成 Prompt、AI 原始响应和逐词校验诊断；内容较多，仅用于手工排查",
    )
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("--limit 不能小于 0")

    if args.batch_size != questions.GENERATION_REQUEST_WORDS:
        print(
            f"忽略 --batch-size {args.batch_size}；高考题生成固定每批 "
            f"{questions.GENERATION_REQUEST_WORDS} 个。",
            file=sys.stderr,
        )
    batch_size = questions.GENERATION_REQUEST_WORDS
    audit_batch_size = max(1, min(10, args.audit_batch_size))
    all_sources = _sources(args.level.strip())
    pending_generation = []
    if args.stage in {"generate", "all"}:
        pending_generation = (
            all_sources[:args.limit or None] if args.force and not args.refresh_prompt_version
            else questions.sources_needing_prompt_refresh(all_sources, limit=args.limit)
        )
    selected_keys = {source["english"] for source in pending_generation}
    remaining_limit = max(0, args.limit - len(selected_keys))
    pending_audit = {}
    if args.stage in {"audit", "all"} and (args.limit == 0 or remaining_limit > 0):
        pending_audit = _pending_audits(
            [source for source in all_sources if source["english"] not in selected_keys],
            limit=remaining_limit,
        )
    actions = _refresh_actions(pending_generation, force=args.force, refresh=args.refresh_prompt_version)
    print(
        f"题库={questions.QUESTION_BANK_FILE} 级别={args.level or '全部'} "
        f"可用词={len(all_sources)} "
        f"Prompt版本={questions.GENERATION_PROMPT_VERSION} "
        f"审计版本={questions.AUDIT_VERSION} "
        f"待审候选={len(pending_audit)} "
        f"本次待处理={len(pending_generation)} 额外待审查={len(pending_audit)}"
    )
    print(
        f"刷新计划：复用旧题={sum(action == 'reaudit' for action in actions.values())} "
        f"续审候选={sum(action == 'resume' for action in actions.values())} "
        f"需要生成={sum(action == 'generate' for action in actions.values())}"
    )
    if args.dry_run:
        if args.stage in {"generate", "all"}:
            for source in pending_generation[:50]:
                print(f"{actions[source['english']]} {source['english']}")
        if args.stage in {"audit", "all"}:
            for key in list(pending_audit)[:50]:
                print(f"audit {key}")
        return 0

    generation_work = args.stage in {"generate", "all"} and bool(pending_generation)
    audit_work = args.stage in {"audit", "all"} and bool(pending_audit)
    if not generation_work and not audit_work:
        return 0
    if args.off_peak_only and not web.gaokao_backfill.is_deepseek_off_peak():
        print("当前为 DeepSeek 高峰时段，未发起模型请求。", file=sys.stderr)
        return 4
    if not web.get_deepseek_api_key():
        print("未配置 DeepSeek API Key，无法生成或审查题库", file=sys.stderr)
        return 2

    with web.gaokao_backfill.generation_job_lock(blocking=False) as acquired, _off_peak_requests(args.off_peak_only):
        if not acquired:
            print("已有后台或手工补题任务正在运行，请稍后重试。", file=sys.stderr)
            return 3

        generated = 0
        generation_failed = 0
        approved = 0
        rejected = 0
        audit_retry = 0
        try:
            if args.stage in {"generate", "all"} and pending_generation:
                generated, generation_failed = _run_generation(
                    pending_generation,
                    batch_size=batch_size,
                    pause=args.pause,
                    force=args.force,
                    refresh_prompt=args.refresh_prompt_version,
                    debug_generation=args.debug_generation,
                )
            if args.stage in {"audit", "all"} and pending_audit:
                approved, rejected, audit_retry = _run_audit(
                    pending_audit,
                    batch_size=audit_batch_size,
                    pause=args.pause,
                    debug_generation=args.debug_generation,
                )
        except _PeakHoursPause:
            print("\n已进入高峰时段，候选与审计进度已保留；低峰时重跑同一命令即可。", file=sys.stderr)
            return 4
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

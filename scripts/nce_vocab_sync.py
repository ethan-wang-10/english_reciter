#!/usr/bin/env python3
"""
从 NCE 课文 JSON 提取词形（spaCy lemma），与 words.csv 比对；未命中则按批调用 DeepSeek
生成词条并追加到 static/wordbanks/words.csv。

复用 simple_web_app 中的 spaCy、DeepSeek、CSV 与疑难词逻辑（方案 A）。

用法（请在项目根目录执行，或任意目录执行本脚本——会自动 chdir 到仓库根）：
  python scripts/nce_vocab_sync.py --lesson static/wordbanks/nce/NCE1/0001_001\\&002.Excuse\\ Me.json --dry-run
  python scripts/nce_vocab_sync.py --nce-dir static/wordbanks/nce/NCE1 --dry-run
  python scripts/nce_vocab_sync.py --nce-dir static/wordbanks/nce/NCE1 --level 初中

疑难词重试（user_data_simple/_shared/wordbank_troubles.json 中 difficult）：
  python scripts/nce_vocab_sync.py --retry-difficult --dry-run
  python scripts/nce_vocab_sync.py --retry-difficult --level 初中

依赖：课文模式需 spaCy + en_core_web_sm；DeepSeek 需环境变量 DEEPSEEK_API_KEY，或 config.json 中 deepseek_api_key（密文时需与 Web 相同 SECRET_KEY / DEEPSEEK_KEY_ENCRYPTION_SECRET 以解密）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set

ROOT = Path(__file__).resolve().parent.parent


def _ensure_repo_root() -> None:
    if os.getcwd() != str(ROOT):
        os.chdir(ROOT)
    root_str = str(ROOT)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)


def _load_nce_english_blob(path: Path) -> str:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    lines = data.get("lines") or []
    parts: List[str] = []
    for line in lines:
        en = (line.get("english") or "").strip()
        if en:
            parts.append(en)
    return " ".join(parts)


def _collect_json_paths(lessons: List[str], nce_dir: Optional[str]) -> List[Path]:
    paths: List[Path] = []
    for raw in lessons:
        p = Path(raw)
        if not p.is_file():
            raise SystemExit(f"文件不存在: {p}")
        paths.append(p.resolve())
    if nce_dir:
        root = Path(nce_dir)
        if not root.is_dir():
            raise SystemExit(f"目录不存在: {root}")
        for p in sorted(root.rglob("*.json")):
            if p.name == "manifest.json":
                continue
            paths.append(p.resolve())
    # 去重保序
    seen: Set[Path] = set()
    out: List[Path] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _build_full_text(paths: List[Path]) -> str:
    chunks: List[str] = []
    for p in paths:
        blob = _load_nce_english_blob(p)
        if blob:
            chunks.append(blob)
    return " ".join(chunks)


def _deepseek_generate_append(
    swa,
    lemmas_ordered: List[str],
    level_hint: str,
    dry_run: bool,
    *,
    remove_difficult_on_success: bool,
) -> None:
    """
    按批调用 DeepSeek，写入 CSV；失败则记入疑难词。
    remove_difficult_on_success=True 时，对成功写入的词条调用 delete_wordbank_difficult。
    """
    if not lemmas_ordered:
        print("无需写入。")
        return

    if dry_run:
        print("\n[dry-run] 将调用 DeepSeek 的词（按批最多 30 个）:")
        for i, w in enumerate(lemmas_ordered):
            print(f"  {i + 1}. {w}")
        return

    if not swa.get_deepseek_api_key():
        raise SystemExit("未配置 DeepSeek API（环境变量 DEEPSEEK_API_KEY 或 config.json 的 deepseek_api_key）")

    generated: List[dict] = []
    failed_surfaces: List[str] = []

    csv_so_far: Set[str] = set(swa.get_csv_english_set())
    batch_size = swa.DEEPSEEK_VOCAB_BATCH_WORDS
    for i in range(0, len(lemmas_ordered), batch_size):
        batch = lemmas_ordered[i : i + batch_size]
        entries = swa.deepseek_generate_word_entries(batch, level=level_hint)
        batch_lower = {b.lower() for b in batch}
        if entries is not None:
            rows, success = swa.accumulate_valid_deepseek_word_rows(
                entries,
                level_hint=level_hint,
                csv_so_far=csv_so_far,
                batch_lower=batch_lower,
            )
            generated.extend(rows)
            miss = [b for b in batch if b.lower() not in success]
        else:
            miss = list(batch)
        failed_surfaces.extend(miss)

    failed_surfaces = swa._dedupe_preserve_order(failed_surfaces)
    if failed_surfaces:
        swa.record_surfaces_to_difficult(failed_surfaces)

    if generated:
        n = swa.append_words_to_csv(generated)
        swa.invalidate_words_csv_cache()
        print(f"\n已追加 {n} 条到 {swa.WORDS_CSV_FILE}")
        if remove_difficult_on_success:
            cleared = 0
            for row in generated:
                en = str(row.get("english", "") or "").strip().lower()
                if en and swa.delete_wordbank_difficult(en):
                    cleared += 1
            print(f"已从疑难词中移除（生成成功）: {cleared} 个")
    else:
        print("\n未生成任何有效词条（DeepSeek 返回空或解析失败）")

    if failed_surfaces:
        print(f"生成失败并已记入疑难词: {len(failed_surfaces)} 个")
        for s in failed_surfaces[:20]:
            print(f"  - {s}")
        if len(failed_surfaces) > 20:
            print(f"  ... 另有 {len(failed_surfaces) - 20} 个")


def _run_retry_difficult(swa, args: argparse.Namespace) -> None:
    """对 wordbank_troubles.json 中的 difficult 再次调用 DeepSeek。"""
    with swa._TROUBLES_LOCK:
        tdoc = swa._read_troubles_unlocked()
        difficult: Dict[str, object] = dict(tdoc.get("difficult") or {})

    if not difficult:
        print("疑难词表为空，无需重试。")
        return

    mappings = swa.get_wordbank_lemma_mappings()
    csv_set = swa.get_csv_english_set()
    # 已在词库（或映射目标已在词库）：清除疑难记录，不调 API
    cleared: List[str] = []
    for w in list(difficult.keys()):
        key = str(w or "").strip().lower()
        if not key:
            continue
        eff = str(mappings.get(key, key) or "").strip().lower()
        if eff in csv_set:
            if swa.delete_wordbank_difficult(key):
                cleared.append(key)
    if cleared:
        print(f"已在 words.csv（含管理员映射目标），已从疑难词移除: {len(cleared)} 个")

    with swa._TROUBLES_LOCK:
        tdoc = swa._read_troubles_unlocked()
        difficult = dict(tdoc.get("difficult") or {})

    to_retry: List[str] = []
    seen: Set[str] = set()
    csv_set = swa.get_csv_english_set()
    mappings = swa.get_wordbank_lemma_mappings()
    for w in difficult:
        key = str(w or "").strip().lower()
        if not key or key in seen:
            continue
        eff = str(mappings.get(key, key) or "").strip().lower()
        if eff in csv_set:
            continue
        seen.add(key)
        to_retry.append(key)

    print(f"疑难词剩余: {len(difficult)}")
    print(f"待 DeepSeek 重试: {len(to_retry)}")

    level_hint = (args.level or "").strip()
    _deepseek_generate_append(
        swa,
        to_retry,
        level_hint,
        args.dry_run,
        remove_difficult_on_success=True,
    )


def main() -> None:
    _ensure_repo_root()

    parser = argparse.ArgumentParser(description="NCE 课文 → words.csv 同步（复用 simple_web_app）")
    parser.add_argument(
        "--lesson",
        action="append",
        default=[],
        metavar="PATH",
        help="NCE 课文 JSON，可重复指定",
    )
    parser.add_argument(
        "--nce-dir",
        default=None,
        metavar="DIR",
        help="递归收集其下 .json（排除 manifest.json）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只统计与列出待生成词，不写 CSV、不调用 DeepSeek")
    parser.add_argument(
        "--level",
        default="",
        help="写入词条的 level 提示（小学/初中/高中/GRE），与线上一致",
    )
    parser.add_argument(
        "--no-spacy-match",
        action="store_true",
        help="匹配阶段不使用 spaCy（仅词库直配 + 规则），与课文快速路径类似",
    )
    parser.add_argument(
        "--retry-difficult",
        action="store_true",
        help="对 user_data_simple/_shared/wordbank_troubles.json 中的疑难词再次调用 DeepSeek（与课文模式互斥）",
    )
    args = parser.parse_args()

    if args.retry_difficult and (args.lesson or args.nce_dir):
        parser.error("使用 --retry-difficult 时不要指定 --lesson 或 --nce-dir")
    if not args.retry_difficult and not args.lesson and not args.nce_dir:
        parser.error("请指定 --lesson 和/或 --nce-dir，或使用 --retry-difficult")

    # 在仓库根目录下导入，保证 static/wordbanks 相对路径有效
    import simple_web_app as swa

    if args.retry_difficult:
        _run_retry_difficult(swa, args)
        return

    json_paths = _collect_json_paths(args.lesson, args.nce_dir)
    if not json_paths:
        raise SystemExit("未找到任何课文 JSON")

    full_text = _build_full_text(json_paths)
    if not full_text.strip():
        raise SystemExit("所选文件中无英文课文内容（lines[].english 为空）")

    use_spacy = not args.no_spacy_match
    lemmas = swa.spacy_extract_lemmas_from_article(full_text)
    if lemmas is None:
        raise SystemExit(
            "spaCy 不可用或模型未加载。请安装 spacy 与 en_core_web_sm："
            f" {sys.executable} -m spacy download en_core_web_sm"
        )

    csv_set = swa.get_csv_english_set()
    mappings = swa.get_wordbank_lemma_mappings()
    with swa._TROUBLES_LOCK:
        tdoc = swa._read_troubles_unlocked()
        difficult: Dict[str, object] = dict(tdoc.get("difficult") or {})

    spacy_lemma_map: Optional[Dict[str, str]] = None
    if use_spacy:
        spacy_lemma_map = swa._spacy_lemma_map_for_surfaces(lemmas)
        if not spacy_lemma_map:
            spacy_lemma_map = None

    matched = 0
    new_lemmas_ordered: List[str] = []
    seen_new: Set[str] = set()
    blocked: List[str] = []
    blocked_seen: Set[str] = set()

    for lem in lemmas:
        hit = swa._first_lemma_in_csv(
            lem, mappings, csv_set, use_spacy, spacy_lemma_map
        )
        if hit is not None:
            matched += 1
            continue
        target = swa._lemma_for_vocab_not_in_csv(lem, mappings)
        tl = target.strip().lower()
        if tl in csv_set:
            matched += 1
            continue
        if lem in difficult or tl in difficult:
            if tl not in blocked_seen:
                blocked_seen.add(tl)
                blocked.append(tl)
            continue
        if tl not in seen_new:
            seen_new.add(tl)
            new_lemmas_ordered.append(tl)

    print(f"课文文件: {len(json_paths)} 个")
    print(f"spaCy 提取 lemma 数（去重、已滤停用词）: {len(lemmas)}")
    print(f"已在 words.csv（含规则/spaCy 匹配）: {matched}")
    print(f"待生成（未在词库）: {len(new_lemmas_ordered)}")
    if blocked:
        print(f"疑难词跳过（见 user_data_simple/_shared/wordbank_troubles.json）: {len(blocked)}")

    if not new_lemmas_ordered:
        print("无需写入。")
        return

    level_hint = (args.level or "").strip()
    _deepseek_generate_append(
        swa,
        new_lemmas_ordered,
        level_hint,
        args.dry_run,
        remove_difficult_on_success=False,
    )


if __name__ == "__main__":
    main()

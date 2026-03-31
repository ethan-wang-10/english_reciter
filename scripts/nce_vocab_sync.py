#!/usr/bin/env python3
"""
从 NCE 课文 JSON 和/或外部词汇表提取词形，与 words.csv 比对；未命中则按批调用 DeepSeek
生成词条并追加到 static/wordbanks/words.csv。课文始终用 spaCy 取 token 表面形；加 --lemmatize 时用 spaCy 校验词形；写入词库前对名词简单复数规范为原形（与 Web VIP 导入一致），管理员映射优先。

复用 simple_web_app 中的 spaCy、DeepSeek、CSV 与疑难词逻辑（方案 A）。
与线上一致：词库匹配不使用快速启发式（无复数/-ed 等规则），仅管理员映射、表面形与（可选）spaCy 校验。

用法（请在项目根目录执行，或任意目录执行本脚本——会自动 chdir 到仓库根）：
  python scripts/nce_vocab_sync.py --lesson static/wordbanks/nce/NCE1/0001_001\\&002.Excuse\\ Me.json --dry-run
  python scripts/nce_vocab_sync.py --nce-dir static/wordbanks/nce/NCE1 --dry-run
  python scripts/nce_vocab_sync.py --nce-dir static/wordbanks/nce/NCE1 --level 初中

词汇表（每行一词，或 TOEFL 词典行：词头 + 空格 + [音标]）：
  python scripts/nce_vocab_sync.py --vocab-file static/wordbanks/CET4+6_edited.txt --dry-run
  python scripts/nce_vocab_sync.py --vocab-file static/wordbanks/TOEFL.txt --vocab-format toefl --dry-run
  python scripts/nce_vocab_sync.py --vocab-file a.txt --vocab-file b.txt --vocab-format auto

疑难词重试（user_data_simple/_shared/wordbank_troubles.json 中 difficult）：
  python scripts/nce_vocab_sync.py --retry-difficult --dry-run
  python scripts/nce_vocab_sync.py --retry-difficult --level 初中

依赖：课文模式需 spaCy + en_core_web_sm；DeepSeek 需环境变量 DEEPSEEK_API_KEY，或 config.json 中 deepseek_api_key（密文时需与 Web 相同 SECRET_KEY / DEEPSEEK_KEY_ENCRYPTION_SECRET 以解密）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set

_TOEFL_HEADWORD_RE = re.compile(r"^(.+?)\s+\[")

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


def _detect_vocab_format(path: Path) -> str:
    """根据首条非空行判断：含「词头 + 空格 + [」视为 toefl，否则 plain。"""
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if _TOEFL_HEADWORD_RE.match(s):
                return "toefl"
            return "plain"
    return "plain"


def _parse_plain_vocab_file(path: Path, normalize) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            key = normalize(s)
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(key)
    return out


def _parse_toefl_vocab_file(path: Path, normalize) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            m = _TOEFL_HEADWORD_RE.match(s)
            if not m:
                continue
            head = normalize(m.group(1).strip())
            if not head or head in seen:
                continue
            seen.add(head)
            out.append(head)
    return out


def _parse_vocab_file(path: Path, fmt: str, normalize) -> List[str]:
    eff = fmt.strip().lower()
    if eff == "auto":
        eff = _detect_vocab_format(path)
    if eff == "toefl":
        return _parse_toefl_vocab_file(path, normalize)
    if eff != "plain":
        raise SystemExit(f"未知词汇表格式: {fmt!r}（可用 plain / toefl / auto）")
    return _parse_plain_vocab_file(path, normalize)


def _merge_surfaces_preserve_order(surfaces: List[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for w in surfaces:
        if w in seen:
            continue
        seen.add(w)
        out.append(w)
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

    failed_surfaces: List[str] = []
    total_appended = 0
    difficult_cleared = 0

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
            if rows:
                n = swa.append_words_to_csv(rows)
                total_appended += n
                print(f"  本批已写入 {n} 条到 {swa.WORDS_CSV_FILE}（累计 {total_appended}）")
                if remove_difficult_on_success:
                    for row in rows:
                        en = str(row.get("english", "") or "").strip().lower()
                        if en and swa.delete_wordbank_difficult(en):
                            difficult_cleared += 1
            miss = [b for b in batch if b.lower() not in success]
        else:
            miss = list(batch)
        failed_surfaces.extend(miss)

    failed_surfaces = swa._dedupe_preserve_order(failed_surfaces)
    if failed_surfaces:
        swa.record_surfaces_to_difficult(failed_surfaces)

    if total_appended:
        print(f"\n共追加 {total_appended} 条到 {swa.WORDS_CSV_FILE}")
        if remove_difficult_on_success and difficult_cleared:
            print(f"已从疑难词中移除（生成成功）: {difficult_cleared} 个")
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
        help="（需同时指定 --lemmatize）不做 spaCy 词形校验，仅用词库直配与管理员映射",
    )
    parser.add_argument(
        "--lemmatize",
        action="store_true",
        help="用 spaCy 原型校验每个词是否可识别；课文仍取表面形；写入/DeepSeek 使用表面形或管理员映射（与 VIP 词汇导入一致）",
    )
    parser.add_argument(
        "--retry-difficult",
        action="store_true",
        help="对 user_data_simple/_shared/wordbank_troubles.json 中的疑难词再次调用 DeepSeek（与课文模式互斥）",
    )
    parser.add_argument(
        "--vocab-file",
        action="append",
        default=[],
        metavar="PATH",
        help="外部词汇表（可多次指定）；plain=每行一词，toefl=词头 + 空格 + [音标]…（见 --vocab-format）",
    )
    parser.add_argument(
        "--vocab-format",
        default="auto",
        choices=("plain", "toefl", "auto"),
        metavar="FMT",
        help="词汇表解析方式：plain / toefl / auto（默认 auto：按文件首行自动判断）",
    )
    args = parser.parse_args()

    if args.retry_difficult and (args.lesson or args.nce_dir or args.vocab_file):
        parser.error("使用 --retry-difficult 时不要指定 --lesson、--nce-dir 或 --vocab-file")
    if not args.retry_difficult and not args.lesson and not args.nce_dir and not args.vocab_file:
        parser.error("请指定 --lesson 和/或 --nce-dir 和/或 --vocab-file，或使用 --retry-difficult")

    # 在仓库根目录下导入，保证 static/wordbanks 相对路径有效
    import simple_web_app as swa

    if args.retry_difficult:
        _run_retry_difficult(swa, args)
        return

    json_paths = _collect_json_paths(args.lesson, args.nce_dir)
    normalize = swa._normalize_apostrophe_token

    vocab_surfaces: List[str] = []
    for raw in args.vocab_file:
        p = Path(raw)
        if not p.is_file():
            raise SystemExit(f"词汇表文件不存在: {p}")
        vocab_surfaces.extend(_parse_vocab_file(p.resolve(), args.vocab_format, normalize))
    vocab_surfaces = _merge_surfaces_preserve_order(vocab_surfaces)

    surfaces_from_article: List[str] = []
    if json_paths:
        full_text = _build_full_text(json_paths)
        if not full_text.strip():
            raise SystemExit("所选文件中无英文课文内容（lines[].english 为空）")
        surfaces_from_article = swa.spacy_extract_surfaces_from_article(full_text)
        if surfaces_from_article is None:
            raise SystemExit(
                "spaCy 不可用或模型未加载。请安装 spacy 与 en_core_web_sm："
                f" {sys.executable} -m spacy download en_core_web_sm"
            )

    combined = _merge_surfaces_preserve_order(vocab_surfaces + surfaces_from_article)
    if not combined:
        raise SystemExit("未从词汇表或课文收集到任何词")

    use_spacy_validate = args.lemmatize and (not args.no_spacy_match)
    csv_set = swa.get_csv_english_set()
    mappings = swa.get_wordbank_lemma_mappings()
    with swa._TROUBLES_LOCK:
        tdoc = swa._read_troubles_unlocked()
        difficult: Dict[str, object] = dict(tdoc.get("difficult") or {})

    lemma_map: Optional[Dict[str, str]] = None
    if use_spacy_validate:
        uniq_for_map = list(dict.fromkeys(s for s in combined if s not in mappings))
        if uniq_for_map and swa._wordbank_lemma_spacy_enabled() and swa._get_spacy_nlp() is not None:
            lemma_map = swa._spacy_lemma_map_for_surfaces(uniq_for_map)
            if not lemma_map:
                lemma_map = None

    matched = 0
    new_lemmas_ordered: List[str] = []
    seen_new: Set[str] = set()
    blocked: List[str] = []
    blocked_seen: Set[str] = set()
    invalid_surfaces = 0

    for surface in combined:
        if use_spacy_validate:
            if not swa._vocab_import_spacy_accepts_surface(surface, mappings, lemma_map):
                invalid_surfaces += 1
                continue
            target = (
                mappings[surface]
                if surface in mappings
                else swa._normalize_import_english_surface(surface)
            )
            tl = target.strip().lower()
            if tl in csv_set:
                matched += 1
                continue
            if surface in difficult or tl in difficult:
                if tl not in blocked_seen:
                    blocked_seen.add(tl)
                    blocked.append(tl)
                continue
            if tl not in seen_new:
                seen_new.add(tl)
                new_lemmas_ordered.append(tl)
        else:
            tl = swa._normalize_apostrophe_token(str(surface).strip())
            if not tl:
                continue
            canon = (
                mappings[tl]
                if tl in mappings
                else swa._normalize_import_english_surface(tl)
            )
            if canon in csv_set:
                matched += 1
                continue
            if tl in difficult or canon in difficult:
                if canon not in blocked_seen:
                    blocked_seen.add(canon)
                    blocked.append(canon)
                continue
            if canon not in seen_new:
                seen_new.add(canon)
                new_lemmas_ordered.append(canon)

    if args.vocab_file:
        print(f"词汇表文件: {len(args.vocab_file)} 个，解析词数（文件内去重后）: {len(vocab_surfaces)}")
    if json_paths:
        print(f"课文文件: {len(json_paths)} 个")
        print(f"spaCy 从课文提取表面形数（去重、已滤停用词）: {len(surfaces_from_article)}")
    print(f"合并后待匹配词数（保序去重）: {len(combined)}")
    if use_spacy_validate and invalid_surfaces:
        print(f"未通过 spaCy 词形校验（已跳过）: {invalid_surfaces}")
    print(
        f"已在 words.csv（{'表面形精确匹配；含 spaCy 校验' if use_spacy_validate else '仅原样或管理员映射'}）: {matched}"
    )
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

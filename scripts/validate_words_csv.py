#!/usr/bin/env python3
"""
校验 static/wordbanks/words.csv：必填字段、英文格式、spaCy 词形（与 Web 导入一致）。

- 必填非空：english, chinese, level, phonetic, example1, example1_cn, example2, example2_cn
- example1_form / example2_form 允许为空（与现有数据一致）
- 英文：与 DeepSeek 词条一致的小写 + 撇号/连字符规则
- spaCy：单段词用 _spacy_lemma_for_surface；含连字符时按段分别校验（如 ice-cream、man-made）

异常词（english 小写）写入 user_data_simple/_shared/wordbank_troubles.json 的 difficult，
复用 simple_web_app.record_surfaces_to_difficult。

用法（在项目根目录）：
  python scripts/validate_words_csv.py
  python scripts/validate_words_csv.py --dry-run
  python scripts/validate_words_csv.py --csv static/wordbanks/words.csv
  python scripts/validate_words_csv.py --remove-from-csv --dry-run   # 预览将删除的行
  python scripts/validate_words_csv.py --remove-from-csv            # 删除非法行并写入疑难词
  python scripts/validate_words_csv.py --spellcheck                # 额外：pyspellchecker 查拼写（需 pip install pyspellchecker）
  python scripts/validate_words_csv.py --spellcheck --spell-min-len 6

合并 token 与词头一致时，若已安装 NLTK WordNet 数据，会用 synsets 兜底（如 wed）；未安装则仅依赖多词白名单与 spaCy。

--spellcheck 仅在通过格式与 spaCy 后执行；长度小于 --spell-min-len 的分段跳过（减少短词/专有名词误报）。
pyspellchecker 为美式词频；英式拼写可写入 scripts/spelling_extra_words.txt（或 --spell-extra-file）以补充。
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple

if TYPE_CHECKING:
    from spellchecker import SpellChecker

ROOT = Path(__file__).resolve().parent.parent
_SPELL_EXTRA_DEFAULT = ROOT / "scripts" / "spelling_extra_words.txt"

_ENGLISH_RE = re.compile(r"^[a-z][a-z'\-]*$")
_PART_RE = re.compile(r"^[a-z][a-z']*$")

# spaCy 会拆成多 token 的常见词头（WordNet 可能无 cannot 等义项）
_MULTI_TOKEN_HEADWORDS = frozenset(
    {
        "cannot",
        "gonna",
        "wanna",
        "gotta",
        "kinda",
        "oughta",
        "coulda",
        "woulda",
        "shoulda",
    }
)


def _ensure_repo_root() -> None:
    if os.getcwd() != str(ROOT):
        os.chdir(ROOT)
    root_str = str(ROOT)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)


def _row_required_non_empty() -> Tuple[str, ...]:
    return (
        "english",
        "chinese",
        "level",
        "phonetic",
        "example1",
        "example1_cn",
        "example2",
        "example2_cn",
    )


def _spacy_accepts_headword(swa, nlp, english_lower: str) -> bool:
    """单段 _spacy_lemma_for_surface；连字符分段各自校验；单字串被 spaCy 拆成多 token 时合并比对并可用 WordNet 兜底。"""
    s = swa._normalize_apostrophe_token(str(english_lower).strip())
    if not s or not _ENGLISH_RE.match(s):
        return False
    if "-" in s:
        for part in s.split("-"):
            if not part:
                return False
            if not _PART_RE.match(part):
                return False
            if swa._spacy_lemma_for_surface(part) is None:
                return False
        return True
    if swa._spacy_lemma_for_surface(s) is not None:
        return True
    if s in _MULTI_TOKEN_HEADWORDS:
        return True
    doc = nlp(s)
    if len(doc) == 1:
        return False
    merged = "".join(t.text.lower() for t in doc)
    if merged != s:
        return False
    try:
        from nltk.corpus import wordnet as wn

        if wn.synsets(s):
            return True
    except LookupError:
        pass
    except Exception:
        pass
    return False


def _load_spell_extra_words(path: Path) -> List[str]:
    if not path.is_file():
        return []
    out: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s.lower())
    return out


def _spellcheck_reasons(spell: "SpellChecker", en_lower: str, min_len: int) -> List[str]:
    """对整词或连字符分段做词典检查；仅报告长度 >= min_len 的分段。"""
    s = str(en_lower).strip().lower()
    if not s:
        return []
    parts = s.split("-") if "-" in s else [s]
    out: List[str] = []
    for p in parts:
        if not p or len(p) < min_len:
            continue
        unk = spell.unknown([p])
        if not unk:
            continue
        sug = spell.correction(p)
        if sug and sug != p:
            out.append(f"拼写疑似错误:{p}→{sug}")
        else:
            out.append(f"词典未收录:{p}")
    return out


def validate_row(
    swa,
    nlp,
    row: Dict[str, str],
    *,
    spell: Optional["SpellChecker"] = None,
    spell_min_len: int = 5,
) -> List[str]:
    reasons: List[str] = []
    for k in swa._CSV_FIELDS:
        if k not in row:
            reasons.append(f"缺少列:{k}")

    required = _row_required_non_empty()
    for k in required:
        v = str(row.get(k, "") or "").strip()
        if not v:
            reasons.append(f"空字段:{k}")

    en = str(row.get("english", "") or "").strip().lower()
    if en:
        if not _ENGLISH_RE.match(en):
            reasons.append("english:格式非法")
        elif not _spacy_accepts_headword(swa, nlp, en):
            reasons.append("english:spaCy无法识别为合法词形(单段或连字符分段)")
        elif spell is not None:
            for sr in _spellcheck_reasons(spell, en, spell_min_len):
                reasons.append(f"english:{sr}")
    return reasons


def _write_csv_rows_atomic(path: Path, fieldnames: List[str], rows: List[dict]) -> None:
    """原子写入全表（用于非默认路径；默认路径请用 simple_web_app._write_words_csv_rows_atomic_under_lock）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".csv", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({k: str(row.get(k, "") or "").strip() for k in fieldnames})
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main() -> None:
    _ensure_repo_root()
    parser = argparse.ArgumentParser(description="校验 words.csv 并写入疑难词")
    parser.add_argument(
        "--csv",
        default="static/wordbanks/words.csv",
        metavar="PATH",
        help="CSV 路径（默认 static/wordbanks/words.csv）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印问题；与 --remove-from-csv 联用时也不改 CSV、不写疑难词",
    )
    parser.add_argument(
        "--remove-from-csv",
        action="store_true",
        help="从指定 CSV 中删除校验未通过的行（默认仍写入疑难词；--dry-run 时仅预览）",
    )
    parser.add_argument(
        "--spellcheck",
        action="store_true",
        help="启用 pyspellchecker 拼写检查（在格式与 spaCy 通过后执行；需安装 pyspellchecker）",
    )
    parser.add_argument(
        "--spell-min-len",
        type=int,
        default=5,
        metavar="N",
        help="拼写检查最短分段长度（默认 5；连字符词按段计）",
    )
    parser.add_argument(
        "--spell-extra-file",
        type=Path,
        default=None,
        metavar="PATH",
        help=f"补充词表路径（默认 {_SPELL_EXTRA_DEFAULT.name}；不存在则忽略）",
    )
    args = parser.parse_args()

    import simple_web_app as swa

    spell: Optional["SpellChecker"] = None
    if args.spellcheck:
        try:
            from spellchecker import SpellChecker

            spell = SpellChecker()
            extra_path = args.spell_extra_file or _SPELL_EXTRA_DEFAULT
            extras = _load_spell_extra_words(extra_path)
            if extras:
                spell.word_frequency.load_words(extras)
                print(
                    f"拼写检查: 已加载补充词 {len(extras)} 个（{extra_path}）",
                    file=sys.stderr,
                )
        except ImportError:
            raise SystemExit(
                "未安装 pyspellchecker。请执行: pip install pyspellchecker"
            ) from None

    csv_path = Path(args.csv)
    if not csv_path.is_file():
        raise SystemExit(f"文件不存在: {csv_path.resolve()}")

    nlp = swa._get_spacy_nlp()
    if nlp is None:
        raise SystemExit(
            "spaCy 模型不可用。请安装: pip install -r requirements-simple.txt "
            f"并执行 {sys.executable} -m spacy download en_core_web_sm"
        )

    bad: List[str] = []
    seen: Set[str] = set()
    dup: List[str] = []
    line_no = 1
    all_rows: List[Tuple[dict, int]] = []

    with open(csv_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise SystemExit("CSV 无表头")
        fn = [str(x).strip() for x in reader.fieldnames]
        expected = list(swa._CSV_FIELDS)
        if fn != expected:
            raise SystemExit(
                f"表头列与预期不一致。\n预期: {expected}\n实际: {fn}"
            )

        for row in reader:
            line_no += 1
            en_raw = str(row.get("english", "") or "").strip().lower()
            if en_raw:
                if en_raw in seen:
                    dup.append(f"第{line_no}行 重复 english:{en_raw}")
                seen.add(en_raw)

            all_rows.append((dict(row), line_no))

    smin = max(1, int(args.spell_min_len))

    checked: List[Tuple[dict, int, List[str]]] = []
    for row, ln in all_rows:
        reasons = validate_row(
            swa,
            nlp,
            row,
            spell=spell,
            spell_min_len=smin,
        )
        checked.append((row, ln, reasons))
        if reasons:
            en_raw = str(row.get("english", "") or "").strip().lower()
            bad.append(en_raw or f"(第{ln}行无english)")
            label = en_raw or f"(行{ln})"
            print(f"{label}\t{'; '.join(reasons)}\t(line {ln})")

    if dup:
        print("\n--- 重复 english（请人工处理，不写入疑难词）---")
        for d in dup:
            print(d)

    to_record = sorted({b for b in bad if b and not b.startswith("(")})

    kept_rows = [dict(r) for r, ln, reasons in checked if not reasons]
    removed_n = len(all_rows) - len(kept_rows)

    if args.remove_from_csv:
        if args.dry_run and removed_n:
            print(
                f"\n[dry-run] 将从 CSV 删除 {removed_n} 行，保留 {len(kept_rows)} 行（未写入）"
            )
        elif not args.dry_run and removed_n:
            with swa._words_csv_interprocess_lock():
                with swa._words_csv_lock():
                    if csv_path.resolve() == swa.WORDS_CSV_FILE.resolve():
                        swa._write_words_csv_rows_atomic_under_lock(kept_rows)
                    else:
                        _write_csv_rows_atomic(csv_path, expected, kept_rows)
            print(
                f"\n已从 {csv_path} 删除 {removed_n} 行，保留 {len(kept_rows)} 行。"
            )
            if csv_path.resolve() == swa.WORDS_CSV_FILE.resolve():
                swa.invalidate_words_csv_cache()
        elif not args.dry_run and not removed_n:
            print("\n无行需从 CSV 删除。")

    if not args.dry_run and to_record:
        swa.record_surfaces_to_difficult(to_record)
        print(
            f"\n已写入疑难词 difficult: {len(to_record)} 个 -> {swa.WORDBANK_TROUBLES_FILE}"
        )
    elif args.dry_run and to_record:
        print(f"\n[dry-run] 将写入疑难词 {len(to_record)} 个（未写入）")

    if not to_record and not dup and removed_n == 0:
        print("\n未发现问题。")


if __name__ == "__main__":
    main()

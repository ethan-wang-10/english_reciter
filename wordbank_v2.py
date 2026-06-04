"""
新词库 words_v2.json：多义项、词组；与 words.csv 双轨并存（对外统一接口以 v2 为准，同键覆盖 CSV）。
持久化以 senses 为权威；chinese_summary 与 example1..N 在读取时由 materialize_v2_entry_for_read 派生。
见 docs/refine_work_bank.md。
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import logging

from project_paths import STATIC_WB_DIR, WORDS_INTERPROCESS_LOCKFILE

logger = logging.getLogger(__name__)

WORDS_V2_FILE = STATIC_WB_DIR / "words_v2.json"
# 与 simple_web_app 中 words.csv 锁同路径，保证多进程下与 CSV 写互斥

_words_v2_lock = threading.Lock()
_words_v2_cache: Optional[List[dict]] = None
_words_v2_cache_mtime: float = 0.0
_words_v2_by_key: Optional[Dict[str, dict]] = None

_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def normalize_english_key(s: str) -> str:
    """与词库查询键一致：trim + 小写 + 空白规范化。"""
    return " ".join(str(s).strip().lower().split())


def build_chinese_summary(senses: List[dict]) -> str:
    """由义项列表生成确定性中文摘要（多义项用全角分号连接）。"""
    if not senses:
        return ""
    parts: List[str] = []
    for s in senses:
        line = format_single_sense_chinese(s)
        if line:
            parts.append(line)
    return "；".join(parts)


def format_single_sense_chinese(sense: dict) -> str:
    """单义项一行中文（与 build_chinese_summary 中单条格式一致）。"""
    zh = str(sense.get("definition_zh", "")).strip()
    if not zh:
        return ""
    pos = str(sense.get("pos") or "").strip().lower()
    abbr = _pos_display(pos)
    if abbr:
        return f"{abbr} {zh}"
    return zh


def chinese_sense_lines_from_senses(senses: List[dict]) -> List[str]:
    """多义项时各义项一行（与 format_single_sense_chinese 一致）；单义项或非列表时返回空。"""
    if not isinstance(senses, list) or len(senses) <= 1:
        return []
    out: List[str] = []
    for s in senses:
        line = format_single_sense_chinese(s)
        if line:
            out.append(line)
    return out if len(out) > 1 else []


def _pos_display(pos: str) -> str:
    if not pos:
        return ""
    m = {
        "noun": "n.",
        "n": "n.",
        "verb": "v.",
        "v": "v.",
        "adj": "adj.",
        "adjective": "adj.",
        "adv": "adv.",
        "adverb": "adv.",
        "phrase": "phr.",
        "prep": "prep.",
        "preposition": "prep.",
        "conj": "conj.",
        "conjunction": "conj.",
    }
    if pos in m:
        return m[pos]
    if len(pos) <= 5 and pos.isalpha():
        return pos + "." if not pos.endswith(".") else pos
    return pos


def assign_sense_ids(english_norm: str, senses: List[dict]) -> None:
    for i, s in enumerate(senses):
        s["id"] = f"{english_norm}#s{i}"


def _has_cjk(s: str) -> bool:
    return bool(_CJK_RE.search(str(s or "")))


def _has_latin(s: str) -> bool:
    return bool(_LATIN_RE.search(str(s or "")))


def _valid_english_example(s: str) -> bool:
    text = str(s or "").strip()
    return bool(text) and _has_latin(text) and not _has_cjk(text)


def _valid_chinese_example(s: str) -> bool:
    text = str(s or "").strip()
    return bool(text) and _has_cjk(text)


def _normalize_sense_examples(sense: dict) -> bool:
    """
    Ensure example_en is English and example_cn is Chinese.
    DeepSeek occasionally swaps these fields; swap back when both values are recoverable.
    """
    ex_en = str(sense.get("example_en", "")).strip()
    ex_cn = str(sense.get("example_cn", "")).strip()
    if _has_cjk(ex_en) and _valid_english_example(ex_cn):
        sense["example_en"] = ex_cn
        sense["example_cn"] = ex_en
        return _valid_chinese_example(ex_en)
    return _valid_english_example(ex_en) and _valid_chinese_example(ex_cn)


def finalize_v2_entry_from_deepseek(raw: dict) -> Optional[dict]:
    """
    将 DeepSeek 返回的单条 dict 规范为 LexicalEntryV2；非法则 None。
    """
    en = normalize_english_key(str(raw.get("english", "")))
    if not en:
        return None
    senses_in = raw.get("senses")
    if not isinstance(senses_in, list) or not senses_in:
        return None
    senses: List[dict] = []
    for s in senses_in:
        if not isinstance(s, dict):
            return None
        dz = str(s.get("definition_zh", "")).strip()
        if not dz:
            return None
        po = str(s.get("phonetic_override", "")).strip()
        ex_en = str(s.get("example_en", "")).strip()
        ex_cn = str(s.get("example_cn", "")).strip()
        ex_form = str(s.get("example_form", "")).strip()
        senses.append(
            {
                "id": "",
                "pos": str(s.get("pos", "")).strip() or None,
                "definition_zh": dz,
                "phonetic_override": po or None,
                "example_en": ex_en,
                "example_cn": ex_cn,
                "example_form": ex_form,
            }
        )
    assign_sense_ids(en, senses)
    # 兼容旧 prompt：仅顶层 example1/example2 时按义项顺序填入
    _hydrate_sense_examples_from_legacy_top_level(senses, raw)
    for i, sense in enumerate(senses):
        if not _normalize_sense_examples(sense):
            logger.warning(
                "拒绝 DeepSeek v2 词条 %r: sense[%s] 例句中英文字段不合法",
                en,
                i,
            )
            return None
    ek = str(raw.get("entry_kind", "")).strip().lower()
    if ek not in ("word", "phrase"):
        ek = "phrase" if " " in en else "word"
    # 持久化以 senses 为唯一数据源；chinese_summary / example1..N 由 materialize_v2_entry_for_read 派生
    entry: Dict[str, Any] = {
        "english": en,
        "entry_kind": ek,
        "level": str(raw.get("level", "")).strip(),
        "phonetic": str(raw.get("phonetic", "")).strip(),
        "senses": senses,
    }
    return entry


def _hydrate_sense_examples_from_legacy_top_level(senses: List[dict], raw: dict) -> None:
    """若义项内无例句，用顶层 example1/example2… 按顺序补到前几条义项。"""
    legacy_slots = [
        (
            str(raw.get("example1", "")).strip(),
            str(raw.get("example1_form", "")).strip(),
            str(raw.get("example1_cn", "")).strip(),
        ),
        (
            str(raw.get("example2", "")).strip(),
            str(raw.get("example2_form", "")).strip(),
            str(raw.get("example2_cn", "")).strip(),
        ),
    ]
    for i, s in enumerate(senses):
        has = bool(s.get("example_en") or s.get("example_cn"))
        if has:
            continue
        if i < len(legacy_slots):
            le, lf, lc = legacy_slots[i]
            if le or lc:
                s["example_en"] = le
                s["example_form"] = lf
                s["example_cn"] = lc


def _flatten_sense_examples_into_entry(entry: Dict[str, Any], senses: List[dict]) -> None:
    """将 senses[].example_* 写入 example1..exampleN（N=len(senses)，上限 8）。"""
    max_n = min(len(senses), 8)
    for i in range(max_n):
        s = senses[i]
        en = str(s.get("example_en", "")).strip()
        cn = str(s.get("example_cn", "")).strip()
        form = str(s.get("example_form", "")).strip()
        idx = i + 1
        entry[f"example{idx}"] = en
        entry[f"example{idx}_form"] = form
        entry[f"example{idx}_cn"] = cn
    # 清空多余槽位（条目从多变少时）
    for j in range(max_n + 1, 9):
        entry.pop(f"example{j}", None)
        entry.pop(f"example{j}_form", None)
        entry.pop(f"example{j}_cn", None)


def strip_redundant_v2_fields_for_storage(entry: dict) -> dict:
    """
    写入 words_v2.json 前去掉可由 senses 派生的键，避免与 senses 重复落盘。
    无 senses 时不改动（兼容异常手工条目）。
    """
    out = dict(entry)
    senses = out.get("senses")
    if not (isinstance(senses, list) and len(senses) > 0):
        return out
    out.pop("chinese_summary", None)
    for k in range(1, 9):
        out.pop(f"example{k}", None)
        out.pop(f"example{k}_form", None)
        out.pop(f"example{k}_cn", None)
    return out


def materialize_v2_entry_for_read(entry: dict) -> dict:
    """
    从权威字段 senses 派生 chinese_summary 与 example1..N，供查询与 CSV 扁平行兼容。
    新写入的 v2 条目可只含 senses（不落冗余）；旧文件若仍含摘要/扁平行，此处以 senses 为准覆盖。
    无 senses 时保留条目内已有扁平字段（极少见）。
    """
    out = dict(entry)
    senses = out.get("senses")
    if isinstance(senses, list) and len(senses) > 0:
        out["chinese_summary"] = build_chinese_summary(senses)
        _flatten_sense_examples_into_entry(out, senses)
    return out


def v2_entry_to_flat_csv_row(entry: dict) -> dict:
    """供 pick_example_for_word / csv_word_to_review_item 使用的扁平行（含 chinese 键）。"""
    mat = materialize_v2_entry_for_read(entry)
    ch = str(mat.get("chinese_summary") or mat.get("chinese") or "").strip()
    out: Dict[str, Any] = {
        "english": str(mat.get("english", "")).strip(),
        "chinese": ch,
        "level": str(mat.get("level", "")).strip(),
        "phonetic": str(mat.get("phonetic", "")).strip(),
    }
    for k in range(1, 9):
        out[f"example{k}"] = str(mat.get(f"example{k}", "") or "").strip()
        out[f"example{k}_form"] = str(mat.get(f"example{k}_form", "") or "").strip()
        out[f"example{k}_cn"] = str(mat.get(f"example{k}_cn", "") or "").strip()
    senses_in = mat.get("senses")
    sl = chinese_sense_lines_from_senses(senses_in if isinstance(senses_in, list) else [])
    if sl:
        out["chinese_sense_lines"] = sl
    return out


@contextmanager
def _interprocess_lock() -> Any:
    """与 simple_web_app._words_csv_interprocess_lock 同文件，避免并发写 CSV 与 JSON 冲突。"""
    WORDS_INTERPROCESS_LOCKFILE.parent.mkdir(parents=True, exist_ok=True)
    f = open(WORDS_INTERPROCESS_LOCKFILE, "a+b", buffering=0)
    try:
        if sys.platform == "win32":
            import msvcrt

            f.seek(0, os.SEEK_END)
            if f.tell() == 0:
                f.write(b"\0")
                f.flush()
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if sys.platform == "win32":
            import msvcrt

            try:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        else:
            import fcntl

            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        try:
            f.close()
        except OSError:
            pass


def _read_words_v2_raw_unlocked() -> List[dict]:
    """供缓存加载：解析失败时返回 []（仅影响内存展示，不写入）。"""
    if not WORDS_V2_FILE.is_file():
        return []
    try:
        # utf-8-sig：避免 Windows 下带 BOM 的 JSON 解析失败
        raw = WORDS_V2_FILE.read_text(encoding="utf-8-sig")
        data = json.loads(raw)
        if not isinstance(data, list):
            return []
        return [x for x in data if isinstance(x, dict)]
    except (OSError, json.JSONDecodeError) as e:
        logger.error("读取 words_v2.json 失败: %s", e)
        return []


def _read_words_v2_for_append() -> List[dict]:
    """
    供 append 使用：文件不存在或仅空白时返回 []。
    若文件存在且非空白却不能解析为 JSON 数组，**抛错**，禁止退化为 [] 后整文件覆写（否则易删掉原有条目）。
    """
    if not WORDS_V2_FILE.is_file():
        return []
    try:
        raw = WORDS_V2_FILE.read_text(encoding="utf-8-sig")
    except OSError as e:
        logger.error("读取 words_v2.json 失败: %s", e)
        raise
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("words_v2.json JSON 无法解析（追加前拒绝写入以免覆盖）: %s", e)
        raise ValueError(
            "words_v2.json 存在但无法解析为 JSON，已中止追加。请修复文件或从备份恢复后再试。"
        ) from e
    if not isinstance(data, list):
        raise ValueError(
            "words_v2.json 根节点必须是 JSON 数组，已中止追加以免覆盖原有数据。"
        )
    return [x for x in data if isinstance(x, dict)]


def invalidate_words_v2_cache() -> None:
    global _words_v2_cache, _words_v2_cache_mtime, _words_v2_by_key
    with _words_v2_lock:
        _words_v2_cache = None
        _words_v2_cache_mtime = 0.0
        _words_v2_by_key = None


def load_words_v2_list() -> List[dict]:
    """带 mtime 缓存的新词库列表。"""
    global _words_v2_cache, _words_v2_cache_mtime, _words_v2_by_key
    with _words_v2_lock:
        try:
            mtime = WORDS_V2_FILE.stat().st_mtime if WORDS_V2_FILE.exists() else 0.0
        except OSError:
            mtime = 0.0
        if _words_v2_cache is not None and mtime == _words_v2_cache_mtime:
            return _words_v2_cache

    with _interprocess_lock():
        with _words_v2_lock:
            try:
                mtime2 = WORDS_V2_FILE.stat().st_mtime if WORDS_V2_FILE.exists() else 0.0
            except OSError:
                mtime2 = 0.0
            if _words_v2_cache is not None and mtime2 == _words_v2_cache_mtime:
                return _words_v2_cache
            rows = _read_words_v2_raw_unlocked()
            _words_v2_cache = rows
            _words_v2_cache_mtime = mtime2
            _words_v2_by_key = None
            return rows


def load_words_v2_by_key() -> Dict[str, dict]:
    """english 规范化键 -> 完整 v2 条目。"""
    global _words_v2_by_key
    with _words_v2_lock:
        if _words_v2_by_key is not None:
            return _words_v2_by_key
    load_words_v2_list()
    with _words_v2_lock:
        if _words_v2_by_key is None:
            m: Dict[str, dict] = {}
            for e in _words_v2_cache or []:
                k = normalize_english_key(e.get("english", ""))
                if k:
                    m[k] = e
            _words_v2_by_key = m
        return _words_v2_by_key


def get_v2_english_key_set() -> set:
    return set(load_words_v2_by_key().keys())


def _write_words_v2_atomic_under_lock(rows: List[dict]) -> None:
    global _words_v2_cache, _words_v2_cache_mtime, _words_v2_by_key
    STATIC_WB_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=False)
    fd, tmp = tempfile.mkstemp(suffix=".json", dir=str(STATIC_WB_DIR), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, WORDS_V2_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _words_v2_cache = None
    _words_v2_by_key = None
    try:
        _words_v2_cache_mtime = WORDS_V2_FILE.stat().st_mtime if WORDS_V2_FILE.exists() else 0.0
    except OSError:
        _words_v2_cache_mtime = 0.0


def append_words_v2_entries(entries: List[dict]) -> Tuple[int, List[str]]:
    """
    追加新词库条目；已存在同一 english（规范化键）则跳过。
    返回 (实际追加条数, 跳过的键列表)。
    """
    if not entries:
        return 0, []
    with _interprocess_lock():
        with _words_v2_lock:
            data = _read_words_v2_for_append()
            existing_keys = {normalize_english_key(e.get("english", "")) for e in data if e.get("english")}
            skipped: List[str] = []
            new_ones: List[dict] = []
            for ent in entries:
                k = normalize_english_key(ent.get("english", ""))
                if not k:
                    continue
                if k in existing_keys:
                    skipped.append(k)
                    continue
                new_ones.append(strip_redundant_v2_fields_for_storage(ent))
                existing_keys.add(k)
            if not new_ones:
                return 0, skipped
            _write_words_v2_atomic_under_lock(data + new_ones)
            return len(new_ones), skipped

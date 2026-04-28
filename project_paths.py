"""
项目内共享路径常量（避免 simple_web_app / wordbank_v2 等处重复定义导致漂移）。
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
STATIC_WB_DIR = PROJECT_ROOT / "static" / "wordbanks"
# 与 words.csv、words_v2 写入互斥；须全项目唯一路径
WORDS_INTERPROCESS_LOCKFILE = STATIC_WB_DIR / ".words.csv.lock"

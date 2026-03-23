#!/usr/bin/env python3
"""
内置词库唯一数据源：static/wordbanks/words.csv
本地修改 CSV 后提交并部署即可更新词库。本脚本用于快速查看行数与按 level 分布。
"""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "static" / "wordbanks" / "words.csv"


def main() -> None:
    if not CSV_PATH.exists():
        raise SystemExit(f"缺少词库文件: {CSV_PATH}")
    with open(CSV_PATH, "r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    n = len(rows)
    by_level = Counter((r.get("level") or "").strip() or "(空)" for r in rows)
    print(f"words.csv: 共 {n} 行（表头除外）")
    for lv, c in sorted(by_level.items(), key=lambda x: (-x[1], x[0])):
        print(f"  {lv}: {c}")


if __name__ == "__main__":
    main()

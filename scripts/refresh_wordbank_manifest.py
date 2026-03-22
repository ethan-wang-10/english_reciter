#!/usr/bin/env python3
"""
唯一词库数据源：static/wordbanks/{primary,junior,senior}.json
根据各文件中的 words 长度刷新 manifest.json 中的 count。
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WB = ROOT / "static" / "wordbanks"

PHASES = [
    ("primary", "小学", "小学英语词汇（含例句简写）"),
    ("junior", "初中", "初中英语词汇（含例句简写）"),
    ("senior", "高中", "高中英语词汇（含例句简写）"),
]


def main() -> None:
    manifest = {
        "schema": "english_reciter.wordbank.manifest/v1",
        "version": 1,
        "description": "系统词库：小学 / 初中 / 高中，供家长勾选导入",
        "phases": [],
    }
    for phase_id, label, desc in PHASES:
        path = WB / f"{phase_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        words = data.get("words")
        if not isinstance(words, list):
            raise SystemExit(f"{path}: 缺少 words 数组")
        count = len(words)
        manifest["phases"].append(
            {
                "id": phase_id,
                "label": label,
                "file": f"{phase_id}.json",
                "description": desc,
                "count": count,
            }
        )
        print(f"{phase_id}: {count} 词")

    out = WB / "manifest.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

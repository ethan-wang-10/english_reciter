#!/usr/bin/env python3
"""
将 users.json 导入 users.sqlite3（显式迁移 / 运维工具）。

应用首次启动时：若 users.sqlite3 为空且存在 users.json，会自动迁移并重命名备份；
本脚本用于在已有库上强制从指定 JSON 导入，或 dry-run 检查。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from user_store import (  # noqa: E402
    import_users_from_json,
    user_table_count,
    users_json_path,
    users_sqlite_path,
)


def _sqlite_users_row_count(sqlite_path: Path) -> int:
    if not sqlite_path.is_file():
        return 0
    conn = sqlite3.connect(str(sqlite_path))
    try:
        cur = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users' LIMIT 1"
        )
        if not cur.fetchone():
            return 0
        return int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])
    finally:
        conn.close()


def main() -> int:
    p = argparse.ArgumentParser(description="将 users.json 导入 SQLite 用户表")
    p.add_argument(
        "--data-dir",
        type=Path,
        default=Path("user_data_simple"),
        help="数据目录（含 users.json / users.sqlite3）",
    )
    p.add_argument(
        "--json",
        type=Path,
        default=None,
        help="源 JSON 路径，默认 <data-dir>/users.json",
    )
    p.add_argument(
        "--replace",
        action="store_true",
        help="清空现有 SQLite 用户表后再导入（危险：请先备份 users.sqlite3）",
    )
    p.add_argument(
        "--no-backup-json",
        action="store_true",
        help="导入成功后不重命名源 JSON（默认会改名为 *.imported_*.bak）",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印当前 SQLite 行数与 JSON 中用户数，不写库、不触发应用内自动迁移",
    )
    args = p.parse_args()
    data_dir = args.data_dir.resolve()
    json_path = (args.json or users_json_path(data_dir)).resolve()

    if not json_path.is_file():
        print(f"错误: 找不到 JSON 文件: {json_path}", file=sys.stderr)
        return 1

    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"错误: 无法解析 JSON: {e}", file=sys.stderr)
        return 1
    if not isinstance(data, dict):
        print("错误: JSON 根须为对象", file=sys.stderr)
        return 1
    n_json = sum(1 for v in data.values() if isinstance(v, dict))

    sqlite_path = users_sqlite_path(data_dir)
    n_sql = _sqlite_users_row_count(sqlite_path)

    print(f"数据目录: {data_dir}")
    print(f"SQLite:   {sqlite_path}  当前行数: {n_sql}")
    print(f"JSON:     {json_path}  有效用户对象: {n_json}")

    if args.dry_run:
        print("dry-run：未修改数据库")
        return 0

    if n_json == 0:
        print("错误: JSON 中无有效用户对象", file=sys.stderr)
        return 1

    if n_sql > 0 and not args.replace:
        print(
            "错误: SQLite 中已有用户。若要覆盖请使用 --replace（务必先备份 users.sqlite3）。",
            file=sys.stderr,
        )
        return 1

    inserted = import_users_from_json(
        json_path,
        data_dir=data_dir,
        replace_existing=bool(args.replace or n_sql == 0),
        backup_json=not args.no_backup_json,
    )
    print(f"已导入 {inserted} 条用户记录（导入后 SQLite 行数: {user_table_count()}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

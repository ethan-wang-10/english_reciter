#!/usr/bin/env python3
"""
从 nce.ichochy.com 同源数据源抓取新概念英语全册课文（英文 + 中文对照）。

站点课件托管在 bookPath（见 iChochy/NCE 的 data.json），与在线播放器使用相同的
book.json + .lrc 格式。直接 HTTP 请求会被 Cloudflare 拦截，需使用 curl_cffi 的
浏览器 TLS 指纹。

用法:
  pip install -r scripts/requirements-nce-scraper.txt
  python scripts/fetch_nce_corpus.py --output-dir static/wordbanks/nce
  python scripts/fetch_nce_corpus.py --books NCE1 --max-units 5   # 试跑

版权声明: 教材内容版权归原出版社所有；本脚本仅作个人学习用途，请支持正版。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

DATA_JSON_URL = "https://cdn.jsdelivr.net/gh/iChochy/NCE@main/data.json"

# 与 js/main.js 中 LRCParser 一致
LRC_LINE_RE = re.compile(r"\[(\d{2}):(\d{2})\.(\d{2,3})\](.+)")


def parse_lrc(lrc_text: str) -> list[dict[str, Any]]:
    lines = []
    for line in lrc_text.splitlines():
        m = LRC_LINE_RE.match(line.strip())
        if not m:
            continue
        minutes, seconds, ms_raw, rest = m.groups()
        ms = int(ms_raw)
        if len(ms_raw) == 2:
            ms *= 10
        t = int(minutes) * 60 + int(seconds) + ms / 1000.0 - 0.5
        text = rest.strip()
        parts = [p.strip() for p in text.split("|")]
        lines.append(
            {
                "time": t,
                "english": parts[0] if parts else "",
                "chinese": parts[1] if len(parts) > 1 else "",
                "raw": text,
            }
        )
    lines.sort(key=lambda x: x["time"])
    return lines


def fetch_text(session: Any, url: str) -> str:
    r = session.get(url, impersonate="chrome", timeout=60)
    r.raise_for_status()
    return r.text


def safe_filename(title: str) -> str:
    s = re.sub(r'[<>:"/\\|?*]', "_", title)
    return s.strip() or "untitled"


def main() -> int:
    parser = argparse.ArgumentParser(description="抓取新概念英语课文（LRC → 中英对照）")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("static/wordbanks/nce"),
        help="输出根目录（默认 static/wordbanks/nce）",
    )
    parser.add_argument(
        "--books",
        type=str,
        default="NCE1,NCE2,NCE3,NCE4",
        help="逗号分隔的课本 key，默认四册主版本",
    )
    parser.add_argument(
        "--include-85",
        action="store_true",
        help="同时抓取 NCE1(85)…NCE4(85) 版本",
    )
    parser.add_argument(
        "--max-units",
        type=int,
        default=0,
        help="每册最多抓取单元数（0 表示不限制，用于试跑）",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.35,
        help="请求间隔秒数，降低对源站压力",
    )
    parser.add_argument(
        "--data-json-url",
        type=str,
        default=DATA_JSON_URL,
        help="data.json 地址（课本列表与 bookPath）",
    )
    args = parser.parse_args()

    try:
        from curl_cffi import requests as cf_requests
    except ImportError:
        print(
            "需要安装 curl_cffi: pip install -r scripts/requirements-nce-scraper.txt",
            file=sys.stderr,
        )
        return 1

    session = cf_requests.Session()

    raw = fetch_text(session, args.data_json_url)
    catalog = json.loads(raw)
    wanted = {k.strip() for k in args.books.split(",") if k.strip()}
    books = [b for b in catalog.get("books", []) if b.get("key") in wanted]

    if args.include_85:
        extra = {f"NCE{i}(85)" for i in range(1, 5)}
        for b in catalog.get("books", []):
            if b.get("key") in extra:
                books.append(b)

    if not books:
        print("未匹配到任何课本，请检查 --books 与 data.json 中的 key。", file=sys.stderr)
        return 1

    out_root: Path = args.output_dir
    out_root.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {"books": [], "source": args.data_json_url}

    for book in books:
        key = book["key"]
        base = book["bookPath"].rstrip("/")
        book_dir = out_root / safe_filename(key)
        book_dir.mkdir(parents=True, exist_ok=True)

        book_json_url = f"{base}/book.json"
        print(f"→ {key}: {book_json_url}")
        try:
            bconf = json.loads(fetch_text(session, book_json_url))
        except Exception as e:
            print(f"  跳过 {key}（book.json 失败）: {e}", file=sys.stderr)
            continue

        units = bconf.get("units") or []
        if args.max_units:
            units = units[: args.max_units]

        book_entry: dict[str, Any] = {
            "key": key,
            "bookName": bconf.get("bookName"),
            "bookLevel": bconf.get("bookLevel"),
            "bookPath": base,
            "units": [],
        }

        for i, unit in enumerate(units):
            fn = unit.get("filename") or ""
            title = unit.get("title") or fn
            if not fn:
                continue
            lrc_url = f"{base}/{quote(fn, safe='')}.lrc"
            try:
                lrc_body = fetch_text(session, lrc_url)
                time.sleep(args.delay)
            except Exception as e:
                print(f"  警告: {title} LRC 失败: {e}", file=sys.stderr)
                continue

            parsed = parse_lrc(lrc_body)
            unit_slug = safe_filename(title)
            md_path = book_dir / f"{i+1:04d}_{unit_slug}.md"
            json_path = book_dir / f"{i+1:04d}_{unit_slug}.json"

            lines_en = [x["english"] for x in parsed if x["english"]]
            body_md = f"# {title}\n\n"
            body_md += f"**Book:** {bconf.get('bookName', '')} ({key})\n\n---\n\n"
            for row in parsed:
                if not row["english"] and not row["chinese"]:
                    continue
                body_md += f"{row['english']}\n"
                if row["chinese"]:
                    body_md += f"*{row['chinese']}*\n"
                body_md += "\n"

            md_path.write_text(body_md, encoding="utf-8")
            json_path.write_text(
                json.dumps(
                    {
                        "bookKey": key,
                        "title": title,
                        "filename": fn,
                        "lines": parsed,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            book_entry["units"].append(
                {
                    "title": title,
                    "filename": fn,
                    "markdown": str(md_path.relative_to(out_root)),
                    "json": str(json_path.relative_to(out_root)),
                    "lineCount": len(parsed),
                }
            )
            print(f"    [{i+1}/{len(units)}] {title}")

        (book_dir / "book_meta.json").write_text(
            json.dumps(book_entry, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        manifest["books"].append(book_entry)

    (out_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"完成。输出目录: {out_root.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

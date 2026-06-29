#!/usr/bin/env python
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config
from src.extractors.image_fetcher import ensure_content_image, is_valid_png, parse_content_image_src
from src.models import Article, SessionLocal, init_db


def iter_zheshang_html_files(data_dir: Path) -> list[Path]:
    return sorted(data_dir.rglob("浙商期货*.html"))


def main() -> None:
    parser = argparse.ArgumentParser(description="下载浙商期货 HTML 中缺失的正文 PNG")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    init_db()
    files = iter_zheshang_html_files(config.DATA_DIR)
    if args.limit:
        files = files[: args.limit]

    stats = {"checked": 0, "missing": 0, "downloaded": 0, "failed": 0}
    for html_path in files:
        stats["checked"] += 1
        html = html_path.read_text(encoding="utf-8", errors="ignore")
        image_src = parse_content_image_src(html)
        if not image_src:
            continue
        png_path = html_path.parent / image_src
        if is_valid_png(png_path):
            continue

        stats["missing"] += 1
        rel = html_path.relative_to(config.DATA_DIR)
        print(f"缺失: {rel} -> {image_src}")
        if args.dry_run:
            continue

        result = ensure_content_image(html_path, base_url=config.CNZSQH_BASE_URL)
        if result and is_valid_png(result):
            stats["downloaded"] += 1
            print(f"  已下载: {result}")
        else:
            stats["failed"] += 1
            print("  下载失败（官网可能已下线该文章，可配置 CNZSQH_BASE_URL 指向镜像）")

    print(
        f"完成: checked={stats['checked']}, missing={stats['missing']}, "
        f"downloaded={stats['downloaded']}, failed={stats['failed']}"
    )


if __name__ == "__main__":
    main()

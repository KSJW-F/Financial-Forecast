#!/usr/bin/env python
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.pipeline import import_data


def main() -> None:
    parser = argparse.ArgumentParser(description="导入并分析期货研报数据")
    parser.add_argument("--limit", type=int, default=None, help="最多处理文件数")
    parser.add_argument("--force", action="store_true", help="清空数据库后重新导入")
    parser.add_argument("--reprocess", action="store_true", help="更新已存在记录")
    args = parser.parse_args()

    stats = import_data(limit=args.limit, force=args.force, reprocess=args.reprocess)
    print(
        f"导入完成: processed={stats['processed']}, updated={stats['updated']}, "
        f"skipped={stats['skipped']}, failed={stats['failed']}"
    )


if __name__ == "__main__":
    main()

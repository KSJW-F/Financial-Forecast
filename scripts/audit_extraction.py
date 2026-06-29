#!/usr/bin/env python
"""批量扫描提取问题：按机构统计空壳/过短/未知，无需逐篇人工排查。"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config
from src.cleaner import clean_text
from src.diagnostics import classify_extraction_issue
from src.extractors import extract_article
from src.extractors.html_recovery import is_broken_html


def main() -> None:
    parser = argparse.ArgumentParser(description="扫描 data 目录提取质量")
    parser.add_argument("--broker", default="", help="仅扫描指定机构")
    parser.add_argument("--file-type", default="html", help="html/pdf/png")
    args = parser.parse_args()

    files = sorted(config.DATA_DIR.rglob(f"*.{args.file_type.lstrip('.')}"))
    if args.broker:
        files = [path for path in files if args.broker in path.name]

    issue_by_broker: dict[str, Counter] = defaultdict(Counter)
    samples: dict[str, list[str]] = defaultdict(list)

    for file_path in files:
        broker = file_path.name.split("_")[0]
        try:
            title, raw_content, file_type = extract_article(file_path)
            html_raw = file_path.read_text(encoding="utf-8", errors="ignore") if file_type == "html" else None
            cleaned = clean_text(raw_content) or clean_text(title)
            issue = classify_extraction_issue(file_path, file_type, title, cleaned, html_raw=html_raw)
            if issue:
                key = issue.split("：", 1)[0]
                issue_by_broker[broker][key] += 1
                if len(samples[broker]) < 3:
                    samples[broker].append(str(file_path.relative_to(config.DATA_DIR)))
            elif file_type == "html" and is_broken_html(html_raw or ""):
                issue_by_broker[broker]["采集失败"] += 1
        except Exception as exc:
            issue_by_broker[broker]["异常"] += 1
            if len(samples[broker]) < 3:
                samples[broker].append(f"{file_path.name}: {exc}")

    print(f"扫描 {len(files)} 个 {args.file_type} 文件\n")
    for broker in sorted(issue_by_broker, key=lambda name: sum(issue_by_broker[name].values()), reverse=True):
        counter = issue_by_broker[broker]
        total = sum(counter.values())
        print(f"[{broker}] 问题 {total} 篇")
        for issue, count in counter.most_common():
            print(f"  - {issue}: {count}")
        for sample in samples[broker]:
            print(f"    例: {sample}")
        print()


if __name__ == "__main__":
    main()

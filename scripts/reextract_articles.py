#!/usr/bin/env python
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config
from src.cleaner import clean_text
from src.diagnostics import classify_extraction_issue, classify_unknown_reason
from src.extractors import extract_article
from src.models import Article, Prediction, SessionLocal, init_db
from src.pipeline import analyze_content

def main() -> None:
    parser = argparse.ArgumentParser(description="重新提取正文并更新预测（PDF OCR / 图片 OCR）")
    parser.add_argument("--broker", default="", help="仅处理指定机构")
    parser.add_argument("--file-type", default="", help="仅处理指定格式：html / pdf / png")
    parser.add_argument("--unknown-only", action="store_true", help="仅重提取趋势仍为未知的文章")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--use-llm", action="store_true")
    args = parser.parse_args()

    init_db()
    session = SessionLocal()
    stats = {"updated": 0, "failed": 0}

    try:
        query = session.query(Article).order_by(Article.id.asc())
        if args.broker:
            query = query.filter(Article.broker.like(f"%{args.broker}%"))
        if args.file_type:
            query = query.filter(Article.file_type == args.file_type.lower())
        if args.unknown_only:
            query = query.join(Prediction).filter(Prediction.trend == "未知").distinct()
        if args.limit:
            query = query.limit(args.limit)
        articles = query.all()
        print(
            f"待处理 {len(articles)} 篇"
            + (f"（格式={args.file_type}）" if args.file_type else "")
            + ("（仅未知）" if args.unknown_only else "")
        )

        for article in articles:
            file_path = config.DATA_DIR / article.file_path
            if not file_path.exists():
                continue
            try:
                title, raw_content, file_type = extract_article(file_path)
                html_raw = None
                if file_type == "html":
                    from src.extractors.html_recovery import read_html_text

                    html_raw = read_html_text(file_path)
                cleaned_content = clean_text(raw_content) or clean_text(title)
                extraction_issue = classify_extraction_issue(
                    file_path,
                    file_type,
                    title,
                    cleaned_content,
                    html_raw=html_raw,
                )

                article.title = title
                article.file_type = file_type
                article.raw_content = raw_content
                article.cleaned_content = cleaned_content

                session.query(Prediction).filter_by(article_id=article.id).delete()
                for result in analyze_content(
                    title,
                    cleaned_content,
                    use_llm=args.use_llm,
                    file_path=file_path,
                    file_type=file_type,
                    extraction_issue=extraction_issue,
                ):
                    unknown_reason = ""
                    if result.trend == "未知":
                        unknown_reason = result.unknown_reason or classify_unknown_reason(
                            file_path,
                            file_type,
                            title,
                            cleaned_content,
                            extraction_issue=extraction_issue,
                        )
                    session.add(
                        Prediction(
                            article_id=article.id,
                            commodity=result.commodity,
                            trend=result.trend,
                            confidence=result.confidence,
                            source=result.source,
                            summary=result.summary,
                            unknown_reason=unknown_reason,
                        )
                    )
                stats["updated"] += 1
            except Exception as exc:
                stats["failed"] += 1
                print(f"失败 {article.file_path}: {exc}")

            if stats["updated"] % 100 == 0 and stats["updated"]:
                session.commit()
                print(f"进度: {stats['updated']}")

        session.commit()
    finally:
        session.close()

    print(f"完成: updated={stats['updated']}, failed={stats['failed']}")


if __name__ == "__main__":
    main()

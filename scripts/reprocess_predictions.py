#!/usr/bin/env python
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config
from src.diagnostics import classify_extraction_issue, classify_unknown_reason
from src.pipeline import analyze_content
from src.models import Article, Prediction, SessionLocal, init_db


def reprocess_predictions(
    limit: int | None = None,
    use_llm: bool = False,
    unknown_only: bool = True,
) -> dict:
    init_db()
    session = SessionLocal()
    stats = {"updated": 0, "articles": 0, "llm_used": 0, "unknown_before": 0, "identified": 0}

    try:
        query = session.query(Article).order_by(Article.id.asc())
        if limit:
            query = query.limit(limit)
        articles = query.all()
        stats["articles"] = len(articles)

        for article in articles:
            existing = (
                session.query(Prediction)
                .filter_by(article_id=article.id)
                .order_by(Prediction.id.asc())
                .first()
            )
            was_unknown = existing is None or existing.trend == "未知"
            if unknown_only and not was_unknown:
                continue

            if was_unknown:
                stats["unknown_before"] += 1

            file_path = config.DATA_DIR / article.file_path
            extraction_issue = classify_extraction_issue(
                file_path,
                article.file_type,
                article.title,
                article.cleaned_content,
            )
            need_llm = use_llm and config.LLM_ENABLED
            results = analyze_content(
                article.title,
                article.cleaned_content,
                use_llm=need_llm,
                file_path=file_path,
                file_type=article.file_type,
                extraction_issue=extraction_issue,
            )

            session.query(Prediction).filter_by(article_id=article.id).delete()
            for result in results:
                unknown_reason = ""
                if result.trend == "未知":
                    unknown_reason = result.unknown_reason or classify_unknown_reason(
                        file_path,
                        article.file_type,
                        article.title,
                        article.cleaned_content,
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
                if result.trend != "未知":
                    stats["identified"] += 1
                if result.source == "llm":
                    stats["llm_used"] += 1

            stats["updated"] += 1
            if stats["updated"] % 200 == 0:
                session.commit()
                print(
                    f"进度: {stats['updated']} | 新识别: {stats['identified']} | AI: {stats['llm_used']}"
                )

        session.commit()
    finally:
        session.close()

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="重新分析研报预测结果")
    parser.add_argument("--limit", type=int, default=None, help="最多处理条数")
    parser.add_argument("--use-llm", action="store_true", help="规则失败后调用 AI")
    parser.add_argument("--all", action="store_true", help="处理全部文章")
    args = parser.parse_args()

    result = reprocess_predictions(
        limit=args.limit,
        use_llm=args.use_llm,
        unknown_only=not args.all,
    )
    print(
        "重算完成: "
        f"updated={result['updated']}, "
        f"unknown_before={result['unknown_before']}, "
        f"identified={result['identified']}, "
        f"llm_used={result['llm_used']}"
    )


if __name__ == "__main__":
    main()

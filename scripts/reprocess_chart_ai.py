#!/usr/bin/env python
"""对未知样本：先重新提取正文，再规则/图表AI/普通AI 补全趋势。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config
from src.analyzer.chart_analyzer import needs_chart_ai
from src.cleaner import clean_text
from src.diagnostics import classify_extraction_issue, classify_unknown_reason
from src.extractors import extract_article
from src.models import Article, Prediction, SessionLocal, init_db
from src.pipeline import analyze_content


def main() -> None:
    parser = argparse.ArgumentParser(description="未知样本：重提取 + AI/规则补全")
    parser.add_argument("--broker", default="", help="仅处理指定机构，如 五矿期货")
    parser.add_argument("--limit", type=int, default=20, help="最多处理条数")
    parser.add_argument("--chart-only", action="store_true", help="仅处理图表型内容")
    parser.add_argument("--file", default="", help="仅处理文件名包含该关键字的文章")
    parser.add_argument("--no-llm", action="store_true", help="不调用 LLM，仅规则+启发式")
    args = parser.parse_args()

    use_llm = config.LLM_ENABLED and not args.no_llm
    if use_llm:
        print(f"提示：分层分析 = 强规则 → LLM({config.LLM_PROVIDER}) → 图表启发式")
    else:
        print("提示：当前不调用 LLM（规则 + 图表启发式）。去掉 --no-llm 且 LLM_ENABLED=true 可启用 AI")

    init_db()
    session = SessionLocal()
    stats = {
        "scanned": 0,
        "updated": 0,
        "identified": 0,
        "llm": 0,
        "empty_shell": 0,
        "reextracted": 0,
    }

    try:
        query = (
            session.query(Article)
            .join(Prediction)
            .filter(Prediction.trend == "未知")
            .order_by(Article.id.desc())
            .distinct()
        )
        if args.broker:
            query = query.filter(Article.broker.like(f"%{args.broker}%"))
        if args.file:
            query = query.filter(Article.file_path.like(f"%{args.file}%"))

        articles = query.limit(args.limit * 4).all()
        print(f"候选未知文章 {len(articles)} 篇，目标处理 {args.limit} 篇")

        for article in articles:
            if stats["updated"] >= args.limit:
                break

            file_path = config.DATA_DIR / article.file_path
            if not file_path.exists():
                continue

            try:
                title, raw_content, file_type = extract_article(file_path)
            except Exception as exc:
                print(f"提取失败 {article.file_path}: {exc}")
                continue

            cleaned = clean_text(raw_content) or clean_text(title)
            if cleaned and len(cleaned) > len(article.cleaned_content or ""):
                stats["reextracted"] += 1
            article.title = title or article.title
            article.raw_content = raw_content
            article.cleaned_content = cleaned
            article.file_type = file_type or article.file_type

            is_chart = needs_chart_ai(article.title, cleaned, file_type=article.file_type)
            if args.chart_only and not is_chart:
                continue

            stats["scanned"] += 1
            html_raw = None
            if file_type == "html" and file_path.exists():
                from src.extractors.html_recovery import read_html_text

                html_raw = read_html_text(file_path)
            extraction_issue = classify_extraction_issue(
                file_path,
                article.file_type,
                article.title,
                cleaned,
                html_raw=html_raw,
            )

            results = analyze_content(
                article.title,
                cleaned,
                use_llm=use_llm,
                file_path=file_path,
                file_type=article.file_type,
                extraction_issue=extraction_issue,
            )

            session.query(Prediction).filter_by(article_id=article.id).delete()
            identified_here = 0
            for result in results:
                unknown_reason = ""
                if result.trend == "未知":
                    unknown_reason = result.unknown_reason or classify_unknown_reason(
                        file_path,
                        article.file_type,
                        article.title,
                        cleaned,
                        extraction_issue=extraction_issue,
                    )
                    if "空壳" in unknown_reason or len(cleaned) < 30:
                        stats["empty_shell"] += 1
                else:
                    identified_here += 1
                if result.source == "llm":
                    stats["llm"] += 1
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
            stats["identified"] += identified_here
            if is_chart:
                tag = "图表"
            elif len(cleaned) < 40:
                tag = "空壳"
            else:
                tag = "正文"
            print(
                f"[{stats['updated']}/{args.limit}] {tag} "
                f"{article.file_path} len={len(cleaned)} -> 识别 {identified_here} 条"
            )
            session.commit()

        print(
            "完成: "
            f"scanned={stats['scanned']}, updated={stats['updated']}, "
            f"reextracted={stats['reextracted']}, identified={stats['identified']}, "
            f"llm={stats['llm']}, empty_shell={stats['empty_shell']}"
        )
    finally:
        session.close()


if __name__ == "__main__":
    main()

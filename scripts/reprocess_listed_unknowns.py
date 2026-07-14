"""重提取并分析指定文件/ID 的未知样本。"""
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
from src.extractors.html_recovery import read_html_text
from src.models import Article, Prediction, SessionLocal, init_db
from src.pipeline import analyze_content

DEFAULT_FILES = [
    r"20250422\华龙期货_330451_0.html",
    r"20250422\冠通期货_330449.PDF",
    r"20250422\光大期货_330649.PDF",
    r"20250422\倍特期货_330643_0.html",
    r"20250422\五矿期货_330426.PDF",
    r"20250422\中金财富_330470_0.html",
    r"20250422\中衍期货_330225_0.html",
    r"20250421\铜冠金源_329943.PDF",
    r"20250421\金元期货_329979.PDF",
    r"20250421\金元期货_329978.PDF",
    r"20250421\金元期货_329977.PDF",
    r"20250421\瑞达期货_330345_0.html",
    r"20250421\瑞达期货_330323_0.html",
    r"20250421\瑞达期货_330322_0.html",
    r"20250421\瑞达期货_330320_0.html",
    r"20250421\瑞达期货_330319_0.html",
    r"20250421\瑞达期货_330315_0.html",
    r"20250421\瑞达期货_330309_0.html",
    r"20250421\瑞达期货_330308_0.html",
    r"20250421\瑞达期货_329976_0.html",
    r"20250421\瑞达期货_329975_0.html",
    r"20250421\瑞达期货_329974_0.html",
    r"20250421\瑞达期货_329973_0.html",
    r"20250421\瑞达期货_329971_0.html",
    r"20250421\新湖期货_330197.PDF",
    r"20250421\新湖期货_330188.PDF",
    r"20250421\新湖期货_330181_0.html",
    r"20250421\广金期货_330165.PDF",
    r"20250421\大越期货_330083.PDF",
    r"20250421\国信期货_330277_0.html",
    r"20250421\国信期货_329885_0.html",
    r"20250421\国信期货_329879_0.html",
    r"20250421\冠通期货_330372.PDF",
    r"20250421\冠通期货_330293.PDF",
    r"20250421\冠通期货_329982.PDF",
    r"20250421\光大期货_330288.PDF",
    r"20250421\光大期货_330226.PDF",
    r"20250421\五矿期货_329921.PDF",
    r"20250421\五矿期货_329920.PDF",
    r"20250421\中金财富_330079_0.html",
    r"20250421\中金财富_330078_0.html",
    r"20250420\格林大华_329779.PDF",
    r"20250420\国联期货_329795.PDF",
    r"20250419\五矿期货_329759.PDF",
    r"20250419\五矿期货_329753.PDF",
    r"20250418\金元期货_329981.PDF",
    r"20250418\金元期货_329980.PDF",
    r"20250418\瑞达期货_329602.PDF",
    r"20250418\瑞达期货_329566_0.html",
    r"20250418\瑞达期货_329559_0.html",
    r"20250418\瑞达期货_329556_0.html",
    r"20250418\瑞达期货_329555_0.html",
    r"20250418\瑞达期货_329341_0.html",
    r"20250418\瑞达期货_329339_0.html",
    r"20250418\格林大华_329586.PDF",
    r"20250418\广金期货_329474.PDF",
    r"20250418\山金期货_329591.PDF",
    r"20250418\山金期货_329590.PDF",
    r"20250418\国信期货_329640.PDF",
    r"20250418\国信期货_329189_0.html",
    r"20250418\华龙期货_329532_0.html",
    r"20250418\冠通期货_329610.PDF",
    r"20250418\冠通期货_329593.PDF",
    r"20250418\冠通期货_329320.PDF",
    r"20250418\五矿期货_329251.PDF",
    r"20250418\五矿期货_329243.PDF",
    r"20250418\中金财富_329286_0.html",
    r"20250417\瑞达期货_329139_0.html",
    r"20250417\瑞达期货_329138_0.html",
    r"20250417\瑞达期货_329137_0.html",
    r"20250417\瑞达期货_329129_0.html",
    r"20250417\瑞达期货_329126_0.html",
    r"20250417\瑞达期货_328808_0.html",
    r"20250417\瑞达期货_328807_0.html",
    r"20250417\格林大华_329183.PDF",
    r"20250417\广金期货_329654.PDF",
    r"20250417\广金期货_328977.PDF",
    r"20250417\国信期货_329100_0.html",
    r"20250417\冠通期货_329169.PDF",
    r"20250417\冠通期货_329001.PDF",
    r"20250417\冠通期货_328879.PDF",
    r"20250417\中金财富_328887_0.html",
    r"20250416\瑞达期货_328772_0.html",
    r"20250416\瑞达期货_328771_0.html",
    r"20250416\瑞达期货_328770_0.html",
    r"20250416\瑞达期货_328756_0.html",
    r"20250416\瑞达期货_328754_0.html",
    r"20250416\广金期货_329044.PDF",
    r"20250416\冠通期货_328657.PDF",
    r"20250416\冠通期货_328646.PDF",
    r"20250415\瑞达期货_328304_0.html",
]

DEFAULT_IDS = [
    2867, 2815, 2790, 2782, 2772, 2768, 2760, 2759, 2748, 2696, 2692, 2684, 2683,
    2636, 2634, 2585, 2584, 2583, 2582, 2576, 2575, 2524, 2494, 2460, 2446, 2430,
    2421, 2420, 2387, 2383, 2379, 2372, 2366, 2365, 2364, 2351, 2257, 2232, 2022,
    2200, 2163, 2160, 2135, 2134, 2126, 2115, 2107, 2067, 2066, 2065, 2064, 2059,
    2058, 2057, 2049, 2048, 2047, 2027, 2015, 2014, 2013, 1990, 1989, 1976, 1944,
    1919, 1910, 1833, 1827, 1826, 1815, 1750, 1749, 1748,
]


def _collect_articles(session) -> list[Article]:
    seen: set[int] = set()
    articles: list[Article] = []
    for fp in DEFAULT_FILES:
        for candidate in (fp, fp.replace("\\", "/")):
            article = session.query(Article).filter(Article.file_path == candidate).first()
            if article and article.id not in seen:
                seen.add(article.id)
                articles.append(article)
                break
    for article_id in DEFAULT_IDS:
        article = session.get(Article, article_id)
        if article and article.id not in seen:
            seen.add(article.id)
            articles.append(article)
    return articles


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-llm", action="store_true", default=True)
    parser.add_argument("--unknown-only", action="store_true", help="仅仍为未知的样本")
    args = parser.parse_args()

    init_db()
    session = SessionLocal()
    stats = {"scanned": 0, "updated": 0, "identified": 0, "still_unknown": 0}

    try:
        articles = _collect_articles(session)
        print(f"目标文章 {len(articles)} 篇")
        for article in articles:
            if args.unknown_only:
                preds = session.query(Prediction).filter_by(article_id=article.id).all()
                if preds and any(p.trend != "未知" for p in preds):
                    continue

            file_path = config.DATA_DIR / article.file_path
            if not file_path.exists():
                print(f"缺失 {article.file_path}")
                continue

            stats["scanned"] += 1
            try:
                title, raw_content, file_type = extract_article(file_path)
            except Exception as exc:
                print(f"提取失败 {article.file_path}: {exc}")
                continue

            cleaned = clean_text(raw_content) or clean_text(title)
            article.title = title or article.title
            article.raw_content = raw_content
            article.cleaned_content = cleaned
            article.file_type = file_type or article.file_type

            html_raw = read_html_text(file_path) if file_type == "html" else None
            extraction_issue = classify_extraction_issue(
                file_path, article.file_type, article.title, cleaned, html_raw=html_raw
            )
            results = analyze_content(
                article.title,
                cleaned,
                use_llm=False,
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
                else:
                    identified_here += 1
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
            if identified_here == 0:
                stats["still_unknown"] += 1
            print(
                f"[{stats['updated']}] {article.file_path} len={len(cleaned)} "
                f"-> 识别 {identified_here} 条"
            )
            session.commit()

        print(
            "完成: "
            f"scanned={stats['scanned']}, updated={stats['updated']}, "
            f"identified={stats['identified']}, still_unknown={stats['still_unknown']}"
        )
    finally:
        session.close()


if __name__ == "__main__":
    main()

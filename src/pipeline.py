from __future__ import annotations

import logging
import re
import warnings
from pathlib import Path

import config
from src.analyzer.commodity import extract_commodity
from src.analyzer.rule_analyzer import AnalysisResult
from src.analyzer import analyze_with_llm, analyze_with_rules
from src.cleaner import clean_text
from src.diagnostics import classify_extraction_issue, classify_unknown_reason
from src.extractors import extract_article
from src.models import Article, Prediction, SessionLocal, init_db

logger = logging.getLogger(__name__)


def parse_file_metadata(file_path: Path) -> tuple[str, str, str]:
    rel_parts = file_path.relative_to(config.DATA_DIR).parts
    publish_date = "unknown"
    for part in rel_parts[:-1]:
        if re.fullmatch(r"\d{8}", part):
            publish_date = part

    stem = file_path.stem
    parts = stem.split("_")
    broker = parts[0] if parts else "未知机构"
    return broker, publish_date, stem


def parse_date_from_title(title: str) -> str | None:
    match = re.search(r"(20\d{6})", title)
    if match:
        return match.group(1)
    return None


def analyze_content(
    title: str,
    content: str,
    use_llm: bool | None = None,
    *,
    file_path: Path | None = None,
    file_type: str = "",
    extraction_issue: str | None = None,
) -> list[AnalysisResult]:
    llm_enabled = config.LLM_ENABLED if use_llm is None else use_llm
    results = analyze_with_rules(title, content)

    if results:
        for item in results:
            if item.commodity == "综合":
                item.commodity = extract_commodity(title, content)
        return results

    if llm_enabled:
        llm_results = analyze_with_llm(title, content)
        valid_llm = [item for item in llm_results if item.trend != "未知"]
        if valid_llm:
            return valid_llm
        retry_rules = analyze_with_rules(title, content)
        if retry_rules:
            return retry_rules

    unknown_reason = classify_unknown_reason(
        file_path or Path("."),
        file_type,
        title,
        content,
        extraction_issue=extraction_issue,
    )
    return [
        AnalysisResult(
            commodity=extract_commodity(title, content),
            trend="未知",
            confidence="低",
            source="rule",
            summary="未识别到明确趋势观点",
            unknown_reason=unknown_reason,
        )
    ]


def process_file(file_path: Path, session, reprocess: bool = False) -> Article | None:
    rel_path = str(file_path.relative_to(config.DATA_DIR))
    existing = session.query(Article).filter_by(file_path=rel_path).first()

    broker, publish_date, _ = parse_file_metadata(file_path)
    title, raw_content, file_type = extract_article(file_path)
    html_raw = None
    if file_type == "html" and file_path.exists():
        html_raw = file_path.read_text(encoding="utf-8", errors="ignore")
    extraction_issue = classify_extraction_issue(
        file_path,
        file_type,
        title,
        raw_content,
        html_raw=html_raw,
    )
    title_date = parse_date_from_title(title)
    if publish_date == "unknown" and title_date:
        publish_date = title_date

    cleaned_content = clean_text(raw_content)
    if not cleaned_content:
        cleaned_content = clean_text(title)

    if existing and not reprocess:
        return existing

    if existing and reprocess:
        session.query(Prediction).filter_by(article_id=existing.id).delete()
        existing.broker = broker
        existing.title = title
        existing.publish_date = publish_date
        existing.file_type = file_type
        existing.raw_content = raw_content
        existing.cleaned_content = cleaned_content
        article = existing
    else:
        article = Article(
            broker=broker,
            title=title,
            publish_date=publish_date,
            file_path=rel_path,
            file_type=file_type,
            raw_content=raw_content,
            cleaned_content=cleaned_content,
        )
        session.add(article)
        session.flush()

    for result in analyze_content(
        title,
        cleaned_content,
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
    return article


def import_data(
    limit: int | None = None,
    force: bool = False,
    reprocess: bool = False,
) -> dict:
    init_db()
    session = SessionLocal()
    stats = {"processed": 0, "skipped": 0, "failed": 0, "updated": 0}

    warnings.filterwarnings("ignore", message=".*FontBBox.*")

    try:
        if force:
            session.query(Prediction).delete()
            session.query(Article).delete()
            session.commit()

        files = sorted(
            p
            for p in config.DATA_DIR.rglob("*")
            if p.is_file() and p.suffix.lower() in {".html", ".htm", ".pdf", ".png", ".jpg", ".jpeg"}
        )
        if limit:
            files = files[:limit]

        for index, file_path in enumerate(files, start=1):
            try:
                existed = session.query(Article).filter_by(
                    file_path=str(file_path.relative_to(config.DATA_DIR))
                ).first()
                article = process_file(file_path, session, reprocess=force or reprocess)
                if article:
                    if existed and (force or reprocess):
                        stats["updated"] += 1
                    elif not existed:
                        stats["processed"] += 1
                    else:
                        stats["skipped"] += 1
            except Exception as exc:
                stats["failed"] += 1
                logger.debug("Failed to import %s: %s", file_path, exc)

            if index % 200 == 0:
                session.commit()
                print(f"进度: {index}/{len(files)}")

        session.commit()
    finally:
        session.close()

    return stats

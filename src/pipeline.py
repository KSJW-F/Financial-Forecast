from __future__ import annotations

import logging
import re
import warnings
from pathlib import Path

import config
from src.analyzer.chart_analyzer import needs_chart_ai
from src.analyzer.commodity import extract_commodity
from src.analyzer.llm_analyzer import analyze_chart_with_llm, analyze_with_llm
from src.analyzer.rule_analyzer import AnalysisResult, analyze_with_rules
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


def _normalize_rule_results(title: str, content: str, results: list[AnalysisResult]) -> list[AnalysisResult]:
    for item in results:
        if item.commodity == "综合":
            item.commodity = extract_commodity(title, content)
    return results


def _strong_results(results: list[AnalysisResult]) -> list[AnalysisResult]:
    """规则里置信度中/高的明确观点，可直接采用，不必再调 LLM。"""
    return [
        item
        for item in results
        if item.trend != "未知" and item.confidence in {"高", "中"} and item.source == "rule"
    ]


def _weak_only(results: list[AnalysisResult]) -> bool:
    """仅有低置信启发式结果时，开启 LLM 后应继续尝试 AI。"""
    if not results:
        return True
    return all(
        item.source == "chart-heuristic" or item.confidence == "低"
        for item in results
        if item.trend != "未知"
    )


def analyze_content(
    title: str,
    content: str,
    use_llm: bool | None = None,
    *,
    file_path: Path | None = None,
    file_type: str = "",
    extraction_issue: str | None = None,
) -> list[AnalysisResult]:
    """分层分析：强规则 →（可选）LLM → 图表启发式 → 未知。

    - 规则命中明确观点句（偏多/操作建议等）时直接返回，省钱省时。
    - 规则没有、或只有弱启发式时，再交给文华/OpenAI。
    - LLM 失败则回退图表启发式或未知。
    """
    llm_enabled = config.LLM_ENABLED if use_llm is None else use_llm
    only_unknown = getattr(config, "LLM_ONLY_UNKNOWN", True)

    rule_results = _normalize_rule_results(title, content, analyze_with_rules(title, content))
    strong = _strong_results(rule_results)

    # 强规则（明确观点句）始终优先，不浪费 LLM
    if strong:
        return strong

    chart_like = needs_chart_ai(title, content, file_type=file_type)
    chart_results: list[AnalysisResult] = []
    if chart_like:
        # 图表通道内部：LLM → 启发式
        chart_results = [
            item
            for item in analyze_chart_with_llm(title, content, use_llm=llm_enabled)
            if item.trend != "未知"
        ]
        llm_chart = [item for item in chart_results if item.source == "llm"]
        if llm_chart:
            return llm_chart
        # 无 LLM：直接用启发式；有 LLM 且结果偏弱：继续尝试正文 LLM 升级
        if chart_results and (not llm_enabled or not _weak_only(chart_results)):
            return chart_results

    # 规则/图表都没有强结论时，用 LLM 补全
    if llm_enabled:
        llm_results = [
            item for item in analyze_with_llm(title, content) if item.trend != "未知"
        ]
        if llm_results:
            return llm_results

    # LLM 不可用/失败：弱图表启发式 → 任意规则结果
    if chart_results:
        return chart_results
    if rule_results:
        return rule_results

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
        from src.extractors.html_recovery import read_html_text

        html_raw = read_html_text(file_path)
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

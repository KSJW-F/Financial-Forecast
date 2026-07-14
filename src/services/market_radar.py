"""客户端市场雷达：品种排名、按日热度、研报列表。"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.analyzer.commodity import is_valid_commodity
from src.insights import compute_consensus
from src.models import Article, Prediction


def stance_label_from_score(score: float, identified: int) -> str:
    if identified <= 0:
        return "数据不足"
    if score >= 0.85:
        return "积极偏多"
    if score >= 0.55:
        return "谨慎偏多"
    if score >= 0.12:
        return "震荡偏多"
    if score <= -0.85:
        return "积极偏空"
    if score <= -0.55:
        return "谨慎偏空"
    if score <= -0.12:
        return "震荡偏空"
    return "高抛低吸"


STANCE_TONE = {
    "积极偏多": "bull-strong",
    "谨慎偏多": "bull",
    "震荡偏多": "bull-soft",
    "高抛低吸": "neutral",
    "震荡偏空": "bear-soft",
    "谨慎偏空": "bear",
    "积极偏空": "bear-strong",
    "数据不足": "neutral",
}


@dataclass
class RadarItem:
    commodity: str
    weighted_score: float
    stance: str
    tone: str
    identified_reports: int
    momentum: str
    consensus_trend: str
    consensus_pct: float


@dataclass
class DailyStat:
    publish_date: str
    report_count: int
    dominant_trend: str


@dataclass
class ReportRow:
    article_id: int
    broker: str
    title: str
    publish_date: str
    trend: str
    summary: str
    source: str


def rank_commodities(
    session: Session,
    top_n: int | None = 10,
    date_from: str = "",
    date_to: str = "",
    *,
    bullish_only: bool = False,
) -> list[RadarItem]:
    """按加权评分排序。top_n=None 返回全部有效品种。"""
    names = [
        row[0]
        for row in session.query(Prediction.commodity)
        .filter(Prediction.trend != "未知")
        .distinct()
        .all()
    ]
    items: list[RadarItem] = []
    for name in names:
        if not is_valid_commodity(name):
            continue
        insight = compute_consensus(session, name, date_from, date_to)
        if not insight or insight.identified_reports <= 0:
            continue
        score = float(insight.weighted_score)
        if bullish_only and score < 0.08:
            continue
        stance = stance_label_from_score(score, insight.identified_reports)
        items.append(
            RadarItem(
                commodity=name,
                weighted_score=round(score, 2),
                stance=stance,
                tone=STANCE_TONE.get(stance, "neutral"),
                identified_reports=insight.identified_reports,
                momentum=insight.momentum,
                consensus_trend=insight.consensus_trend,
                consensus_pct=insight.consensus_pct,
            )
        )
    items.sort(key=lambda x: (-x.weighted_score, -x.identified_reports, x.commodity))
    if top_n is None or top_n <= 0:
        return items
    return items[:top_n]


def commodity_daily_stats(
    session: Session,
    commodity: str,
    date_from: str = "",
    date_to: str = "",
) -> list[DailyStat]:
    rows = (
        session.query(
            Article.publish_date,
            func.count(Prediction.id).label("cnt"),
        )
        .join(Prediction, Prediction.article_id == Article.id)
        .filter(Prediction.commodity == commodity)
        .filter(Prediction.trend != "未知")
    )
    if date_from:
        rows = rows.filter(Article.publish_date >= date_from)
    if date_to:
        rows = rows.filter(Article.publish_date <= date_to)
    rows = rows.group_by(Article.publish_date).order_by(Article.publish_date.desc()).all()

    stats: list[DailyStat] = []
    for publish_date, cnt in rows:
        trend_rows = (
            session.query(Prediction.trend, func.count(Prediction.id))
            .join(Article, Prediction.article_id == Article.id)
            .filter(Prediction.commodity == commodity)
            .filter(Article.publish_date == publish_date)
            .filter(Prediction.trend != "未知")
            .group_by(Prediction.trend)
            .all()
        )
        dominant = max(trend_rows, key=lambda x: x[1])[0] if trend_rows else "—"
        stats.append(
            DailyStat(
                publish_date=publish_date,
                report_count=int(cnt),
                dominant_trend=dominant,
            )
        )
    return stats


def commodity_reports(
    session: Session,
    commodity: str,
    day: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 40,
) -> list[ReportRow]:
    query = (
        session.query(Prediction, Article)
        .join(Article, Prediction.article_id == Article.id)
        .filter(Prediction.commodity == commodity)
        .filter(Prediction.trend != "未知")
        .order_by(Article.publish_date.desc(), Prediction.id.desc())
    )
    if day:
        query = query.filter(Article.publish_date == day)
    else:
        if date_from:
            query = query.filter(Article.publish_date >= date_from)
        if date_to:
            query = query.filter(Article.publish_date <= date_to)
    rows = query.limit(limit).all()
    return [
        ReportRow(
            article_id=article.id,
            broker=article.broker,
            title=article.title,
            publish_date=article.publish_date,
            trend=pred.trend,
            summary=(pred.summary or "")[:80],
            source=pred.source or "rule",
        )
        for pred, article in rows
    ]

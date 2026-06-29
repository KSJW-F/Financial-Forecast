from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from src.models import Article, Prediction

TREND_SCORE = {
    "看涨": 2,
    "偏多": 1,
    "震荡": 0,
    "中性": 0,
    "偏空": -1,
    "看跌": -2,
    "未知": 0,
}

BULLISH = {"看涨", "偏多"}
BEARISH = {"看跌", "偏空"}
NEUTRAL = {"震荡", "中性"}


@dataclass
class BrokerView:
    broker: str
    publish_date: str
    trend: str
    score: float
    summary: str
    source: str


@dataclass
class DailyTrendPoint:
    publish_date: str
    avg_score: float
    report_count: int
    dominant_trend: str


@dataclass
class ConsensusInsight:
    commodity: str
    date_from: str
    date_to: str
    total_reports: int
    identified_reports: int
    avg_score: float
    weighted_score: float
    consensus_trend: str
    consensus_pct: float
    distribution: dict[str, int]
    signal: str
    signal_level: str
    momentum: str
    momentum_detail: str
    persistence_days: int
    persistence_label: str
    recent_3d_score: float | None
    prior_score: float | None
    daily_series: list[DailyTrendPoint]
    broker_views: list[BrokerView]
    bullish_brokers: list[str]
    bearish_brokers: list[str]


def normalize_date_param(value: str) -> str:
    if not value:
        return ""
    compact = value.replace("-", "").strip()
    if len(compact) == 8 and compact.isdigit():
        return compact
    return ""


def date_for_input(yyyymmdd: str) -> str:
    if len(yyyymmdd) == 8 and yyyymmdd.isdigit():
        return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"
    return ""


def apply_date_filter(query, date_from: str, date_to: str, column):
    if date_from:
        query = query.filter(column >= date_from)
    if date_to:
        query = query.filter(column <= date_to)
    return query


def preset_date_range(preset: str) -> tuple[str, str]:
    today = datetime.now().date()
    if preset == "7d":
        start = today - timedelta(days=6)
        return start.strftime("%Y%m%d"), today.strftime("%Y%m%d")
    if preset == "14d":
        start = today - timedelta(days=13)
        return start.strftime("%Y%m%d"), today.strftime("%Y%m%d")
    if preset == "30d":
        start = today - timedelta(days=29)
        return start.strftime("%Y%m%d"), today.strftime("%Y%m%d")
    return "", ""


def compute_consensus(
    session: Session,
    commodity: str,
    date_from: str = "",
    date_to: str = "",
) -> ConsensusInsight | None:
    if not commodity:
        return None

    query = (
        session.query(
            Article.broker,
            Article.publish_date,
            Prediction.trend,
            Prediction.summary,
            Prediction.source,
        )
        .join(Prediction, Prediction.article_id == Article.id)
        .filter(Prediction.commodity == commodity)
        .filter(Prediction.trend != "未知")
        .filter(Article.publish_date != "unknown")
    )
    query = apply_date_filter(query, date_from, date_to, Article.publish_date)
    rows = query.order_by(Article.publish_date.desc(), Article.id.desc()).all()

    if not rows:
        return None

    distribution: dict[str, int] = {}
    broker_views: list[BrokerView] = []
    scores: list[float] = []
    seen: set[tuple[str, str]] = set()

    for broker, publish_date, trend, summary, source in rows:
        score = float(TREND_SCORE.get(trend, 0))
        scores.append(score)
        distribution[trend] = distribution.get(trend, 0) + 1

        key = (broker, publish_date)
        if key not in seen:
            seen.add(key)
            broker_views.append(
                BrokerView(
                    broker=broker,
                    publish_date=publish_date,
                    trend=trend,
                    score=score,
                    summary=summary or "",
                    source=source or "rule",
                )
            )

    avg_score = sum(scores) / len(scores) if scores else 0.0
    weighted_score = _weighted_score(rows)
    daily_series = _build_daily_series(rows)
    recent_3d_score, prior_score = _recent_vs_prior(daily_series)
    persistence_days, persistence_label = _trend_persistence(daily_series)
    top_trend = max(distribution, key=lambda key: distribution[key])
    consensus_pct = round(distribution[top_trend] / len(rows) * 100, 1)

    signal, signal_level = _build_signal(weighted_score, consensus_pct, distribution, persistence_days)
    momentum, momentum_detail = _build_momentum(rows, daily_series)

    bullish_brokers = sorted({view.broker for view in broker_views if view.trend in BULLISH})
    bearish_brokers = sorted({view.broker for view in broker_views if view.trend in BEARISH})

    total_query = (
        session.query(Article.id)
        .join(Prediction, Prediction.article_id == Article.id)
        .filter(Prediction.commodity == commodity)
        .filter(Article.publish_date != "unknown")
    )
    total_query = apply_date_filter(total_query, date_from, date_to, Article.publish_date)
    total_reports = total_query.count()

    return ConsensusInsight(
        commodity=commodity,
        date_from=date_from,
        date_to=date_to,
        total_reports=total_reports,
        identified_reports=len(rows),
        avg_score=round(avg_score, 2),
        weighted_score=round(weighted_score, 2),
        consensus_trend=top_trend,
        consensus_pct=consensus_pct,
        distribution=dict(sorted(distribution.items(), key=lambda item: -item[1])),
        signal=signal,
        signal_level=signal_level,
        momentum=momentum,
        momentum_detail=momentum_detail,
        persistence_days=persistence_days,
        persistence_label=persistence_label,
        recent_3d_score=recent_3d_score,
        prior_score=prior_score,
        daily_series=daily_series,
        broker_views=broker_views[:20],
        bullish_brokers=bullish_brokers,
        bearish_brokers=bearish_brokers,
    )


def _weighted_score(rows: list[tuple], half_life_days: float = 3.0) -> float:
    if not rows:
        return 0.0

    latest = max(datetime.strptime(row[1], "%Y%m%d") for row in rows if len(row[1]) == 8)
    weighted_sum = 0.0
    weight_total = 0.0
    for _, publish_date, trend, _, _ in rows:
        if len(publish_date) != 8:
            continue
        day = datetime.strptime(publish_date, "%Y%m%d")
        age_days = max((latest - day).days, 0)
        weight = 0.5 ** (age_days / half_life_days)
        weighted_sum += TREND_SCORE.get(trend, 0) * weight
        weight_total += weight
    return weighted_sum / weight_total if weight_total else 0.0


def _build_daily_series(rows: list[tuple]) -> list[DailyTrendPoint]:
    buckets: dict[str, list[float]] = {}
    trend_buckets: dict[str, list[str]] = {}
    for _, publish_date, trend, _, _ in rows:
        if len(publish_date) != 8:
            continue
        buckets.setdefault(publish_date, []).append(float(TREND_SCORE.get(trend, 0)))
        trend_buckets.setdefault(publish_date, []).append(trend)

    series: list[DailyTrendPoint] = []
    for publish_date in sorted(buckets):
        day_scores = buckets[publish_date]
        day_trends = trend_buckets[publish_date]
        dominant = max(set(day_trends), key=day_trends.count)
        series.append(
            DailyTrendPoint(
                publish_date=publish_date,
                avg_score=round(sum(day_scores) / len(day_scores), 2),
                report_count=len(day_scores),
                dominant_trend=dominant,
            )
        )
    return series


def _recent_vs_prior(daily_series: list[DailyTrendPoint]) -> tuple[float | None, float | None]:
    if len(daily_series) < 4:
        return None, None
    recent = daily_series[-3:]
    prior = daily_series[:-3]
    recent_score = sum(point.avg_score for point in recent) / len(recent)
    prior_score = sum(point.avg_score for point in prior) / len(prior)
    return round(recent_score, 2), round(prior_score, 2)


def _trend_persistence(daily_series: list[DailyTrendPoint]) -> tuple[int, str]:
    if not daily_series:
        return 0, "暂无连续趋势"

    streak = 1
    last_sign = _score_sign(daily_series[-1].avg_score)
    for point in reversed(daily_series[:-1]):
        sign = _score_sign(point.avg_score)
        if sign == 0 or sign != last_sign:
            break
        streak += 1

    if last_sign > 0:
        return streak, f"近 {streak} 个交易日评分持续偏多"
    if last_sign < 0:
        return streak, f"近 {streak} 个交易日评分持续偏空"
    return streak, f"近 {streak} 个交易日评分持续中性/震荡"


def _score_sign(score: float) -> int:
    if score >= 0.35:
        return 1
    if score <= -0.35:
        return -1
    return 0


def _build_signal(
    avg_score: float,
    consensus_pct: float,
    distribution: dict[str, int],
    persistence_days: int = 0,
) -> tuple[str, str]:
    bullish_count = sum(distribution.get(key, 0) for key in BULLISH)
    bearish_count = sum(distribution.get(key, 0) for key in BEARISH)
    total = sum(distribution.values())

    persistence_hint = f"且近 {persistence_days} 日方向延续" if persistence_days >= 3 else ""

    if avg_score >= 0.75 and consensus_pct >= 45:
        return (
            f"时序加权共识偏多（近期权重更高），{bullish_count}/{total} 家偏多/看涨{persistence_hint}，可考虑逢低布局。",
            "success",
        )
    if avg_score <= -0.75 and consensus_pct >= 45:
        return (
            f"时序加权共识偏空（近期权重更高），{bearish_count}/{total} 家偏空/看跌{persistence_hint}，注意回调风险。",
            "danger",
        )
    if abs(avg_score) <= 0.25 and total >= 3:
        return (
            f"机构观点以震荡/中性为主，建议观望，等待方向明朗后再决策。",
            "warning",
        )
    return (
        f"机构分歧较大（最高共识仅 {consensus_pct}%），建议缩小仓位、分批观察，勿单一研报定方向。",
        "secondary",
    )


def _build_momentum(rows: list[tuple], daily_series: list[DailyTrendPoint]) -> tuple[str, str]:
    if len(daily_series) >= 4:
        recent = daily_series[-3:]
        prior = daily_series[-6:-3] if len(daily_series) >= 6 else daily_series[:-3]
        if prior:
            recent_avg = sum(point.avg_score for point in recent) / len(recent)
            prior_avg = sum(point.avg_score for point in prior) / len(prior)
            delta = recent_avg - prior_avg
            if delta >= 0.35:
                return "升温", f"近3日均值 {recent_avg:.2f}，较前段 {prior_avg:.2f} 抬升"
            if delta <= -0.35:
                return "降温", f"近3日均值 {recent_avg:.2f}，较前段 {prior_avg:.2f} 回落"

    if len(rows) < 4:
        return "平稳", "样本较少，暂无法判断动量变化"

    ordered = sorted(rows, key=lambda row: row[1])
    mid = len(ordered) // 2
    early = ordered[:mid]
    late = ordered[mid:]

    early_avg = sum(TREND_SCORE.get(row[2], 0) for row in early) / len(early)
    late_avg = sum(TREND_SCORE.get(row[2], 0) for row in late) / len(late)
    delta = late_avg - early_avg

    if delta >= 0.5:
        return "升温", f"后半段评分 {late_avg:.2f}，较前半段 {early_avg:.2f} 明显偏多"
    if delta <= -0.5:
        return "降温", f"后半段评分 {late_avg:.2f}，较前半段 {early_avg:.2f} 明显偏空"
    return "平稳", f"前后半段评分接近（{early_avg:.2f} → {late_avg:.2f}）"

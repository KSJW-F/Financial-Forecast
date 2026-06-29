from __future__ import annotations

from datetime import datetime

import pandas as pd
import plotly
import plotly.express as px
from flask import Flask, abort, render_template, request
from sqlalchemy import func

import config
from src.analyzer.commodity import CANONICAL_COMMODITIES, is_valid_commodity
from src.insights import (
    compute_consensus,
    date_for_input,
    normalize_date_param,
    preset_date_range,
)
from src.models import Article, Prediction, SessionLocal, init_db

app = Flask(__name__)
app.secret_key = config.FLASK_SECRET_KEY

TREND_SCORE = {
    "看涨": 2,
    "偏多": 1,
    "震荡": 0,
    "中性": 0,
    "偏空": -1,
    "看跌": -2,
    "未知": 0,
}

TREND_BADGE = {
    "看涨": "success",
    "偏多": "success",
    "震荡": "warning",
    "中性": "secondary",
    "偏空": "danger",
    "看跌": "danger",
    "未知": "secondary",
}


@app.template_filter("format_date")
def format_date(value: str) -> str:
    if not value or value == "unknown":
        return "—"
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value


def get_session():
    return SessionLocal()


def build_page_range(page: int, total_pages: int, window: int = 2) -> list[int | str]:
    if total_pages <= 1:
        return [1]

    pages: list[int | str] = []
    pages.append(1)

    start = max(2, page - window)
    end = min(total_pages - 1, page + window)

    if start > 2:
        pages.append("...")
    for number in range(start, end + 1):
        pages.append(number)
    if end < total_pages - 1:
        pages.append("...")

    if total_pages > 1:
        pages.append(total_pages)
    return pages


def get_top_commodities(session, limit: int = 30) -> list[str]:
    rows = (
        session.query(Prediction.commodity, func.count(Prediction.id).label("cnt"))
        .filter(Prediction.commodity != "综合")
        .group_by(Prediction.commodity)
        .order_by(func.count(Prediction.id).desc())
        .all()
    )
    commodities = [row[0] for row in rows if is_valid_commodity(row[0])]
    if not commodities:
        commodities = sorted(CANONICAL_COMMODITIES)
    return commodities[:limit]


def get_filter_commodities(session) -> list[str]:
    rows = [row[0] for row in session.query(Prediction.commodity).distinct().all()]
    valid = sorted({name for name in rows if is_valid_commodity(name)})
    return valid


def parse_date_filters() -> dict[str, str]:
    preset = request.args.get("preset", "")
    date_from = normalize_date_param(request.args.get("date_from", ""))
    date_to = normalize_date_param(request.args.get("date_to", ""))
    if preset in {"7d", "14d", "30d"}:
        date_from, date_to = preset_date_range(preset)
    return {
        "date_from": date_from,
        "date_to": date_to,
        "date_from_input": date_for_input(date_from),
        "date_to_input": date_for_input(date_to),
        "preset": preset,
    }


def apply_article_date_filter(query, date_from: str, date_to: str):
    if date_from:
        query = query.filter(Article.publish_date >= date_from)
    if date_to:
        query = query.filter(Article.publish_date <= date_to)
    return query


def build_query_string(filters: dict, page: int | None = None) -> str:
    parts: list[str] = []
    for key in ("broker", "commodity", "trend", "date_from", "date_to", "preset"):
        value = filters.get(key, "")
        if value:
            display = date_for_input(value) if key in {"date_from", "date_to"} else value
            parts.append(f"{key}={display}")
    if page and page > 1:
        parts.append(f"page={page}")
    return "&".join(parts)


@app.route("/")
def index():
    session = get_session()
    try:
        broker = request.args.get("broker", "")
        commodity = request.args.get("commodity", "")
        trend = request.args.get("trend", "")
        date_filters = parse_date_filters()
        page = max(int(request.args.get("page", 1)), 1)
        page_size = 20

        query = session.query(Article).order_by(Article.publish_date.desc(), Article.id.desc())
        query = apply_article_date_filter(query, date_filters["date_from"], date_filters["date_to"])
        if broker:
            query = query.filter(Article.broker.like(f"%{broker}%"))

        if commodity or trend:
            query = query.join(Prediction)
            if commodity:
                query = query.filter(Prediction.commodity.like(f"%{commodity}%"))
            if trend:
                query = query.filter(Prediction.trend == trend)
            query = query.distinct()

        total = query.count()
        total_pages = max((total + page_size - 1) // page_size, 1)
        page = min(page, total_pages)
        articles = query.offset((page - 1) * page_size).limit(page_size).all()

        rows = []
        for article in articles:
            prediction = (
                session.query(Prediction)
                .filter_by(article_id=article.id)
                .order_by(Prediction.id.asc())
                .first()
            )
            trend_value = prediction.trend if prediction else "未知"
            rows.append(
                {
                    "id": article.id,
                    "broker": article.broker,
                    "title": article.title,
                    "publish_date": article.publish_date,
                    "file_path": article.file_path,
                    "commodity": prediction.commodity if prediction else "-",
                    "trend": trend_value,
                    "trend_badge": TREND_BADGE.get(trend_value, "secondary"),
                    "source": prediction.source if prediction else "-",
                    "summary": prediction.summary if prediction else "",
                    "unknown_reason": prediction.unknown_reason if prediction else "",
                }
            )

        brokers = [row[0] for row in session.query(Article.broker).distinct().all()]
        commodities = get_filter_commodities(session)
        trends = [row[0] for row in session.query(Prediction.trend).distinct().all()]

        total_articles = session.query(func.count(Article.id)).scalar() or 0
        unknown_count = (
            session.query(func.count(Prediction.id)).filter(Prediction.trend == "未知").scalar() or 0
        )
        identified_count = (
            session.query(func.count(Prediction.id)).filter(Prediction.trend != "未知").scalar() or 0
        )
        llm_count = (
            session.query(func.count(Prediction.id)).filter(Prediction.source == "llm").scalar() or 0
        )

        return render_template(
            "index.html",
            rows=rows,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            page_range=build_page_range(page, total_pages),
            brokers=sorted(brokers),
            commodities=sorted(commodities),
            trends=sorted(trends),
            filters={
                "broker": broker,
                "commodity": commodity,
                "trend": trend,
                **date_filters,
            },
            query_string=build_query_string(
                {
                    "broker": broker,
                    "commodity": commodity,
                    "trend": trend,
                    "date_from": date_filters["date_from"],
                    "date_to": date_filters["date_to"],
                    "preset": date_filters["preset"],
                }
            ),
            stats={
                "articles": total_articles,
                "identified": identified_count,
                "unknown": unknown_count,
                "llm": llm_count,
            },
        )
    finally:
        session.close()


@app.route("/article/<int:article_id>")
def article_detail(article_id: int):
    session = get_session()
    try:
        article = session.get(Article, article_id)
        if not article:
            abort(404)
        predictions = session.query(Prediction).filter_by(article_id=article.id).all()
        for item in predictions:
            item.trend_badge = TREND_BADGE.get(item.trend, "secondary")
        return render_template("detail.html", article=article, predictions=predictions)
    finally:
        session.close()


@app.route("/charts")
def charts():
    session = get_session()
    try:
        commodities = get_top_commodities(session)
        commodity = request.args.get("commodity") or (commodities[0] if commodities else "")
        date_filters = parse_date_filters()

        records = []
        if commodity:
            query = (
                session.query(
                    Article.publish_date,
                    Prediction.trend,
                    Prediction.commodity,
                    Article.broker,
                )
                .join(Prediction, Prediction.article_id == Article.id)
                .filter(Prediction.commodity == commodity)
                .filter(Article.publish_date != "unknown")
            )
            query = apply_article_date_filter(
                query, date_filters["date_from"], date_filters["date_to"]
            )
            records = query.order_by(Article.publish_date.asc()).all()

        trend_chart = ""
        distribution_chart = ""
        table_rows = []

        if records:
            df = pd.DataFrame(records, columns=["publish_date", "trend", "commodity", "broker"])
            df["score"] = df["trend"].map(TREND_SCORE).fillna(0)
            df["date"] = pd.to_datetime(df["publish_date"], format="%Y%m%d", errors="coerce")
            df = df.dropna(subset=["date"])

            if not df.empty:
                chart_df = (
                    df.groupby(["date", "broker"], as_index=False)
                    .agg(score=("score", "mean"))
                    .sort_values("date")
                )

                trend_fig = px.line(
                    chart_df,
                    x="date",
                    y="score",
                    color="broker",
                    markers=True,
                    title=f"{commodity} 品种趋势评分走势（同机构同日取均值）",
                    labels={"date": "日期", "score": "趋势评分", "broker": "期货公司"},
                )
                trend_fig.update_layout(template="plotly_white", height=420)

                distribution_fig = px.bar(
                    df.groupby("trend", as_index=False).size(),
                    x="trend",
                    y="size",
                    color="trend",
                    title=f"{commodity} 趋势分布",
                    labels={"trend": "趋势判断", "size": "数量"},
                )
                distribution_fig.update_layout(template="plotly_white", showlegend=False, height=320)

                trend_chart = plotly.io.to_html(trend_fig, full_html=False, include_plotlyjs="cdn")
                distribution_chart = plotly.io.to_html(
                    distribution_fig, full_html=False, include_plotlyjs=False
                )
                table_rows = chart_df.tail(30).to_dict("records")

        return render_template(
            "charts.html",
            commodity=commodity,
            commodities=commodities,
            trend_chart=trend_chart,
            distribution_chart=distribution_chart,
            table_rows=table_rows,
            has_data=bool(table_rows),
            filters=date_filters,
            query_string=build_query_string(
                {
                    "commodity": commodity,
                    "date_from": date_filters["date_from"],
                    "date_to": date_filters["date_to"],
                    "preset": date_filters["preset"],
                }
            ),
        )
    finally:
        session.close()


@app.route("/insights")
def insights():
    session = get_session()
    try:
        commodities = get_filter_commodities(session)
        commodity = request.args.get("commodity") or (commodities[0] if commodities else "")
        date_filters = parse_date_filters()

        insight = None
        distribution_chart = ""
        temporal_chart = ""
        if commodity:
            insight = compute_consensus(
                session,
                commodity,
                date_filters["date_from"],
                date_filters["date_to"],
            )
            if insight and insight.distribution:
                dist_df = pd.DataFrame(
                    [{"trend": key, "count": value} for key, value in insight.distribution.items()]
                )
                dist_fig = px.bar(
                    dist_df,
                    x="trend",
                    y="count",
                    color="trend",
                    title=f"{commodity} 机构观点分布",
                    labels={"trend": "趋势", "count": "篇数"},
                )
                dist_fig.update_layout(template="plotly_white", showlegend=False, height=300)
                if not temporal_chart:
                    distribution_chart = plotly.io.to_html(
                        dist_fig, full_html=False, include_plotlyjs="cdn"
                    )
                else:
                    distribution_chart = plotly.io.to_html(
                        dist_fig, full_html=False, include_plotlyjs=False
                    )

            if insight and insight.daily_series:
                series_df = pd.DataFrame(
                    [
                        {
                            "date": pd.to_datetime(point.publish_date, format="%Y%m%d"),
                            "score": point.avg_score,
                        }
                        for point in insight.daily_series
                    ]
                )
                temporal_fig = px.line(
                    series_df,
                    x="date",
                    y="score",
                    markers=True,
                    title=f"{commodity} 日度共识评分（时序）",
                    labels={"date": "日期", "score": "日均评分"},
                )
                temporal_fig.add_hline(y=0, line_dash="dot", line_color="#999")
                temporal_fig.update_layout(template="plotly_white", height=320)
                temporal_chart = plotly.io.to_html(
                    temporal_fig, full_html=False, include_plotlyjs="cdn"
                )
                if distribution_chart:
                    distribution_chart = plotly.io.to_html(
                        dist_fig, full_html=False, include_plotlyjs=False
                    )

        return render_template(
            "insights.html",
            commodity=commodity,
            commodities=commodities,
            insight=insight,
            distribution_chart=distribution_chart,
            temporal_chart=temporal_chart,
            filters=date_filters,
            trend_badge=TREND_BADGE,
        )
    finally:
        session.close()


@app.route("/api/stats")
def api_stats():
    session = get_session()
    try:
        article_count = session.query(func.count(Article.id)).scalar() or 0
        prediction_count = session.query(func.count(Prediction.id)).scalar() or 0
        trend_stats = (
            session.query(Prediction.trend, func.count(Prediction.id))
            .group_by(Prediction.trend)
            .all()
        )
        return {
            "articles": article_count,
            "predictions": prediction_count,
            "trends": {trend: count for trend, count in trend_stats},
            "generated_at": datetime.utcnow().isoformat(),
        }
    finally:
        session.close()


if __name__ == "__main__":
    init_db()
    app.run(debug=config.FLASK_DEBUG, host="0.0.0.0", port=5000)

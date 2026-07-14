from __future__ import annotations

from datetime import datetime

import pandas as pd
import plotly
import plotly.express as px
from flask import Flask, abort, jsonify, redirect, render_template, request, url_for
from sqlalchemy import func

import config
from src.analyzer.commodity import CANONICAL_COMMODITIES, is_valid_commodity
from src.insights import (
    compute_consensus,
    date_for_input,
    normalize_date_param,
    parse_anchor_date,
    preset_date_range,
)
from src.models import Article, Prediction, SessionLocal, init_db
from src.services.advisor_chat import ask_advisor
from src.services.auth import (
    ROLE_CLIENT,
    ROLE_ENTERPRISE,
    authenticate,
    current_user,
    home_for_role,
    login_required,
    login_user,
    logout_user,
    maybe_record_page_view,
    role_required,
)
from src.services.market_radar import (
    STANCE_TONE,
    commodity_daily_stats,
    commodity_reports,
    rank_commodities,
    stance_label_from_score,
)
from src.services.ops_stats import compute_ops_stats
from src.services.upload import save_and_process_upload

app = Flask(__name__)
app.secret_key = config.FLASK_SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024
app.config["TEMPLATES_AUTO_RELOAD"] = True


@app.context_processor
def inject_auth():
    user = current_user()
    return {
        "current_user": user,
        "is_enterprise": bool(user and user.role == ROLE_ENTERPRISE),
        "is_client": bool(user and user.role == ROLE_CLIENT),
    }


@app.before_request
def _track_page_view():
    if request.endpoint in (None, "static", "landing", "login", "logout"):
        return
    if request.path.startswith("/static"):
        return
    user = current_user()
    if user:
        maybe_record_page_view(user, request.path)

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
    "中性": "info",
    "偏空": "danger",
    "看跌": "danger",
    "未知": "secondary",
}

SCORE_BADGE = {
    2: "success",
    1: "success",
    0: "info",
    -1: "danger",
    -2: "danger",
}


def score_badge(score) -> str:
    try:
        key = int(round(float(score)))
    except (TypeError, ValueError):
        return "secondary"
    if key > 0:
        return "success"
    if key < 0:
        return "danger"
    return "info"


def score_label(score) -> str:
    try:
        value = float(score)
    except (TypeError, ValueError):
        return "—"
    if value >= 1.5:
        return "看涨"
    if value >= 0.5:
        return "偏多"
    if value <= -1.5:
        return "看跌"
    if value <= -0.5:
        return "偏空"
    if abs(value) < 0.05:
        return "中性/震荡"
    return "偏中性"


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
        .filter(Prediction.commodity != "商品")
        .filter(Prediction.trend != "未知")
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


def latest_publish_anchor(session) -> str:
    """库内最新有效研报日期（YYYYMMDD），供快捷区间对齐数据。"""
    value = (
        session.query(func.max(Article.publish_date))
        .filter(Article.publish_date.isnot(None))
        .filter(Article.publish_date != "")
        .filter(Article.publish_date != "unknown")
        .scalar()
    )
    return value if value and len(str(value)) == 8 and str(value).isdigit() else ""


def parse_date_filters(session=None) -> dict[str, str]:
    preset = request.args.get("preset", "")
    date_from = normalize_date_param(request.args.get("date_from", ""))
    date_to = normalize_date_param(request.args.get("date_to", ""))
    data_anchor = latest_publish_anchor(session) if session is not None else ""
    if preset in {"7d", "14d", "30d"}:
        date_from, date_to = preset_date_range(preset, parse_anchor_date(data_anchor))
    return {
        "date_from": date_from,
        "date_to": date_to,
        "date_from_input": date_for_input(date_from),
        "date_to_input": date_for_input(date_to),
        "preset": preset,
        "data_anchor": data_anchor,
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


def _platform_stats(session) -> dict:
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
    broker_count = session.query(func.count(func.distinct(Article.broker))).scalar() or 0
    return {
        "articles": total_articles,
        "identified": identified_count,
        "unknown": unknown_count,
        "llm": llm_count,
        "brokers": broker_count,
    }


@app.route("/")
def landing():
    user = current_user()
    if user:
        return redirect(home_for_role(user.role))
    session = get_session()
    try:
        stats = _platform_stats(session)
        return render_template(
            "landing.html",
            stats=stats,
            next_url=request.args.get("next", ""),
            error=request.args.get("error", ""),
            portal=request.args.get("portal", "enterprise"),
        )
    finally:
        session.close()


@app.route("/login", methods=["POST"])
def login():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    portal = (request.form.get("portal") or "client").strip()
    next_url = request.form.get("next") or request.args.get("next") or ""
    user = authenticate(username, password)
    if not user:
        return redirect(
            url_for("landing", error="账号或密码错误", portal=portal, next=next_url)
        )
    login_user(user)
    if next_url.startswith("/") and not next_url.startswith("//"):
        return redirect(next_url)
    return redirect(home_for_role(user.role))


@app.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("landing"))


@app.route("/enterprise")
@role_required(ROLE_ENTERPRISE)
def enterprise_home():
    session = get_session()
    try:
        stats = _platform_stats(session)
        ops = compute_ops_stats(session)
        recent = (
            session.query(Article)
            .order_by(Article.publish_date.desc(), Article.id.desc())
            .limit(6)
            .all()
        )
        recent_rows = []
        for article in recent:
            prediction = (
                session.query(Prediction)
                .filter_by(article_id=article.id)
                .order_by(Prediction.id.asc())
                .first()
            )
            trend_value = prediction.trend if prediction else "未知"
            recent_rows.append(
                {
                    "id": article.id,
                    "broker": article.broker,
                    "title": article.title,
                    "publish_date": article.publish_date,
                    "commodity": prediction.commodity if prediction else "-",
                    "trend": trend_value,
                    "trend_badge": TREND_BADGE.get(trend_value, "secondary"),
                }
            )
        return render_template(
            "enterprise_home.html",
            stats=stats,
            ops=ops,
            recent_rows=recent_rows,
        )
    finally:
        session.close()


@app.route("/market")
@role_required(ROLE_CLIENT)
def market_home():
    session = get_session()
    try:
        date_filters = parse_date_filters(session)
        top_raw = (request.args.get("top") or "all").strip().lower()
        if top_raw in {"all", "0", "全部"}:
            top_n = None
            top_mode = "all"
        else:
            try:
                top_n = int(top_raw)
            except ValueError:
                top_n = None
                top_mode = "all"
            else:
                if top_n <= 5:
                    top_n = 5
                    top_mode = "5"
                elif top_n <= 10:
                    top_n = 10
                    top_mode = "10"
                else:
                    top_n = None
                    top_mode = "all"
        radar = rank_commodities(
            session,
            top_n=top_n,
            date_from=date_filters["date_from"],
            date_to=date_filters["date_to"],
            bullish_only=False,
        )
        commodities = get_filter_commodities(session)
        return render_template(
            "market_home.html",
            radar=radar,
            top_n=top_n,
            top_mode=top_mode,
            radar_total=len(radar) if top_n is None else None,
            commodities=commodities,
            filters=date_filters,
            stance_tone=STANCE_TONE,
        )
    finally:
        session.close()


@app.route("/market/commodity")
@role_required(ROLE_CLIENT)
def market_commodity():
    session = get_session()
    try:
        commodities = get_filter_commodities(session)
        commodity = request.args.get("commodity") or (commodities[0] if commodities else "")
        date_filters = parse_date_filters(session)
        day = normalize_date_param(request.args.get("day", ""))
        insight = None
        daily = []
        reports = []
        stance = "数据不足"
        if commodity:
            insight = compute_consensus(
                session,
                commodity,
                date_filters["date_from"],
                date_filters["date_to"],
            )
            if insight:
                stance = stance_label_from_score(
                    float(insight.weighted_score), insight.identified_reports
                )
            daily = commodity_daily_stats(
                session,
                commodity,
                date_filters["date_from"],
                date_filters["date_to"],
            )
            max_count = max((d.report_count for d in daily), default=1)
            daily_view = [
                {
                    "publish_date": d.publish_date,
                    "report_count": d.report_count,
                    "dominant_trend": d.dominant_trend,
                    "trend_badge": TREND_BADGE.get(d.dominant_trend, "secondary"),
                    "heat_pct": int(round(100 * d.report_count / max_count)),
                }
                for d in daily[:21]
            ]
            reports_raw = commodity_reports(
                session,
                commodity,
                day=day,
                date_from=date_filters["date_from"],
                date_to=date_filters["date_to"],
            )
            reports = [
                {
                    "article_id": r.article_id,
                    "broker": r.broker,
                    "title": r.title,
                    "publish_date": r.publish_date,
                    "trend": r.trend,
                    "summary": r.summary,
                    "source": r.source,
                    "trend_badge": TREND_BADGE.get(r.trend, "secondary"),
                }
                for r in reports_raw
            ]
        else:
            daily_view = []
        return render_template(
            "market_commodity.html",
            commodity=commodity,
            commodities=commodities,
            insight=insight,
            stance=stance,
            stance_tone=STANCE_TONE.get(stance, "neutral"),
            daily=daily_view,
            reports=reports,
            day=day,
            filters=date_filters,
            trend_badge=TREND_BADGE,
        )
    finally:
        session.close()


@app.route("/reports")
@role_required(ROLE_ENTERPRISE)
def index():
    session = get_session()
    try:
        broker = request.args.get("broker", "")
        commodity = request.args.get("commodity", "")
        trend = request.args.get("trend", "")
        date_filters = parse_date_filters(session)
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
        stats = _platform_stats(session)

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
            stats=stats,
        )
    finally:
        session.close()


@app.route("/upload", methods=["GET", "POST"])
@role_required(ROLE_ENTERPRISE)
def upload_page():
    if request.method == "GET":
        return render_template("upload.html")

    result = save_and_process_upload(
        request.files.get("file"),
        broker=request.form.get("broker", ""),
        publish_date=request.form.get("publish_date", ""),
    )
    if request.accept_mimetypes.best == "application/json" or request.headers.get(
        "X-Requested-With"
    ) == "XMLHttpRequest":
        status = 200 if result.ok else 400
        return jsonify(
            {
                "ok": result.ok,
                "message": result.message,
                "article_id": result.article_id,
                "title": result.title,
                "broker": result.broker,
                "file_path": result.file_path,
                "predictions": result.predictions or [],
                "detail_url": (
                    f"/article/{result.article_id}" if result.article_id else ""
                ),
            }
        ), status

    return render_template("upload.html", result=result)


@app.route("/api/upload", methods=["POST"])
@role_required(ROLE_ENTERPRISE)
def api_upload():
    result = save_and_process_upload(
        request.files.get("file"),
        broker=request.form.get("broker", ""),
        publish_date=request.form.get("publish_date", ""),
    )
    status = 200 if result.ok else 400
    return jsonify(
        {
            "ok": result.ok,
            "message": result.message,
            "article_id": result.article_id,
            "title": result.title,
            "broker": result.broker,
            "file_path": result.file_path,
            "predictions": result.predictions or [],
            "detail_url": f"/article/{result.article_id}" if result.article_id else "",
        }
    ), status


@app.route("/advisor")
@login_required
def advisor_page():
    session = get_session()
    try:
        commodities = get_filter_commodities(session)
        commodity = request.args.get("commodity", "")
        return render_template(
            "advisor.html",
            commodities=commodities,
            commodity=commodity,
            llm_enabled=config.LLM_ENABLED,
            llm_provider=config.LLM_PROVIDER,
        )
    finally:
        session.close()


@app.route("/api/advisor/chat", methods=["POST"])
@login_required
def api_advisor_chat():
    payload = request.get_json(silent=True) or {}
    question = payload.get("question") or request.form.get("question", "")
    commodity = payload.get("commodity") or request.form.get("commodity", "")
    date_from = normalize_date_param(
        payload.get("date_from") or request.form.get("date_from", "")
    )
    date_to = normalize_date_param(
        payload.get("date_to") or request.form.get("date_to", "")
    )
    reply = ask_advisor(
        question,
        commodity=commodity,
        date_from=date_from,
        date_to=date_to,
    )
    return jsonify(
        {
            "ok": reply.ok,
            "answer": reply.answer,
            "commodity": reply.commodity,
            "source": reply.source,
            "context_preview": (reply.context_used or "")[:1200],
        }
    )


@app.route("/article/<int:article_id>")
@login_required
def article_detail(article_id: int):
    session = get_session()
    try:
        article = session.get(Article, article_id)
        if not article:
            abort(404)
        predictions = session.query(Prediction).filter_by(article_id=article.id).all()
        for item in predictions:
            item.trend_badge = TREND_BADGE.get(item.trend, "secondary")

        back_params = {
            key: request.args.get(key, "")
            for key in ("page", "broker", "commodity", "trend", "date_from", "date_to", "preset")
            if request.args.get(key)
        }
        return render_template(
            "detail.html",
            article=article,
            predictions=predictions,
            back_params=back_params,
        )
    finally:
        session.close()


@app.route("/charts")
@login_required
def charts():
    session = get_session()
    try:
        commodities = get_top_commodities(session)
        commodity = request.args.get("commodity") or (commodities[0] if commodities else "")
        date_filters = parse_date_filters(session)

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
                .filter(Prediction.trend != "未知")
                .filter(Article.publish_date != "unknown")
            )
            query = apply_article_date_filter(
                query, date_filters["date_from"], date_filters["date_to"]
            )
            records = query.order_by(Article.publish_date.asc()).all()

        trend_chart = ""
        distribution_chart = ""
        table_rows = []
        score_legend = "看涨 +2 · 偏多 +1 · 震荡/中性 0 · 偏空 -1 · 看跌 -2（0 分表示中性/震荡，不是无数据）"

        if records:
            df = pd.DataFrame(records, columns=["publish_date", "trend", "commodity", "broker"])
            df["score"] = df["trend"].map(TREND_SCORE).fillna(0)
            df["date"] = pd.to_datetime(df["publish_date"], format="%Y%m%d", errors="coerce")
            df = df.dropna(subset=["date"])

            if not df.empty:
                chart_df = (
                    df.groupby(["date", "broker"], as_index=False)
                    .agg(
                        score=("score", "mean"),
                        trend=("trend", lambda s: s.mode().iloc[0] if not s.mode().empty else s.iloc[0]),
                        count=("trend", "size"),
                    )
                    .sort_values("date")
                )

                # 机构过多时图例会叠成一团：只保留样本最多的前 8 家
                broker_rank = (
                    chart_df.groupby("broker")["count"].sum().sort_values(ascending=False)
                )
                keep_brokers = set(broker_rank.head(8).index.tolist())
                plot_df = chart_df.copy()
                if len(broker_rank) > 8:
                    plot_df.loc[~plot_df["broker"].isin(keep_brokers), "broker"] = "其他"
                    plot_df = (
                        plot_df.groupby(["date", "broker"], as_index=False)
                        .agg(score=("score", "mean"), count=("count", "sum"))
                        .sort_values("date")
                    )

                trend_fig = px.line(
                    plot_df,
                    x="date",
                    y="score",
                    color="broker",
                    markers=True,
                    title=f"{commodity} · 趋势评分走势",
                    labels={"date": "日期", "score": "趋势评分", "broker": "机构"},
                )
                trend_fig.add_hline(y=0, line_dash="dot", line_color="#94a3b8")
                trend_fig.update_traces(marker=dict(size=7), line=dict(width=2))
                trend_fig.update_layout(
                    template="plotly_white",
                    height=460,
                    margin=dict(t=56, b=96, l=48, r=24),
                    yaxis=dict(range=[-2.2, 2.2], dtick=1),
                    legend=dict(
                        orientation="h",
                        yanchor="top",
                        y=-0.18,
                        x=0,
                        xanchor="left",
                        font=dict(size=11),
                        bgcolor="rgba(255,255,255,0.85)",
                        traceorder="normal",
                    ),
                    hovermode="x unified",
                )

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
                recent = chart_df.sort_values("date", ascending=False).head(30)
                table_rows = []
                for row in recent.to_dict("records"):
                    score = float(row["score"])
                    table_rows.append(
                        {
                            "publish_date": row["date"].strftime("%Y-%m-%d")
                            if hasattr(row["date"], "strftime")
                            else str(row["date"])[:10],
                            "broker": row["broker"],
                            "score": round(score, 2),
                            "trend": row.get("trend") or score_label(score),
                            "trend_label": score_label(score),
                            "score_badge": score_badge(score),
                            "count": int(row.get("count", 1)),
                        }
                    )

        return render_template(
            "charts.html",
            commodity=commodity,
            commodities=commodities,
            trend_chart=trend_chart,
            distribution_chart=distribution_chart,
            table_rows=table_rows,
            has_data=bool(table_rows),
            score_legend=score_legend,
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
@role_required(ROLE_ENTERPRISE)
def insights():
    session = get_session()
    try:
        commodities = get_filter_commodities(session)
        commodity = request.args.get("commodity") or (commodities[0] if commodities else "")
        date_filters = parse_date_filters(session)

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
@role_required(ROLE_ENTERPRISE)
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

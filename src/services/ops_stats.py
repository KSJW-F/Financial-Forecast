"""企业端：登录与使用统计。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.models import AccessLog, User

CN_TZ = ZoneInfo("Asia/Shanghai")


@dataclass
class OpsStats:
    login_today: int
    login_7d: int
    active_users_7d: int
    page_views_today: int
    total_users: int
    recent_logins: list[dict]


def _as_utc_naive(dt: datetime) -> datetime:
    """AccessLog.created_at 按 UTC naive 存储。"""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _format_cn(dt: datetime | None) -> str:
    if not dt:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(CN_TZ).strftime("%Y-%m-%d %H:%M")


def compute_ops_stats(session: Session) -> OpsStats:
    now_cn = datetime.now(CN_TZ)
    start_today_cn = now_cn.replace(hour=0, minute=0, second=0, microsecond=0)
    start_today_utc = _as_utc_naive(start_today_cn)
    start_7d_utc = _as_utc_naive(now_cn - timedelta(days=7))

    login_today = (
        session.query(func.count(AccessLog.id))
        .filter(AccessLog.action == "login", AccessLog.created_at >= start_today_utc)
        .scalar()
        or 0
    )
    login_7d = (
        session.query(func.count(AccessLog.id))
        .filter(AccessLog.action == "login", AccessLog.created_at >= start_7d_utc)
        .scalar()
        or 0
    )
    active_users_7d = (
        session.query(func.count(func.distinct(AccessLog.user_id)))
        .filter(AccessLog.created_at >= start_7d_utc)
        .scalar()
        or 0
    )
    page_views_today = (
        session.query(func.count(AccessLog.id))
        .filter(AccessLog.action == "page_view", AccessLog.created_at >= start_today_utc)
        .scalar()
        or 0
    )
    total_users = session.query(func.count(User.id)).scalar() or 0

    recent = (
        session.query(AccessLog)
        .filter(AccessLog.action == "login")
        .order_by(AccessLog.created_at.desc())
        .limit(12)
        .all()
    )
    recent_logins = [
        {
            "username": row.username,
            "role": row.role,
            "path": row.path,
            "created_at": _format_cn(row.created_at),
        }
        for row in recent
    ]
    return OpsStats(
        login_today=int(login_today),
        login_7d=int(login_7d),
        active_users_7d=int(active_users_7d),
        page_views_today=int(page_views_today),
        total_users=int(total_users),
        recent_logins=recent_logins,
    )

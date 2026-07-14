"""演示账号鉴权：Flask session + 角色守卫。"""
from __future__ import annotations

from datetime import datetime, timedelta
from functools import wraps
from typing import Callable

from flask import g, jsonify, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from src.models import AccessLog, SessionLocal, User

ROLE_ENTERPRISE = "enterprise"
ROLE_CLIENT = "client"
SESSION_USER_ID = "user_id"
SESSION_VIEW_THROTTLE = "view_throttle"


def _wants_json() -> bool:
    if request.path.startswith("/api/"):
        return True
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    best = request.accept_mimetypes.best
    return best == "application/json"


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    return check_password_hash(password_hash, password)


def get_user_by_id(user_id: int | None) -> User | None:
    if not user_id:
        return None
    db = SessionLocal()
    try:
        return db.get(User, user_id)
    finally:
        db.close()


def get_user_by_username(username: str) -> User | None:
    db = SessionLocal()
    try:
        return db.query(User).filter(User.username == username.strip()).first()
    finally:
        db.close()


def authenticate(username: str, password: str) -> User | None:
    user = get_user_by_username(username)
    if not user:
        return None
    if not verify_password(user.password_hash, password):
        return None
    return user


def login_user(user: User) -> None:
    session.clear()
    session[SESSION_USER_ID] = user.id
    session.permanent = True
    record_access(user, "login", request.path or "/login")


def logout_user() -> None:
    session.clear()


def current_user() -> User | None:
    if hasattr(g, "_current_user"):
        return g._current_user
    user = get_user_by_id(session.get(SESSION_USER_ID))
    g._current_user = user
    return user


def record_access(user: User | None, action: str, path: str = "") -> None:
    if not user:
        return
    db = SessionLocal()
    try:
        db.add(
            AccessLog(
                user_id=user.id,
                username=user.username,
                role=user.role,
                action=action,
                path=(path or "")[:300],
            )
        )
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def maybe_record_page_view(user: User | None, path: str) -> None:
    """同 path 5 分钟内不重复记 page_view。"""
    if not user or not path:
        return
    throttle: dict = session.get(SESSION_VIEW_THROTTLE) or {}
    now = datetime.utcnow()
    last_raw = throttle.get(path)
    if last_raw:
        try:
            last = datetime.fromisoformat(last_raw)
            if now - last < timedelta(minutes=5):
                return
        except ValueError:
            pass
    record_access(user, "page_view", path)
    throttle[path] = now.isoformat()
    # 控制 session 体积
    if len(throttle) > 40:
        throttle = dict(list(throttle.items())[-20:])
    session[SESSION_VIEW_THROTTLE] = throttle


def login_required(view: Callable):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user:
            if _wants_json():
                return jsonify({"ok": False, "message": "请先登录"}), 401
            return redirect(url_for("landing", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def role_required(*roles: str):
    def decorator(view: Callable):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if not user:
                if _wants_json():
                    return jsonify({"ok": False, "message": "请先登录"}), 401
                return redirect(url_for("landing", next=request.path))
            if user.role not in roles:
                if _wants_json():
                    return jsonify({"ok": False, "message": "当前账号无权访问此功能"}), 403
                home = (
                    url_for("enterprise_home")
                    if user.role == ROLE_ENTERPRISE
                    else url_for("market_home")
                )
                return redirect(home)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def home_for_role(role: str) -> str:
    if role == ROLE_ENTERPRISE:
        return url_for("enterprise_home")
    return url_for("market_home")

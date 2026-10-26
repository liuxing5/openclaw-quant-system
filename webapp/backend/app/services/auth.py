# -*- coding: utf-8 -*-
"""本地会员制账号：注册送 3 天试用，到期后订阅月/季/年卡。

不依赖外部账号服务：用户表落在本地 PostgreSQL ``app_users``，
密码 PBKDF2-SHA256，登录 token 用 HMAC-SHA256 自签（7 天有效）。
支付走人工：管理员用 ``/api/v1/auth/activate`` 按 QQ 确认后开通。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from ..db import db_session, query, query_one

TRIAL_DAYS = 3
TOKEN_DAYS = 7
_SECRET = os.getenv("AUTH_SECRET", "biaoge-local-auth-2026").encode()
_ADMIN_KEY = os.getenv("MEMBERSHIP_ADMIN_KEY", "lx@610323")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PLANS = {
    "month": {"label": "月卡", "price": 98, "days": 30},
    "quarter": {"label": "季卡", "price": 268, "days": 90},
    "year": {"label": "年卡", "price": 688, "days": 365},
}

_TABLE_READY = False
_TABLE_LOCK = threading.Lock()

_DDL = """
CREATE TABLE IF NOT EXISTS app_users (
    id uuid PRIMARY KEY,
    email text NOT NULL UNIQUE,
    password_hash text NOT NULL,
    nickname text,
    plan text NOT NULL DEFAULT 'trial',
    trial_start timestamptz NOT NULL DEFAULT now(),
    plan_expiry timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_login_at timestamptz,
    is_admin boolean NOT NULL DEFAULT false
)
"""


def ensure_table() -> None:
    global _TABLE_READY
    if _TABLE_READY:
        return
    with _TABLE_LOCK:
        if _TABLE_READY:
            return
        with db_session() as conn:
            cur = conn.cursor()
            try:
                cur.execute(_DDL)
                conn.commit()
            finally:
                cur.close()
        _TABLE_READY = True


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(raw: str) -> bytes:
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))


def make_token(user_id: str) -> str:
    payload = {"uid": user_id, "exp": int(time.time()) + TOKEN_DAYS * 86400}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(_SECRET, body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def parse_token(token: str | None) -> str | None:
    if not token or "." not in token:
        return None
    body, sig = token.split(".", 1)
    expect = hmac.new(_SECRET, body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expect):
        return None
    try:
        payload = json.loads(_unb64(body))
    except Exception:  # noqa: BLE001
        return None
    if int(payload.get("exp") or 0) < time.time():
        return None
    return payload.get("uid")


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000)
    return f"{salt}${dk.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000)
    return hmac.compare_digest(dk.hex(), digest)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(v):
    return v.isoformat() if isinstance(v, datetime) else (str(v) if v else None)


def public_user(row: dict) -> dict:
    now = _now()
    trial_start = row.get("trial_start") or now
    trial_end = trial_start + timedelta(days=TRIAL_DAYS)
    expiry = row.get("plan_expiry")
    plan = row.get("plan") or "trial"
    if plan == "lifetime":
        status, active_until = "active", None
        days_left = None
        label = "终身会员"
    elif expiry and expiry > now:
        status, active_until = "active", expiry
        days_left = max(0, (expiry - now).days + (1 if (expiry - now).seconds > 0 else 0))
        label = _PLANS.get(plan, {}).get("label", "会员")
    elif now < trial_end:
        status, active_until = "trial", trial_end
        days_left = max(0, (trial_end - now).days + (1 if (trial_end - now).seconds > 0 else 0))
        label = "试用中"
    else:
        status, active_until = "expired", expiry or trial_end
        days_left = 0
        label = "已到期"
    return {
        "id": str(row["id"]),
        "username": row.get("nickname") or row["email"].split("@")[0],
        "nickname": row.get("nickname") or row["email"].split("@")[0],
        "email": row["email"],
        "role": "admin" if row.get("is_admin") else "user",
        "plan": plan,
        "plan_label": label,
        "plan_expiry": _iso(expiry),
        "created_at": _iso(row.get("created_at")),
        "last_login_at": _iso(row.get("last_login_at")),
        "is_admin": bool(row.get("is_admin")),
        "membership": {
            "status": status,
            "plan": plan,
            "plan_label": label,
            "trial_end": _iso(trial_end),
            "active_until": _iso(active_until),
            "days_left": days_left,
            "is_trial": status == "trial",
            "is_expired": status == "expired",
        },
        "quota_used": 0,
        "quota_total": 9999 if status != "expired" else 0,
        "status": 1,
    }


def _row_to_dict(row) -> dict:
    return dict(row) if row else {}


def register(email: str, password: str, nickname: str | None = None) -> dict:
    ensure_table()
    email = (email or "").strip().lower()
    if not _EMAIL_RE.match(email):
        raise ValueError("请输入有效邮箱")
    if len(password or "") < 6:
        raise ValueError("密码至少 6 位")
    uid = str(uuid.uuid4())
    nick = (nickname or "").strip() or email.split("@")[0]
    try:
        with db_session() as conn:
            cur = conn.cursor()
            try:
                cur.execute(
                    """
                    INSERT INTO app_users (id, email, password_hash, nickname, plan, trial_start)
                    VALUES (%s, %s, %s, %s, 'trial', now())
                    """,
                    (uid, email, _hash_password(password), nick),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cur.close()
    except Exception as exc:  # noqa: BLE001
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            raise ValueError("该邮箱已注册，请直接登录") from None
        raise
    row = query_one("SELECT * FROM app_users WHERE id = %s", (uid,))
    return {"token": make_token(uid), "user": public_user(_row_to_dict(row))}


def login(email: str, password: str) -> dict:
    ensure_table()
    email = (email or "").strip().lower()
    row = query_one("SELECT * FROM app_users WHERE email = %s", (email,))
    if not row or not _verify_password(password or "", row["password_hash"]):
        raise ValueError("邮箱或密码错误")
    with db_session() as conn:
        cur = conn.cursor()
        try:
            cur.execute("UPDATE app_users SET last_login_at = now() WHERE id = %s", (row["id"],))
            conn.commit()
        finally:
            cur.close()
    row = query_one("SELECT * FROM app_users WHERE id = %s", (row["id"],))
    return {"token": make_token(str(row["id"])), "user": public_user(_row_to_dict(row))}


def me(user_id: str | None) -> dict | None:
    if not user_id:
        return None
    ensure_table()
    row = query_one("SELECT * FROM app_users WHERE id = %s", (user_id,))
    return public_user(_row_to_dict(row)) if row else None


def activate(email: str, plan: str, admin_key: str) -> dict:
    ensure_table()
    if not admin_key or not hmac.compare_digest(str(admin_key), _ADMIN_KEY):
        raise PermissionError("管理员密钥错误")
    p = _PLANS.get(plan)
    if not p:
        raise ValueError("plan 必须是 month / quarter / year")
    row = query_one("SELECT * FROM app_users WHERE email = %s", ((email or "").strip().lower(),))
    if not row:
        raise ValueError("该邮箱未注册")
    now = _now()
    base = row.get("plan_expiry") if row.get("plan_expiry") and row["plan_expiry"] > now else now
    expiry = base + timedelta(days=p["days"])
    with db_session() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                "UPDATE app_users SET plan = %s, plan_expiry = %s WHERE id = %s",
                (plan, expiry, row["id"]),
            )
            conn.commit()
        finally:
            cur.close()
    return public_user(_row_to_dict(query_one("SELECT * FROM app_users WHERE id = %s", (row["id"],))))

# -*- coding: utf-8 -*-
"""B3. 用户反馈（提交 / 我的反馈 / 撤回）。

本系统是单用户本地部署，``user_id`` 统一为 NULL 代表「本机用户」；工作台
（``services/admin.py``）读同一张表并可按状态筛选、回复。

删除语义：前端先试 ``DELETE``，失败再降级 ``PATCH {status:"withdrawn"}``。
这里两者都支持，且都是**软撤回**（保留审计痕迹），不物理删除。
"""
from __future__ import annotations

import uuid

from ..db import execute, query, query_one, scalar

CATEGORIES = ("bug", "idea", "data", "ui", "other")
STATUSES = ("open", "replied", "closed", "withdrawn")

_MAX_CONTENT = 4000
_MIN_CONTENT = 5


def _ser(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "category": row.get("category"),
        "content": row.get("content"),
        "contact": row.get("contact"),
        "status": row.get("status"),
        "admin_reply": row.get("admin_reply"),
        "replied_at": row["replied_at"].isoformat() if row.get("replied_at") else None,
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }


def list_my(page: int = 1, size: int = 20) -> dict:
    page = max(1, int(page or 1))
    size = max(1, min(int(size or 20), 100))
    total = int(scalar("SELECT COUNT(*) FROM app_feedbacks WHERE user_id IS NULL") or 0)
    rows = query(
        """
        SELECT id, category, content, contact, status, admin_reply, replied_at, created_at
        FROM app_feedbacks
        WHERE user_id IS NULL
        ORDER BY created_at DESC
        LIMIT %s OFFSET %s
        """,
        (size, (page - 1) * size),
    )
    return {"items": [_ser(r) for r in rows], "total": total, "page": page, "size": size}


def create(payload: dict) -> dict:
    payload = payload or {}
    category = str(payload.get("category") or "other").strip()
    if category not in CATEGORIES:
        raise ValueError("category 必须是 " + "/".join(CATEGORIES) + " 之一")
    content = str(payload.get("content") or "").strip()
    if len(content) < _MIN_CONTENT:
        raise ValueError(f"反馈内容至少 {_MIN_CONTENT} 个字")
    if len(content) > _MAX_CONTENT:
        raise ValueError(f"反馈内容最多 {_MAX_CONTENT} 个字")
    contact = str(payload.get("contact") or "").strip()[:160] or None
    fid = str(uuid.uuid4())
    # 注意：db.query() 只读、结束时 rollback，写操作必须走 execute()
    execute(
        """
        INSERT INTO app_feedbacks (id, user_id, category, content, contact, status)
        VALUES (%s, NULL, %s, %s, %s, 'open')
        """,
        (fid, category, content, contact),
    )
    row = query_one(
        "SELECT id, category, content, contact, status, admin_reply, replied_at, created_at "
        "FROM app_feedbacks WHERE id = %s",
        (fid,),
    )
    return _ser(row)


def _uid(value) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def withdraw(feedback_id) -> dict | None:
    uid = _uid(feedback_id)
    if not uid:
        return None
    n = execute(
        "UPDATE app_feedbacks SET status = 'withdrawn' "
        "WHERE id = %s AND user_id IS NULL AND status <> 'closed'",
        (uid,),
    )
    if not n:
        return None
    row = query_one(
        "SELECT id, category, content, contact, status, admin_reply, replied_at, created_at "
        "FROM app_feedbacks WHERE id = %s",
        (uid,),
    )
    return _ser(row) if row else None

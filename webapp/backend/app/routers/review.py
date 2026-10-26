# -*- coding: utf-8 -*-
"""C2. 每日复盘、情绪历史序列。"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query

from ..services import review as svc

router = APIRouter(tags=["review"])


@router.get("/reviews")
def reviews(page: int = Query(1, ge=1), size: int = Query(30, ge=1, le=200)):
    return svc.list_reviews(page, size)


@router.get("/reviews/{day}")
def review_detail(day: date, region: str = Query("cn", max_length=8)):
    data = svc.get_review(day, region)
    if not data:
        raise HTTPException(status_code=404, detail="该交易日暂无复盘，可触发生成")
    return data


@router.post("/reviews/{day}/generate")
def generate_review(day: date, payload: dict | None = None):
    """由当日行情 / 龙虎榜 / 板块 / 情绪数据自动撰写复盘（幂等覆盖）。"""
    region = str((payload or {}).get("region") or "cn")[:8]
    if not svc.has_quote(day):
        raise HTTPException(status_code=400, detail=f"{day} 没有行情数据，无法生成复盘")
    content = svc.generate_review(day, region)
    if not content:
        raise HTTPException(status_code=422, detail="当日数据不足，复盘生成失败")
    # services.review.generate_review 内部已幂等落库（同一天重复生成直接覆盖）
    return {
        "trade_date": str(day),
        "region": region,
        "content": content,
        "title": content.get("title"),
        "mood": content.get("mood"),
        "score": content.get("score"),
        "summary": content.get("summary"),
        "generated": True,
    }


@router.get("/sentiment/history")
def sentiment_history(days: int = Query(180, ge=1, le=500)):
    return svc.sentiment_history(days)

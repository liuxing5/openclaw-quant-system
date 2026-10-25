# -*- coding: utf-8 -*-
"""总台（/dashboard）聚合接口。"""
from fastapi import APIRouter, Query

from ..services import market as svc

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard/overview")
def dashboard_overview(refresh: bool = Query(False, description="强制重算聚合缓存")):
    """一屏掌握当日大盘：指数、广度、情绪、梯队、榜单、板块热度。"""
    return svc.dashboard_overview(refresh=refresh)


@router.get("/dashboard/summary")
def dashboard_summary():
    """轻量版：只返回顶栏状态条需要的字段，用于轮询。"""
    day = svc.latest_trade_date()
    if not day:
        return {"trade_date": None}
    br = svc.breadth(day)
    senti = svc.sentiment(day)
    return {
        "trade_date": str(day),
        "score": senti.get("score"),
        "level": senti.get("level"),
        "amount": br.get("amount"),
        "limit_up": br.get("limit_up"),
        "limit_down": br.get("limit_down"),
        "seal_rate": br.get("seal_rate"),
        "up": br.get("up"),
        "down": br.get("down"),
        "flat": br.get("flat"),
    }

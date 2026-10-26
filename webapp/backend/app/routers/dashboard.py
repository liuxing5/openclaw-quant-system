# -*- coding: utf-8 -*-
"""总台（/dashboard）聚合接口。

盘中优先走 ``live_market``（腾讯全市场快照 + 日线历史），
快照不可用时自动回退 ``market`` 的 T-1 聚合，接口形状保持一致。
"""
from fastapi import APIRouter, Query

from ..services import live_market, market as svc

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard/overview")
def dashboard_overview(refresh: bool = Query(False, description="强制重算聚合缓存")):
    """一屏掌握当日大盘：指数、广度、情绪、梯队、榜单、板块热度。"""
    return live_market.overview(refresh=refresh)


@router.get("/dashboard/summary")
def dashboard_summary():
    """轻量版：只返回顶栏状态条需要的字段，用于轮询。"""
    live = live_market.summary()
    if live:
        return live
    day = svc.latest_trade_date()
    if not day:
        return {"trade_date": None}
    br = svc.breadth(day)
    senti = svc.sentiment(day)
    return {
        "trade_date": str(day),
        "live": False,
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

# -*- coding: utf-8 -*-
"""个股分析增强接口：AI 量价推演。"""
from fastapi import APIRouter, HTTPException

from ..db import query
from ..services import minute
from ..services import stock_analysis as svc
from ..services import technical

router = APIRouter(tags=["analysis"])


@router.get("/stock-analysis/{code}/multi-level")
def multi_level(code: str):
    ts_code = svc._norm(code)
    data = minute.multi_level(ts_code)
    if not data.get("daily") and not data.get("m30"):
        raise HTTPException(status_code=404, detail="该标的分钟/日线数据不足")
    return data


@router.get("/stock-analysis/{code}/technical")
def technical_detail(code: str):
    ts_code = svc._norm(code)
    rows = query(
        "SELECT trade_date, high, low, close FROM daily_quotes WHERE ts_code = %s "
        "ORDER BY trade_date DESC LIMIT 260",
        (ts_code,),
    )
    if not rows or len(rows) < 26:
        raise HTTPException(status_code=404, detail="该标的日线数据不足")
    rows = list(reversed(rows))
    highs = [float(r["high"]) for r in rows if r.get("high") is not None]
    lows = [float(r["low"]) for r in rows if r.get("low") is not None]
    closes = [float(r["close"]) for r in rows if r.get("close") is not None]
    return {
        "ts_code": ts_code,
        "trade_date": str(rows[-1]["trade_date"]),
        "bars": len(closes),
        "macd": technical.macd(closes),
        "chan": technical.chan(highs, lows, closes),
    }


@router.post("/stock-analysis/{code}/projection")
def projection(code: str, payload: dict | None = None):
    target = (payload or {}).get("target")
    try:
        data = svc.projection(code, target)
    except ValueError:
        raise HTTPException(status_code=400, detail="target 必须是数字") from None
    if not data:
        raise HTTPException(status_code=404, detail="该标的日线数据不足，无法推演")
    return data

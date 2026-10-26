# -*- coding: utf-8 -*-
"""个股分析增强接口：AI 量价推演。"""
from fastapi import APIRouter, HTTPException

from ..services import stock_analysis as svc

router = APIRouter(tags=["analysis"])


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

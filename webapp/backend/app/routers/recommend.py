# -*- coding: utf-8 -*-
"""荐股：五策略对比页的结构化数据。"""
from fastapi import APIRouter, HTTPException

from ..services import funnel

router = APIRouter(tags=["recommend"])


@router.get("/recommend/funnel")
def recommend_funnel():
    try:
        return funnel.funnel_data()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"荐股数据源暂不可用: {exc}") from None

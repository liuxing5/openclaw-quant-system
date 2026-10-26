# -*- coding: utf-8 -*-
"""C3. 实时异动事件与异动规则。"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query

from ..services import anomaly as svc

router = APIRouter(tags=["anomaly"])


@router.get("/anomaly/events")
def anomaly_events(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    scope: str = Query("all_market", pattern="^(all_market|symbol|board)$"),
    all_market: bool = Query(True),
    date_str: str | None = Query(None, alias="date"),
):
    day = None
    if date_str:
        try:
            day = date.fromisoformat(date_str)
        except ValueError:
            raise HTTPException(status_code=400, detail="date 必须是 YYYY-MM-DD") from None
    # all_market=false 与 scope=symbol 等价，保留两个参数是为了兼容原站口径
    eff_scope = scope if scope != "all_market" else ("all_market" if all_market else "symbol")
    return svc.events(day, eff_scope, page, size)


@router.post("/anomaly/events/scan")
def anomaly_scan(payload: dict | None = None):
    scope = str((payload or {}).get("scope") or "all_market")
    if scope not in ("all_market", "symbol", "board"):
        raise HTTPException(status_code=400, detail="scope 必须是 all_market/symbol/board")
    return svc.scan(scope)


@router.get("/anomaly/rules")
def anomaly_rules(page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200)):
    return svc.list_rules(page, size)


@router.post("/anomaly/rules")
def create_anomaly_rule(payload: dict):
    try:
        return svc.create_rule(payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.patch("/anomaly/rules/{rule_id}")
def update_anomaly_rule(rule_id: str, payload: dict):
    try:
        item = svc.update_rule(rule_id, payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if not item:
        raise HTTPException(status_code=404, detail="规则不存在")
    return item


@router.delete("/anomaly/rules/{rule_id}")
def delete_anomaly_rule(rule_id: str):
    if not svc.delete_rule(rule_id):
        raise HTTPException(status_code=404, detail="规则不存在")
    return {"ok": True, "id": rule_id}

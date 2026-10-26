# -*- coding: utf-8 -*-
"""C1. 选股策略：定义 CRUD、立即跑批、命中明细、统计与历史扫描。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..services import strategy as svc

router = APIRouter(tags=["strategy"])


def _bad(exc: ValueError):
    raise HTTPException(status_code=400, detail=str(exc)) from None


# 注意：字面量路径必须先于 /{code} 注册，否则 run-stats / scans 会被当成 code
@router.get("/strategies")
def strategies():
    items = svc.list_strategies()
    return {
        "items": items,
        "total": len(items),
        "meta": {
            "engine": "sql_full_market_scan",
            "runnable": sum(1 for i in items if i.get("runnable")),
            "families": sorted({i.get("family") for i in items if i.get("family")}),
        },
    }


@router.post("/strategies")
def create_strategy(payload: dict):
    try:
        return svc.create_strategy(payload or {})
    except FileExistsError:
        raise HTTPException(status_code=409, detail="策略 code 已存在") from None
    except ValueError as exc:
        _bad(exc)


@router.get("/strategies/run-stats")
def run_stats(days: int = Query(30, ge=1, le=365)):
    return svc.run_stats(days)


@router.get("/strategies/scans")
def scans(
    strategy: str | None = Query(None),
    days: int = Query(30, ge=1, le=730),
):
    data = svc.list_scans(strategy, days)
    data["options"] = svc.scan_summary().get("strategies", [])
    return data


@router.get("/strategies/{code}")
def strategy_detail(code: str):
    item = svc.get_def(code)
    if not item:
        raise HTTPException(status_code=404, detail="策略不存在")
    return item


@router.patch("/strategies/{code}")
def update_strategy(code: str, payload: dict):
    try:
        item = svc.update_strategy(code, payload or {})
    except ValueError as exc:
        _bad(exc)
    if not item:
        raise HTTPException(status_code=404, detail="策略不存在")
    return item


@router.delete("/strategies/{code}")
def delete_strategy(code: str):
    if not svc.delete_strategy(code):
        raise HTTPException(status_code=404, detail="策略不存在")
    return {"ok": True, "code": code}


@router.post("/strategies/{code}/run")
def run_strategy(code: str):
    try:
        return svc.run_strategy(code)
    except KeyError:
        raise HTTPException(status_code=404, detail="策略不存在") from None
    except NotImplementedError:
        raise HTTPException(
            status_code=400,
            detail="该策略为自定义定义，尚无内置扫描器；请从内置策略复制参数后运行",
        ) from None
    except ValueError as exc:
        _bad(exc)


@router.get("/strategies/{code}/runs")
def runs(code: str, limit: int = Query(20, ge=1, le=100)):
    return svc.list_runs(code, limit)


@router.get("/strategies/{code}/hits")
def hits(
    code: str,
    run_id: str | None = Query(None),
    size: int = Query(50, ge=1, le=500),
):
    data = svc.run_hits(code, run_id, size)
    if not data:
        raise HTTPException(status_code=404, detail="暂无命中记录，请先运行该策略")
    return data

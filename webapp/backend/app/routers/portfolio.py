# -*- coding: utf-8 -*-
"""B1. 自选分组 / 持仓 / 价格提醒。

契约见 ``webapp/API_CONTRACT.md`` 的「B1 自选 / 持仓 / 提醒」。路由层只做
参数解析、路径 id 校验和 HTTP 语义（404/400），取数与计算全部在
``services/portfolio.py``，方便不起服务就能联调。

注意路由注册顺序：``/watchlists/default``、``/watchlists/holding-alert-settings``、
``/alerts/triggered`` 这些**字面量**路径必须排在 ``/{id}`` 之前，
否则会被 FastAPI 当成普通 id 段吃掉。
"""
from __future__ import annotations

import uuid as _uuid

from fastapi import APIRouter, HTTPException, Query

from ..services import portfolio as svc

router = APIRouter(tags=["portfolio"])


def _uid(value) -> str:
    """路径 id 必须是合法 uuid，否则 404（别把脏字符串丢给 PG 变成 500）。"""
    try:
        return str(_uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=404, detail="记录不存在") from None


def _one(row, detail: str = "记录不存在"):
    if not row:
        raise HTTPException(status_code=404, detail=detail)
    return row


def _bad(exc: ValueError):
    """service 层用 ValueError 表达参数错误，这里统一翻成 400。"""
    raise HTTPException(status_code=400, detail=str(exc)) from None


# ================================================================ 自选分组
@router.get("/watchlists")
def watchlists():
    """分组列表（含每个分组的标的数）。"""
    return {"items": svc.list_groups()}


@router.post("/watchlists")
def create_watchlist(payload: dict):
    try:
        return svc.create_group((payload or {}).get("name"))
    except ValueError as exc:
        _bad(exc)


@router.get("/watchlists/default")
def default_watchlist():
    """默认分组：库里没有「默认分组」时自动创建一条再返回。"""
    return svc.default_group()


@router.get("/watchlists/default/items")
def default_watchlist_items():
    """字面量别名：前端/冒烟脚本会直接请求 /watchlists/default/items。"""
    return svc.list_items(svc.default_group()["id"])


@router.get("/watchlists/default/quotes")
def default_watchlist_quotes(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    refresh: str | None = Query("auto"),
):
    return svc.group_quotes(svc.default_group()["id"], page, size, refresh)


@router.get("/watchlists/holding-alert-settings")
def get_holding_alert_settings():
    """持仓破位邮件提醒开关（原站文案：跌破成本价的 5% 触发）。"""
    return svc.get_holding_alert_settings()


@router.put("/watchlists/holding-alert-settings")
def put_holding_alert_settings(payload: dict):
    try:
        return svc.put_holding_alert_settings(payload or {})
    except ValueError as exc:
        _bad(exc)


@router.patch("/watchlists/{id}")
def rename_watchlist(id: str, payload: dict):
    gid = _uid(id)
    try:
        return _one(svc.rename_group(gid, (payload or {}).get("name")))
    except ValueError as exc:
        _bad(exc)


@router.delete("/watchlists/{id}")
def delete_watchlist(id: str):
    """删除分组连同其标的一起删（表上没有外键，service 里保证不留孤儿行）。"""
    gid = _uid(id)
    _one(svc.delete_group(gid))
    return {"ok": True, "id": gid}


# ================================================================ 分组标的
@router.get("/watchlists/{id}/items")
def watchlist_items(id: str):
    return svc.list_items(_uid(id))


@router.post("/watchlists/{id}/items")
def add_watchlist_item(id: str, payload: dict):
    """加入分组；同一代码重复添加**幂等**（返回既有标的，仍为 2xx）。"""
    try:
        return svc.add_item(
            _uid(id),
            (payload or {}).get("ts_code"),
            (payload or {}).get("stock_name"),
        )
    except ValueError as exc:
        _bad(exc)


@router.delete("/watchlists/{id}/items/{item_id}")
def remove_watchlist_item(id: str, item_id: str):
    iid = _uid(item_id)
    _one(svc.remove_item(_uid(id), iid), "标的不存在")
    return {"ok": True, "id": iid}


@router.get("/watchlists/{id}/quotes")
def watchlist_quotes(
    id: str,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    refresh: str | None = Query("auto"),
):
    """行情列：一次 snapshot_rows 覆盖整页（禁止逐条查），返回 as_of + meta。"""
    return svc.group_quotes(_uid(id), page, size, refresh)


# ================================================================ 持仓
@router.get("/holdings")
def holdings(page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=200)):
    """持仓列表：分页下推 SQL，合并实时价算市值/浮盈/今日盈亏。"""
    return svc.list_holdings(page, size)


@router.post("/holdings")
def create_holding(payload: dict):
    try:
        return svc.create_holding(payload or {})
    except ValueError as exc:
        _bad(exc)


@router.get("/holdings/summary")
def holdings_summary():
    """持仓汇总：总市值/总成本/总浮盈/今日盈亏/盈亏家数。"""
    return svc.holdings_summary()


@router.patch("/holdings/{id}")
def update_holding(id: str, payload: dict):
    try:
        return _one(svc.update_holding(_uid(id), payload or {}))
    except ValueError as exc:
        _bad(exc)


@router.delete("/holdings/{id}")
def delete_holding(id: str):
    hid = _uid(id)
    _one(svc.delete_holding(hid))
    return {"ok": True, "id": hid}


# ================================================================ 提醒
@router.get("/alerts")
def alerts(page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=200), type: str | None = None):
    """提醒列表；读取时顺带**惰性**做一次触发检测并回写计数。"""
    try:
        return svc.list_alerts(page, size, type)
    except ValueError as exc:
        _bad(exc)


@router.get("/alerts/triggered")
def triggered_alerts(limit: int = Query(20, ge=1, le=200)):
    """最近触发记录（先惰性检测一轮，再按 last_triggered_at 倒序取前 N 条）。"""
    return svc.triggered_alerts(limit)


@router.post("/alerts")
def create_alert(payload: dict):
    try:
        return svc.create_alert(payload or {})
    except ValueError as exc:
        _bad(exc)


@router.patch("/alerts/{id}")
def update_alert(id: str, payload: dict):
    try:
        return _one(svc.update_alert(_uid(id), payload or {}))
    except ValueError as exc:
        _bad(exc)


@router.delete("/alerts/{id}")
def delete_alert(id: str):
    aid = _uid(id)
    _one(svc.delete_alert(aid))
    return {"ok": True, "id": aid}

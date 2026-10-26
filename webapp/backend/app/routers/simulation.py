# -*- coding: utf-8 -*-
"""B2. 模拟盘：账户 / 下单撮合 / 持仓 / 委托 / 汇总 / 清仓。

路由层只做三件事：参数解析（分页、id）、HTTP 语义（400/404）、把结果交回前端。
真正的撮合、费用、账务一致性全在 ``services/simulation.py``，
因为那部分必须整笔事务原子提交，放路由里迟早被人改出「钱扣了持仓没记」的洞。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..services import simulation as svc

router = APIRouter(tags=["simulation"])


def _page_size(page: int, size: int) -> tuple[int, int]:
    if page < 1:
        raise HTTPException(status_code=400, detail="page 必须 >= 1")
    if size < 1 or size > 200:
        raise HTTPException(status_code=400, detail="size 取值范围 1~200")
    return page, size


# ---------------------------------------------------------------- 账户
@router.get("/simulation/accounts")
def list_accounts():
    """账户列表（含现金、持仓市值、总资产、持仓/委托笔数）。"""
    return svc.list_accounts()


@router.post("/simulation/accounts")
def create_account(payload: Optional[dict] = None):
    """新建账户：``{name?, initial_cash?}``，默认 100 万初始资金。"""
    data = payload or {}
    return svc.create_account(data.get("name"), data.get("initial_cash"))


# ---------------------------------------------------------------- 汇总 / 持仓 / 委托
@router.get("/simulation/summary")
def summary(account_id: Optional[str] = Query(None)):
    """账户总览：现金、持仓市值、总资产、总盈亏、当日盈亏、胜率、收益率、委托数。

    不传 ``account_id`` 时自动使用（必要时创建）「默认账户」。
    """
    return svc.summary(account_id)


@router.get("/simulation/positions")
def positions(
    account_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
):
    """持仓分页（含最新价、市值、浮盈、当日盈亏）。"""
    page, size = _page_size(page, size)
    return svc.positions(account_id, page, size)


@router.get("/simulation/orders")
def orders(
    account_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
):
    """委托历史分页：``{items, total, page, size}``。"""
    page, size = _page_size(page, size)
    return svc.orders(account_id, page, size)


# ---------------------------------------------------------------- 写操作
@router.post("/simulation/orders")
def place_order(payload: dict):
    """下单并撮合：``{account_id?, ts_code, side, qty, order_type, price?}``

    成交返回 ``{order, position, account, matched:true}``；
    限价不划算 / 非整手 / 超持仓 / 资金不足等业务拒绝**不报 HTTP 错**，
    而是返回 ``{order:{status:"rejected", reason}, matched:false}``（委托仍落库）。
    """
    if not isinstance(payload, dict) or not payload:
        raise HTTPException(status_code=400, detail="请求体不能为空")
    return svc.place_order(payload)


@router.delete("/simulation/orders/{order_id}")
def cancel_order(order_id: str):
    """撤单：仅 pending 可撤（本引擎即时撮合，成交后返回 400 说明原因）。"""
    if not order_id.strip():
        raise HTTPException(status_code=400, detail="缺少委托 id")
    return svc.cancel_order(order_id)


@router.post("/simulation/reset")
def reset(payload: Optional[dict] = None):
    """清仓重置：``{account_id?}`` → 清空委托与持仓，现金恢复初始值。"""
    data = payload or {}
    return svc.reset(data.get("account_id"))

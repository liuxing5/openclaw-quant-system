# -*- coding: utf-8 -*-
"""D2. 指数 / 财务 / 市场环境 / 全市场数据表 / 个股档案（BE-finance）。

路由只做「参数校验 + 调服务 + 错误码」，所有 SQL 与口径都在
``services/finance.py``；广度/情绪/指数/成交额一律复用 ``services/market.py``，
避免同一件事在系统里有两套涨跌停与情绪口径。

注：``/market-data/securities/search`` 的字面路由在 ``routers/market.py``
且注册在先，所以本文件里的 ``/market-data/securities/{code}`` 不会抢占它。
"""
from fastapi import APIRouter, HTTPException, Query

from ..services import finance as svc

router = APIRouter(tags=["finance"])


# ---------------------------- 指数 ----------------------------


@router.get("/index/summary")
def index_summary():
    """6 大指数总览：最新价、涨跌幅、OHLC、昨收、近 20 日 sparkline。"""
    return svc.index_summary()


@router.get("/index/quotes/{ts_code}")
def index_quotes(
    ts_code: str,
    days: int = Query(250, ge=5, le=1000, description="返回最近 N 个交易日"),
):
    """单指数日线（升序 OHLCV）。代码支持 000001.SH / sh000001 / 000001。"""
    code = svc._resolve_index_code(ts_code)
    if not code:
        raise HTTPException(status_code=404, detail="指数不存在")
    data = svc.index_series(code, days)
    if not data["items"]:
        raise HTTPException(status_code=404, detail="该指数暂无行情数据")
    return data


@router.get("/index/components/{ts_code}")
def index_components(
    ts_code: str,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    top: int = Query(100, ge=5, le=2000, description="按总市值近似的成分集合大小 N"),
):
    """指数成分股（本地无官方成分表 → 按总市值前 N 只近似，meta.approx=true）。"""
    code = svc._resolve_index_code(ts_code)
    if not code:
        raise HTTPException(status_code=404, detail="指数不存在")
    return svc.index_components(code, page, size, top)


# ---------------------------- 财务 ----------------------------


@router.get("/finance/statements")
def finance_statements(
    ts_code: str | None = Query(None, description="如 600519 / 600519.SH"),
    limit: int = Query(8, ge=1, le=50),
):
    """财报数据（利润表 + 资产负债表 + 现金流），按 report_date 倒序。"""
    return svc.statements(ts_code, limit)


@router.get("/finance/indicators")
def finance_indicators(
    ts_code: str | None = Query(None, description="不传则返回全市场指标摘要"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    sort: str = Query("net_profit"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
):
    """关键指标卡：单票 latest+series，或全市场分页摘要。"""
    return svc.indicators(ts_code=ts_code, page=page, size=size, sort=sort, order=order)


# ---------------------------- 市场环境 ----------------------------


@router.get("/market-env")
def market_env():
    """市场环境一屏：指数趋势 + 广度 + 情绪 + 成交额序列 + 中文要点。"""
    return svc.market_env()


@router.get("/market-env/history")
def market_env_history(
    days: int = Query(svc._ENV_HISTORY_DEFAULT_DAYS, ge=20, le=1000,
                      description="自然日范围；实测超 3s 已把上限压到 120 天"),
):
    """环境历史：逐日涨跌家数 / 涨停数 / 成交额（SQL 单次聚合）+ 情绪分。"""
    return svc.market_env_history(days)


# ---------------------------- 全市场数据表 ----------------------------


@router.get("/data/quotes")
def data_quotes(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200, description="单次最多 200 行"),
    market: str | None = Query(None, description="SH / SZ / BJ"),
    sort: str = Query("pct_chg", description="排序列（白名单），如 close / amount"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    keyword: str | None = Query(None, max_length=32, description="代码或名称模糊匹配"),
):
    """全市场行情表：服务端分页 + 服务端排序（禁止整表拉回 Python）。"""
    return svc.data_quotes(page=page, size=size, market=market,
                           sort=sort, order=order, keyword=keyword)


@router.get("/data/fundamentals")
def data_fundamentals(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    sort: str = Query("net_profit"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    keyword: str | None = Query(None, max_length=32),
):
    """全市场基本面表（stock_fundamentals）：服务端分页 + 服务端排序。"""
    return svc.data_fundamentals(page=page, size=size, sort=sort,
                                 order=order, keyword=keyword)


# ---------------------------- 个股档案 ----------------------------


@router.get("/market-data/securities/{code}")
def security_profile(code: str):
    """个股档案：基础信息 + 最新行情快照 + 所属行业（industry 取自财务表）。"""
    data = svc.security_profile(code)
    if not data:
        raise HTTPException(status_code=404, detail=f"证券不存在: {code}")
    return data

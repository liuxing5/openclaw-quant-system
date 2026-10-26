# -*- coding: utf-8 -*-
"""A. 实时行情层接口。

三个端点共用一条取数链路（``services.realtime.snapshot_rows``），
所以这里只做「参数解析 + 结构裁剪 + 可序列化」，不做任何网络/聚合逻辑：
    - codes 缺省时自动取「自选全部 + 持仓全部」，因此前端轮询可以不传参数；
    - 单次最多 200 只（超出截断并打 ``truncated`` 标记），既控 payload 也控取价 URL 长度；
    - 任何一张业务表缺失/为空、或外部行情源全挂，都返回**结构完整**的 200 响应。
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query

from ..db import query
from ..services.realtime import is_trading, market_status, norm_code, snapshot_rows

router = APIRouter(tags=["realtime"])

MAX_CODES = 200

# 实时源（对应 services/realtime.py 的取数优先级）
_LIVE_SOURCES = ("sina", "tencent", "eastmoney")

# 完整字段：详情/列表页直出；轻量字段：轮询用（去掉涨跌停价与市值等重字段）
_FULL_FIELDS = (
    "name",
    "price",
    "pct_chg",
    "open",
    "high",
    "low",
    "pre_close",
    "volume",
    "amount",
    "turnover_rate",
    "market_cap",
    "limit_up_price",
    "limit_down_price",
    "trade_date",
    "source",
)
_LIGHT_FIELDS = (
    "name",
    "price",
    "pct_chg",
    "open",
    "high",
    "low",
    "pre_close",
    "volume",
    "amount",
    "turnover_rate",
    "trade_date",
    "source",
)


def _num(v):
    """Decimal/str/数值统一转 float；转不动就给 None（前端不用判类型）。"""
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _default_codes() -> list[str]:
    """codes 为空时的兜底代码集：自选全部 + 持仓全部。

    这两张表是本系统自己的可写表，可能还没建好或一行都没有——
    出错按「空列表」处理，接口照常返回空 items，而不是 500。
    """
    out: list[str] = []
    for sql in (
        "SELECT DISTINCT ts_code FROM app_watchlist_items",
        "SELECT DISTINCT ts_code FROM app_holdings",
    ):
        try:
            out += [r["ts_code"] for r in query(sql)]
        except Exception:  # noqa: BLE001 表缺失/连接异常一律当没有数据
            pass
    return out


def _parse_codes(codes: str | None) -> tuple[list[str], bool]:
    """``a,b,c`` -> 归一化去重的代码列表 + 是否被截断（>200 只）。"""
    raw = [c.strip() for c in (codes or "").split(",") if c.strip()]
    if not raw:
        raw = _default_codes()
    seen: dict[str, None] = {}
    for c in raw:
        n = norm_code(c)
        if n:
            seen.setdefault(n, None)
    norm = list(seen)
    return norm[:MAX_CODES], len(norm) > MAX_CODES


def _item(row: dict, fields: tuple[str, ...]) -> dict:
    """把 snapshot_rows 的行裁成契约字段，并保证 JSON 可序列化。"""
    out: dict = {"ts_code": row.get("ts_code")}
    for f in fields:
        v = row.get(f)
        if f in ("name", "source"):
            out[f] = v
        elif f == "trade_date":
            out[f] = str(v) if v else None
        else:
            out[f] = _num(v)
    return out


def _top_source(items: list[dict]) -> str:
    """顶层 source 取行里出现最多的实时源；一个实时源都没有就是 db（日线兜底）。"""
    counts: dict[str, int] = {}
    for it in items:
        s = it.get("source")
        if s:
            counts[s] = counts.get(s, 0) + 1
    for s in _LIVE_SOURCES:  # 按优先级找，平局时先到先得
        if counts.get(s):
            return s
    return "db" if counts.get("db") else "none"


def _snapshot_payload(codes: str | None, fields: tuple[str, ...]) -> dict:
    codes_list, truncated = _parse_codes(codes)
    rows = snapshot_rows(codes_list) if codes_list else []
    items = [_item(r, fields) for r in rows]
    source = _top_source(items)
    return {
        "as_of": datetime.now().isoformat(timespec="seconds"),
        "trading": is_trading(),
        "source": source,
        "live": source in _LIVE_SOURCES,
        "truncated": truncated,
        "count": len(items),
        "items": items,
    }


@router.get("/market-data/realtime")
def realtime_quotes(codes: str = Query("", description="逗号分隔 ts_code；留空=自选+持仓全部")):
    """实时行情（完整字段，含涨跌停价与市值）。

    取价走 10s（盘中）/600s（非交易）进程内缓存，请求路径只读缓存；
    网络源全挂时降级为日线收盘值，响应结构保持不变。
    """
    return _snapshot_payload(codes, _FULL_FIELDS)


@router.get("/market-data/realtime/batch")
def realtime_batch(codes: str = Query("", description="逗号分隔 ts_code；留空=自选+持仓全部")):
    """轻量实时行情，专供前端高频轮询：省掉涨跌停价/总市值等重字段，payload 更小。"""
    return _snapshot_payload(codes, _LIGHT_FIELDS)


@router.get("/market-data/realtime/status")
def realtime_status():
    """状态条：当前实际命中的数据源（sina/tencent/eastmoney/db）、是否交易中、下次刷新时间。

    会顺带触发一次（带缓存的）探活取数，因此返回的 source 就是此刻真实命中的源。
    """
    return market_status()

# -*- coding: utf-8 -*-
"""交易日历：用一张 500 行的小表替代对 200 万行行情表的 DISTINCT 扫描。

实测 `SELECT DISTINCT trade_date FROM daily_quotes ORDER BY trade_date DESC LIMIT 120`
需要回扫约 60 万条索引项（~5 秒），而同样的结果放进 trade_calendar 后只需几毫秒。
日历由行情同步任务写入，这里只做增量补齐。
"""
from __future__ import annotations

from datetime import date

from ..cache import ttl_cache
from ..db import execute, query, scalar

_DDL = """
CREATE TABLE IF NOT EXISTS trade_calendar (
    trade_date DATE PRIMARY KEY
);
"""


@ttl_cache(600)
def ensure_calendar() -> int:
    """把 daily_quotes 里新增的交易日补进日历（幂等、增量）。"""
    try:
        execute(_DDL)
    except Exception:  # noqa: BLE001
        return 0
    try:
        return execute(
            """
            INSERT INTO trade_calendar (trade_date)
            SELECT DISTINCT trade_date FROM daily_quotes
            WHERE trade_date > COALESCE((SELECT MAX(trade_date) FROM trade_calendar),
                                        DATE '1900-01-01')
            ON CONFLICT DO NOTHING
            """
        )
    except Exception:  # noqa: BLE001
        return 0


def trade_dates(limit: int = 120) -> list[date]:
    ensure_calendar()
    rows = query(
        "SELECT trade_date FROM trade_calendar ORDER BY trade_date DESC LIMIT %s",
        (limit,),
    )
    return [r["trade_date"].date() if hasattr(r["trade_date"], "date") else r["trade_date"] for r in rows]


def max_calendar_date() -> date | None:
    ensure_calendar()
    v = scalar("SELECT MAX(trade_date) FROM trade_calendar")
    return v.date() if hasattr(v, "date") else v

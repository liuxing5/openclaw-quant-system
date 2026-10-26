# -*- coding: utf-8 -*-
"""D2 财务 / 指数 / 市场环境 / 全市场数据表的服务层（BE-G）。

为什么这样写
------------
1. **SQL 下推，不在 Python 里拉全表**
   ``/data/quotes`` 背后是 5219 行的当日全市场快照，``/market-env/history``
   要在 205 万行日线上按日聚合。本机到远端 PostgreSQL 的带宽只有 40~90 KB/s
   （见 ``app/db.py`` 的实测），所以排序、分页、聚合、涨跌停判定全部下推，
   Python 只负责：date/Decimal -> JSON 可序列化值，以及少量派生指标
   （ROE、成分股权重、情绪档位、中文要点）。

2. **复用 ``services/market.py``，不写第二套口径**
   最新交易日、涨跌广度、情绪分档、指数总览、成交额序列一律直接调用
   market 服务的既有函数 —— 涨跌停口径（``_FX_CTE``）、情绪分档（
   ``SENTIMENT_BANDS``）必须与总台/行情页保持一致，否则同一份数据在不同
   页面会给出不同的涨停家数。

3. **降级要可见**
   本地没有官方指数成分表、``app_sentiment_history`` 目前为空、
   ``stock_fundamentals`` 的资产/负债/行业等列全为 NULL。这些情况一律在
   ``meta`` 里标 ``approx/degraded`` 并写清原因，而不是让前端以为数据完整。

4. **只回传小结果集**
   每个接口的返回都做了行数上限（分页 size<=200、成分 top<=2000、
   指数窗口 21 行/指数），单接口 payload 目标 < 100KB。
"""
from __future__ import annotations

import math
import time
from datetime import date, datetime
from decimal import Decimal

from ..cache import ttl_cache
from ..db import query
from . import market as mkt

# --------------------------------------------------------------------------
# 通用：JSON 序列化 / 数值兜底
# --------------------------------------------------------------------------


def _ser(v):
    """date/Decimal/psycopg2 特有类型 -> JSON 可序列化值。

    契约要求日期是 ``YYYY-MM-DD`` 字符串、金额是数值（Decimal 必须转 float），
    否则 FastAPI 的 JSONResponse 会直接 500。
    """
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.isoformat(timespec="seconds")
    if isinstance(v, date):
        return str(v)
    if isinstance(v, memoryview):
        return bytes(v).decode("utf-8", "ignore")
    return v


def _row(r) -> dict:
    return {k: _ser(v) for k, v in dict(r).items()}


def _rows(rs) -> list[dict]:
    return [_row(r) for r in rs]


def _f(v, default=None):
    try:
        if v is None:
            return default
        f = float(v)
        # 除零产生的 NaN/Inf 一旦进 JSON 会让整个响应 500，统一降级为「无数据」
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _d(v):
    if v is None:
        return None
    return v.date() if hasattr(v, "date") else v


def _pct(v, digits=2) -> float | None:
    if v is None:
        return None
    return round(float(v), digits)


# --------------------------------------------------------------------------
# 代码归一化：600519 / 600519.SH / sh600519 统一成标准 ts_code
# --------------------------------------------------------------------------


def normalize_code(code: str | None) -> str | None:
    """把裸代码补全交易所后缀。

    规则：6/9 开头 -> SH，4/8 开头 -> BJ，其余（0/2/3 开头的深市）-> SZ。
    已带后缀的直接大写返回，前端传 ``600519`` 或 ``600519.SH`` 都能命中。
    """
    c = (code or "").strip().upper()
    if not c:
        return None
    if "." in c:
        return c
    if c.startswith(("6", "9")):
        return f"{c}.SH"
    if c.startswith(("4", "8")):
        return f"{c}.BJ"
    return f"{c}.SZ"


@ttl_cache(300)
def _resolve_index_code(code: str | None) -> str | None:
    """指数代码归一化：优先查 index_basic（支持 symbol=sh000001 与裸代码）。"""
    c = (code or "").strip()
    if not c:
        return None
    if "." in c:
        return c.upper()
    rows = query(
        "SELECT ts_code FROM index_basic "
        "WHERE UPPER(symbol) = %s OR ts_code LIKE %s LIMIT 1",
        (c.upper(), f"{c.upper()}.%"),
    )
    if rows:
        return rows[0]["ts_code"]
    return normalize_code(c)


# --------------------------------------------------------------------------
# 指数总览 / 指数日线
# --------------------------------------------------------------------------

# 只取每个指数最近 21 根 K 线：总览只需要「最新价 + 昨收 + 近 20 日 sparkline」，
# 拉 600 行纯属浪费带宽（实测 600 行 ~1.6s，126 行 ~0.65s）。
_INDEX_WINDOW_SQL = """
WITH ranked AS (
  SELECT i.ts_code, i.trade_date, i.open, i.high, i.low, i.close, i.volume, b.name,
         ROW_NUMBER() OVER (PARTITION BY i.ts_code ORDER BY i.trade_date DESC) AS rn
  FROM index_quotes i
  LEFT JOIN index_basic b ON b.ts_code = i.ts_code
  WHERE i.ts_code = ANY(%s)
)
SELECT ts_code, name, trade_date, open, high, low, close, volume,
       LAG(close) OVER (PARTITION BY ts_code ORDER BY trade_date) AS prev
FROM ranked
WHERE rn <= 21
ORDER BY ts_code, trade_date
"""


@ttl_cache(60)
def index_summary() -> dict:
    """6 大指数一屏：最新价/涨跌幅/OHLC/昨收 + 近 20 日收盘迷你曲线。"""
    day = mkt.latest_trade_date()
    if not day:
        return {"items": [], "total": 0, "trade_date": None}

    codes = [c for c, _ in mkt.INDEX_LIST]
    rows = query(_INDEX_WINDOW_SQL, (codes,))
    by_code: dict[str, list[dict]] = {}
    for r in rows:
        by_code.setdefault(r["ts_code"], []).append(r)

    items = []
    ordered = list(mkt.INDEX_LIST) + [
        (c, None) for c in by_code if c not in codes
    ]
    for code, fallback_name in ordered:
        series = by_code.get(code)
        if not series:
            continue
        last = series[-1]
        close = _f(last["close"])
        prev = _f(last.get("prev"))
        pct = ((close - prev) / prev * 100) if (prev and close is not None) else None
        items.append(
            {
                "ts_code": code,
                "name": last.get("name") or fallback_name or code,
                "price": close,
                "pct_chg": _pct(pct),
                "open": _f(last["open"]),
                "high": _f(last["high"]),
                "low": _f(last["low"]),
                "volume": _f(last["volume"]),
                "prev_close": prev,
                # sparkline = 最近 20 个交易日收盘价（含最新）
                "sparkline": [_f(s["close"]) for s in series[-20:]],
            }
        )
    return {"items": items, "total": len(items), "trade_date": str(day)}


@ttl_cache(60)
def index_series(ts_code: str, days: int) -> dict:
    """单指数日线（升序，供 K 线/面积图直接消费）。"""
    day = mkt.latest_trade_date()
    rows = query(
        """
        SELECT trade_date, open, high, low, close, volume
        FROM index_quotes WHERE ts_code = %s
        ORDER BY trade_date DESC LIMIT %s
        """,
        (ts_code, days),
    )
    items = _rows(reversed(rows))
    return {"items": items, "total": len(items), "ts_code": ts_code,
            "trade_date": str(day) if day else None}


# --------------------------------------------------------------------------
# 指数成分（降级：按总市值近似）
# --------------------------------------------------------------------------

# 本地没有官方成分表（index_components 表不存在），降级为「最新交易日按总市值
# 取前 N 只」当权重股清单，并在 meta.approx 明示。市值优先取 stock_fundamentals
# （若未来补齐），为空时回落到 daily_quotes.total_market_cap（当前 1500 只有值）。
_COMPONENTS_SQL = """
WITH base AS (
  SELECT d.ts_code, s.stock_name, d.pct_chg,
         COALESCE(f.total_market_cap, d.total_market_cap) AS market_cap
  FROM daily_quotes d
  LEFT JOIN stock_basic_info s ON s.ts_code = d.ts_code
  LEFT JOIN stock_fundamentals f ON f.ts_code = d.ts_code
  WHERE d.trade_date = (SELECT MAX(trade_date) FROM daily_quotes)
), topn AS (
  SELECT ts_code, stock_name, pct_chg, market_cap,
         ROW_NUMBER() OVER (ORDER BY market_cap DESC NULLS LAST) AS rank_no
  FROM base
  WHERE market_cap IS NOT NULL
), picked AS (
  -- 「前 N 只」集合先定下来，权重与总数都以这个集合为分母
  SELECT * FROM topn WHERE rank_no <= %s
)
SELECT ts_code, stock_name, pct_chg, market_cap, rank_no,
       SUM(market_cap) OVER () AS total_cap,
       COUNT(*)        OVER () AS universe
FROM picked
WHERE rank_no > %s
ORDER BY rank_no
LIMIT %s
"""


@ttl_cache(30)
def index_components(ts_code: str, page: int, size: int, top: int) -> dict:
    """指数成分股（近似）：按总市值降序取前 N 只 + 归一化权重。

    ``rank_no <= top`` 圈定「前 N 只」集合，``rank_no > offset`` 完成分页；
    ``universe`` / ``total_cap`` 用窗口函数在同一条 SQL 里带出，不额外发 COUNT。
    """
    day = mkt.latest_trade_date()
    offset = (page - 1) * size
    rows = query(_COMPONENTS_SQL, (top, offset, size))
    if rows:
        universe = int(rows[0]["universe"])
        total = universe
    else:
        # 翻页越界或整表没有市值数据：退化成一次轻量计数，保证 total 语义正确
        n = int(
            query(
                """
                SELECT COUNT(*) AS c FROM daily_quotes
                WHERE trade_date = (SELECT MAX(trade_date) FROM daily_quotes)
                  AND total_market_cap IS NOT NULL
                """
            )[0]["c"]
            or 0
        )
        total = min(n, top)
        universe = total

    items = []
    for r in rows:
        cap = _f(r["market_cap"])
        sum_cap = _f(r["total_cap"])
        items.append(
            {
                "ts_code": r["ts_code"],
                "stock_name": r["stock_name"],
                "market_cap": cap,
                "pct_chg": _pct(r["pct_chg"]),
                # 权重按「所选前 N 只」的市值总和归一，用百分数表达（与涨跌幅口径一致）
                "weight": round(cap / sum_cap * 100, 4) if (cap and sum_cap) else None,
            }
        )
    return {"items": items, "total": total, "page": page, "size": size,
            "ts_code": ts_code, "meta": _components_meta(day, universe)}


def _components_meta(day, universe: int) -> dict:
    return {
        "approx": True,
        "note": "成分股权重按总市值近似排序，本地暂无官方成分表",
        "universe": universe,
        "trade_date": str(day) if day else None,
    }


# --------------------------------------------------------------------------
# 财报三表 / 关键指标
# --------------------------------------------------------------------------

_STATEMENT_COLS = """
report_date, revenue, net_profit, net_profit_deducted, gross_margin, net_margin,
total_assets, total_liabilities, equity, debt_ratio, operating_cashflow
"""


@ttl_cache(60)
def statements(ts_code: str | None, limit: int) -> dict:
    """财报数据：按 report_date 倒序返回 N 期（利润表 + 资产负债表 + 现金流）。"""
    code = normalize_code(ts_code)
    if not code:
        return {"items": [], "total": 0, "ts_code": None,
                "meta": {"degraded": True, "note": "缺少 ts_code，无法定位财报"}}
    rows = query(
        f"""
        SELECT {_STATEMENT_COLS}, COUNT(*) OVER () AS total
        FROM stock_fundamentals
        WHERE ts_code = %s
        ORDER BY report_date DESC
        LIMIT %s
        """,
        (code, limit),
    )
    if not rows:
        return {"items": [], "total": 0, "ts_code": code,
                "meta": {"degraded": True, "note": "本地暂无该标的财报数据"}}
    total = int(rows[0].get("total") or len(rows))
    items = _rows(rows)
    for it in items:
        it.pop("total", None)
    return {"items": items, "total": total, "ts_code": code}


# 单票指标里要出的序列（数组长度与 report_date 对齐）
_SERIES_KEYS = (
    "revenue", "net_profit", "gross_margin", "eps", "roe",
    "debt_ratio", "revenue_yoy", "profit_yoy",
)

# 全市场指标摘要的排序白名单（白名单 SQL 片段，绝不拼用户输入）
_IND_SORTS = {
    "report_date": "f.report_date",
    "revenue": "f.revenue",
    "net_profit": "f.net_profit",
    "gross_margin": "f.gross_margin",
    "net_margin": "f.net_margin",
    "eps": "f.eps",
    "bps": "f.bps",
    "debt_ratio": "f.debt_ratio",
    "operating_cashflow": "f.operating_cashflow",
    "shareholder_count": "f.shareholder_count",
    "pledge_ratio": "f.pledge_ratio",
}


def _roe(net_profit, equity):
    """ROE = 归属净利润 / 所有者权益 * 100（在 Python 里算，权益为 NULL 时给 NULL）。"""
    np_ = _f(net_profit)
    eq = _f(equity)
    if np_ is None or not eq:
        return None
    return round(np_ / eq * 100, 2)


def indicators(ts_code: str | None = None, page: int = 1, size: int = 20,
               sort: str = "net_profit", order: str = "desc") -> dict:
    """关键指标卡。

    * 传 ``ts_code``：单票，返回最新一期指标 + 按 report_date 对齐的历史序列；
    * 不传：全市场（293 只）指标摘要，服务端分页 + 服务端排序。
    """
    if ts_code:
        return _single_indicators(ts_code)
    return _all_indicators(page, size, sort, order)


@ttl_cache(60)
def _single_indicators(ts_code: str) -> dict:
    code = normalize_code(ts_code)
    empty_series = {k: [] for k in _SERIES_KEYS}
    empty_series["report_date"] = []
    if not code:
        return {"latest": {}, "series": empty_series, "ts_code": None,
                "meta": {"degraded": True, "note": "缺少 ts_code"}}
    rows = query(
        """
        SELECT f.*, s.stock_name
        FROM stock_fundamentals f
        LEFT JOIN stock_basic_info s ON s.ts_code = f.ts_code
        WHERE f.ts_code = %s
        ORDER BY f.report_date DESC
        """,
        (code,),
    )
    if not rows:
        return {"latest": {}, "series": empty_series, "ts_code": code,
                "meta": {"degraded": True, "note": "本地暂无该标的财务指标"}}

    rows.sort(key=lambda r: r["report_date"] or date.min, reverse=True)
    series = {"report_date": [str(_d(r["report_date"])) for r in rows]}
    for key in _SERIES_KEYS:
        if key == "roe":
            series[key] = [_roe(r.get("net_profit"), r.get("equity")) for r in rows]
        else:
            series[key] = [_ser(r.get(key)) for r in rows]

    top = rows[0]
    latest = {
        "ts_code": code,
        "stock_name": top.get("stock_name"),
        "report_date": str(_d(top.get("report_date"))),
        "revenue": _ser(top.get("revenue")),
        "net_profit": _ser(top.get("net_profit")),
        "net_profit_deducted": _ser(top.get("net_profit_deducted")),
        "gross_margin": _f(top.get("gross_margin")),
        "net_margin": _f(top.get("net_margin")),
        "total_assets": _ser(top.get("total_assets")),
        "total_liabilities": _ser(top.get("total_liabilities")),
        "equity": _ser(top.get("equity")),
        "debt_ratio": _f(top.get("debt_ratio")),
        "operating_cashflow": _ser(top.get("operating_cashflow")),
        "eps": _ser(top.get("eps")),
        "bps": _ser(top.get("bps")),
        "roe": _roe(top.get("net_profit"), top.get("equity")),
        "revenue_yoy": _f(top.get("revenue_yoy")),
        "profit_yoy": _f(top.get("profit_yoy")),
        "shareholder_count": top.get("shareholder_count"),
        "pledge_ratio": _f(top.get("pledge_ratio")),
    }

    # 估值指标财务表里大多是空的，用当日行情补 PE/PB/市值，页面才有可看的数
    q = query(
        """
        SELECT trade_date, close, pct_chg, pe_ratio, pb_ratio,
               total_market_cap, circulating_market_cap
        FROM daily_quotes WHERE ts_code = %s
        ORDER BY trade_date DESC LIMIT 1
        """,
        (code,),
    )
    if q:
        qq = q[0]
        latest["valuation"] = {
            "trade_date": str(_d(qq.get("trade_date"))),
            "close": _f(qq.get("close")),
            "pct_chg": _f(qq.get("pct_chg")),
            "pe_ratio": _f(qq.get("pe_ratio")),
            "pb_ratio": _f(qq.get("pb_ratio")),
            "total_market_cap": _ser(qq.get("total_market_cap")),
            "circulating_market_cap": _ser(qq.get("circulating_market_cap")),
        }
        latest["pe_ratio"] = latest["valuation"]["pe_ratio"]
        latest["pb_ratio"] = latest["valuation"]["pb_ratio"]
        latest["total_market_cap"] = latest["valuation"]["total_market_cap"]
    return {"latest": latest, "series": series, "ts_code": code}


def _all_indicators(page: int, size: int, sort: str, order: str) -> dict:
    order_sql = "ASC" if str(order).lower() == "asc" else "DESC"
    order_col = _IND_SORTS.get(sort, _IND_SORTS["net_profit"])
    rows = query(
        f"""
        SELECT f.ts_code, s.stock_name, f.report_date, f.revenue, f.net_profit,
               f.gross_margin, f.net_margin, f.eps, f.bps, f.debt_ratio,
               f.revenue_yoy, f.profit_yoy, f.equity, f.operating_cashflow,
               f.shareholder_count, f.pledge_ratio,
               COUNT(*) OVER () AS total
        FROM stock_fundamentals f
        LEFT JOIN stock_basic_info s ON s.ts_code = f.ts_code
        ORDER BY {order_col} {order_sql} NULLS LAST, f.ts_code
        LIMIT %s OFFSET %s
        """,
        (size, (page - 1) * size),
    )
    total = int(rows[0]["total"]) if rows else int(
        query("SELECT COUNT(*) AS c FROM stock_fundamentals")[0]["c"] or 0
    )
    items = []
    for r in rows:
        it = _row(r)
        it.pop("total", None)
        it["roe"] = _roe(r.get("net_profit"), r.get("equity"))
        items.append(it)
    return {"items": items, "total": total, "page": page, "size": size}


# --------------------------------------------------------------------------
# 市场环境（复用 market 服务） + 环境历史
# --------------------------------------------------------------------------

_ENV_BANDS = [
    (70, "强势"),
    (58, "偏强"),
    (45, "中性"),
    (32, "偏弱"),
    (0, "弱势"),
]


@ttl_cache(60)
def market_env() -> dict:
    """市场环境一屏：指数 + 广度 + 情绪 + 成交额序列 + 中文要点。

    内部依赖 market 服务的 breadth/sentiment/limit_ladder（各自也有 120s TTL），
    这里再包一层 60s 缓存：冷调用要串起 5~6 条聚合 SQL（实测最慢 17s），
    缓存后盘中轮询几乎零成本。
    """
    day = mkt.latest_trade_date()
    if not day:
        return {"trade_date": None, "indexes": [], "breadth": {}, "sentiment": {},
                "amount_trend": [], "env_level": None, "notes": []}

    indexes = mkt.index_overview(day)
    breadth = mkt.breadth(day)
    sentiment = mkt.sentiment(day) or {}
    # 成交额序列在 amount_trend 里已经给出，情绪对象里那份是重复 payload，去掉
    sentiment.pop("amount_series", None)
    amount_trend = [
        {"trade_date": a.get("date"), "amount": a.get("amount")}
        for a in mkt.amount_series(day, 21)
    ]

    score = _f(sentiment.get("score"))
    up_ratio = _f(breadth.get("up_ratio"), 0) or 0
    # 环境分 = 情绪综合分 60% + 上涨广度 40%：情绪偏领先、广度偏同步，
    # 加权后比单看情绪更贴近「今天好不好做」的直觉。
    env_score = round((score or 0) * 0.6 + up_ratio * 100 * 0.4, 1)
    env_level = next(label for threshold, label in _ENV_BANDS if env_score >= threshold)

    notes = _env_notes(indexes, breadth, sentiment)
    return {
        "trade_date": str(day),
        "indexes": indexes,
        "breadth": breadth,
        "sentiment": sentiment,
        "amount_trend": amount_trend,
        "env_level": env_level,
        "env_score": env_score,
        "notes": notes,
    }


def _env_notes(indexes: list[dict], breadth: dict, sentiment: dict) -> list[str]:
    """把一屏数据翻译成 3~5 句中文要点（前端直接展示，省得前端拼文案）。"""
    notes: list[str] = []
    up_ix = [i for i in indexes if (i.get("pct_chg") or 0) > 0]
    down_ix = [i for i in indexes if (i.get("pct_chg") or 0) < 0]
    sh = next((i for i in indexes if i["ts_code"] == "000001.SH"), None)
    if sh and sh.get("close") is not None:
        sign = "+" if (sh.get("pct_chg") or 0) >= 0 else ""
        notes.append(
            f"上证指数收 {sh['close']:.2f} 点，{sign}{sh.get('pct_chg') or 0:.2f}%；"
            f"6 大指数 {len(up_ix)} 涨 {len(down_ix)} 跌。"
        )
    elif indexes:
        notes.append(f"纳入监控的 {len(indexes)} 个指数中 {len(up_ix)} 涨 {len(down_ix)} 跟。")

    amount = _f(breadth.get("amount"), 0) or 0
    avg20 = _f(sentiment.get("amount_avg20"), 0) or 0
    if amount and avg20:
        ratio = amount / avg20
        verb = "放量" if ratio >= 1 else "缩量"
        notes.append(
            f"两市成交 {mkt.amount_yi(amount)}，较 20 日均额{verb} {abs(ratio - 1) * 100:.0f}%。"
        )
    elif amount:
        notes.append(f"两市成交 {mkt.amount_yi(amount)}。")

    if breadth.get("total"):
        seal = breadth.get("seal_rate")
        notes.append(
            f"全市场上涨 {breadth.get('up', 0)} 家、下跌 {breadth.get('down', 0)} 家，"
            f"涨停 {breadth.get('limit_up', 0)} 家、跌停 {breadth.get('limit_down', 0)} 家"
            + (f"，封板率 {seal * 100:.0f}%。" if seal is not None else "。")
        )

    dims = sentiment.get("dims") or []
    if dims and sentiment.get("score") is not None:
        best = max(dims, key=lambda d: d.get("value") or 0)
        worst = min(dims, key=lambda d: d.get("value") or 0)
        notes.append(
            f"情绪综合分 {sentiment['score']}（{sentiment.get('level') or '—'}）："
            f"最强维度「{best['label']}」{best.get('value')} 分，"
            f"最弱「{worst['label']}」{worst.get('value')} 分。"
        )

    trend = sentiment.get("trend") or {}
    if trend.get("ma20_ratio") is not None:
        notes.append(
            f"站上 MA20 的个股占 {trend['ma20_ratio'] * 100:.0f}%，"
            f"60 日新高 {trend.get('new_high', 0)} 家 / 新低 {trend.get('new_low', 0)} 家。"
        )
    return notes


# 按「交易日」而不是自然日截取：180 个交易日 ≈ 260 个自然日，若用自然日会把
# 前端选的「近 180 日」悄悄变成 120 天。日期清单走 trade_calendar（574 行小表）。
_ENV_HISTORY_MAX_DAYS = 250
_ENV_HISTORY_DEFAULT_DAYS = 180

# 复用 market 服务的涨跌停口径 CTE（ST 5% / 创业科创板 20% / 北交所 30% / 其余 10%），
# 保证这里的涨停家数与连板梯队、广度直方图完全一致。
_ENV_HISTORY_SQL = mkt._FX_CTE.format(where="dq.trade_date = ANY(%s)") + """
, agg AS (
  SELECT trade_date,
         COUNT(*) FILTER (WHERE pct_chg IS NOT NULL) AS total,
         COUNT(*) FILTER (WHERE pct_chg > 0)         AS up,
         COUNT(*) FILTER (WHERE pct_chg < 0)         AS down,
         COUNT(*) FILTER (WHERE is_lu)               AS limit_up,
         COALESCE(SUM(amount), 0)                    AS amount
  FROM fx
  GROUP BY trade_date
)
SELECT a.trade_date, a.total, a.up, a.down, a.limit_up, a.amount,
       s.score, s.mood
FROM agg a
LEFT JOIN app_sentiment_history s ON s.trade_date = a.trade_date
ORDER BY a.trade_date DESC
"""


@ttl_cache(120)
def market_env_history(days: int = _ENV_HISTORY_DEFAULT_DAYS) -> dict:
    """环境历史：广度/涨停/成交额逐日聚合，情绪 score/mood 直接读表（缺失为 null）。"""
    requested = int(days or _ENV_HISTORY_DEFAULT_DAYS)
    effective = min(max(requested, 20), _ENV_HISTORY_MAX_DAYS)
    started = time.time()
    dates = mkt.trade_dates(effective)
    rows = query(_ENV_HISTORY_SQL, (dates,)) if dates else []
    elapsed_ms = int((time.time() - started) * 1000)

    items = [
        {
            "trade_date": str(_d(r["trade_date"])),
            "up": int(r["up"] or 0),
            "down": int(r["down"] or 0),
            "limit_up": int(r["limit_up"] or 0),
            "amount": _f(r["amount"], 0) or 0,
            "score": _f(r.get("score")),
            "mood": r.get("mood"),
        }
        for r in rows
    ]
    meta = {
        "days": effective,
        "requested_days": requested,
        "rows": len(items),
        "elapsed_ms": elapsed_ms,
        "sentiment_source": "app_sentiment_history",
    }
    if effective < requested:
        meta["degraded"] = True
        meta["note"] = (
            f"请求 {requested} 个交易日，为控制全市场逐日聚合耗时，已截断到 {effective} 个交易日"
        )
    else:
        meta["degraded"] = False
        meta["note"] = "情绪 score/mood 读 app_sentiment_history，缺失即为 null"
    return {"items": items, "total": len(items), "meta": meta}


# --------------------------------------------------------------------------
# 全市场数据表（服务端分页 + 服务端排序）
# --------------------------------------------------------------------------

QUOTE_COLUMNS = [
    {"key": "ts_code", "label": "代码"},
    {"key": "stock_name", "label": "名称"},
    {"key": "market", "label": "市场"},
    {"key": "trade_date", "label": "交易日"},
    {"key": "open", "label": "开盘"},
    {"key": "high", "label": "最高"},
    {"key": "low", "label": "最低"},
    {"key": "close", "label": "收盘"},
    {"key": "pct_chg", "label": "涨跌幅(%)"},
    {"key": "volume", "label": "成交量(手)"},
    {"key": "amount", "label": "成交额(元)"},
    {"key": "turnover_rate", "label": "换手率(%)"},
    {"key": "total_market_cap", "label": "总市值(元)"},
]

# 排序白名单：只允许这些列进入 ORDER BY（防 SQL 注入，也防前端传任意表达式）
_QUOTE_SORTS = {
    "ts_code": "ts_code",
    "stock_name": "stock_name",
    "market": "market",
    "trade_date": "trade_date",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "pct_chg": "pct_chg",
    "volume": "volume",
    "amount": "amount",
    "turnover_rate": "turnover_rate",
    "total_market_cap": "total_market_cap",
}

_FUND_COLUMNS = [
    {"key": "ts_code", "label": "代码"},
    {"key": "stock_name", "label": "名称"},
    {"key": "report_date", "label": "报告期"},
    {"key": "revenue", "label": "营业收入(元)"},
    {"key": "net_profit", "label": "净利润(元)"},
    {"key": "gross_margin", "label": "毛利率(%)"},
    {"key": "net_margin", "label": "净利率(%)"},
    {"key": "debt_ratio", "label": "资产负债率(%)"},
    {"key": "operating_cashflow", "label": "经营现金流(元)"},
    {"key": "eps", "label": "EPS(元)"},
    {"key": "bps", "label": "BPS(元)"},
    {"key": "shareholder_count", "label": "股东户数"},
    {"key": "pledge_ratio", "label": "质押比例(%)"},
]


def data_quotes(page: int, size: int, market: str | None = None,
                sort: str = "pct_chg", order: str = "desc",
                keyword: str | None = None) -> dict:
    """全市场行情表：LIMIT/OFFSET + ORDER BY 全部在 SQL 里，单次最多 200 行。"""
    day = mkt.latest_trade_date()
    order_sql = "ASC" if str(order).lower() == "asc" else "DESC"
    order_col = _QUOTE_SORTS.get(sort, "pct_chg")

    kw = (keyword or "").strip()
    mkt_code = (market or "").strip().upper()
    mkt_ok = mkt_code in {"SH", "SZ", "BJ"}

    where_market = "market = %s" if mkt_ok else "TRUE"
    where_kw = "(ts_code ILIKE %s OR stock_name ILIKE %s)" if kw else "TRUE"

    params: list = []
    if mkt_ok:
        params.append(mkt_code)
    if kw:
        params += [f"%{kw}%", f"%{kw}%"]
    params += [size, (page - 1) * size]

    rows = query(
        f"""
        WITH base AS (
          SELECT d.ts_code, s.stock_name, s.market, d.trade_date,
                 d.open, d.high, d.low, d.close, d.pct_chg,
                 d.volume, d.amount, d.turnover_rate, d.total_market_cap
          FROM daily_quotes d
          LEFT JOIN stock_basic_info s ON s.ts_code = d.ts_code
          WHERE d.trade_date = (SELECT MAX(trade_date) FROM daily_quotes)
        )
        SELECT *, COUNT(*) OVER () AS total
        FROM base
        WHERE {where_market} AND {where_kw}
        ORDER BY {order_col} {order_sql} NULLS LAST, ts_code
        LIMIT %s OFFSET %s
        """,
        tuple(params),
    )
    if rows:
        total = int(rows[0].get("total") or 0)
    else:
        total = 0
    items = []
    for r in rows:
        it = _row(r)
        it.pop("total", None)
        items.append(it)
    return {"items": items, "total": total, "page": page, "size": size,
            "trade_date": str(day) if day else None, "columns": QUOTE_COLUMNS}


def data_fundamentals(page: int, size: int, sort: str = "net_profit",
                      order: str = "desc", keyword: str | None = None) -> dict:
    """全市场基本面表：stock_fundamentals（293 只）服务端分页排序。"""
    order_sql = "ASC" if str(order).lower() == "asc" else "DESC"
    order_col = _IND_SORTS.get(sort, _IND_SORTS["net_profit"])
    kw = (keyword or "").strip()
    where_kw = "(f.ts_code ILIKE %s OR s.stock_name ILIKE %s)" if kw else "TRUE"
    params: list = []
    if kw:
        params += [f"%{kw}%", f"%{kw}%"]
    params += [size, (page - 1) * size]

    rows = query(
        f"""
        SELECT f.ts_code, s.stock_name, f.report_date, f.revenue, f.net_profit,
               f.net_profit_deducted, f.gross_margin, f.net_margin,
               f.total_assets, f.total_liabilities, f.equity, f.debt_ratio,
               f.operating_cashflow, f.eps, f.bps, f.revenue_yoy, f.profit_yoy,
               f.industry, f.shareholder_count, f.pledge_ratio,
               COUNT(*) OVER () AS total
        FROM stock_fundamentals f
        LEFT JOIN stock_basic_info s ON s.ts_code = f.ts_code
        WHERE {where_kw}
        ORDER BY {order_col} {order_sql} NULLS LAST, f.ts_code
        LIMIT %s OFFSET %s
        """,
        tuple(params),
    )
    total = int(rows[0]["total"]) if rows else int(
        query("SELECT COUNT(*) AS c FROM stock_fundamentals")[0]["c"] or 0
    )
    items = []
    for r in rows:
        it = _row(r)
        it.pop("total", None)
        it["roe"] = _roe(r.get("net_profit"), r.get("equity"))
        items.append(it)
    return {"items": items, "total": total, "page": page, "size": size,
            "columns": _FUND_COLUMNS}


# --------------------------------------------------------------------------
# 个股档案
# --------------------------------------------------------------------------

# 单条 SQL 取档案：stock_basic_info 为准（必须存在，否则 404），
# 行情取该股最新一天（走 (ts_code, trade_date) 主键倒序，代价 O(log n)），
# industry 只能从 stock_fundamentals 取（stock_basic_info 没有这一列，本地为 NULL）。
_SECURITY_SQL = """
SELECT sb.ts_code, sb.stock_name, sb.market, sb.list_date, sb.is_st,
       f.industry,
       d.trade_date, d.close, d.pct_chg, d.volume, d.amount, d.turnover_rate,
       d.pe_ratio, d.pb_ratio, d.total_market_cap,
       d.limit_up_price, d.limit_down_price
FROM stock_basic_info sb
LEFT JOIN stock_fundamentals f ON f.ts_code = sb.ts_code
LEFT JOIN LATERAL (
  SELECT trade_date, close, pct_chg, volume, amount, turnover_rate,
         pe_ratio, pb_ratio, total_market_cap, limit_up_price, limit_down_price
  FROM daily_quotes
  WHERE ts_code = sb.ts_code
  ORDER BY trade_date DESC
  LIMIT 1
) d ON true
WHERE sb.ts_code = %s
"""


@ttl_cache(60)
def security_profile(code: str) -> dict | None:
    """个股档案；查不到（不在 stock_basic_info）返回 None，由路由转 404。"""
    ts_code = normalize_code(code)
    if not ts_code:
        return None
    rows = query(_SECURITY_SQL, (ts_code,))
    if not rows:
        return None
    r = rows[0]
    return {
        "ts_code": r["ts_code"],
        "name": r.get("stock_name"),
        "market": r.get("market"),
        "list_date": str(_d(r.get("list_date"))) if r.get("list_date") else None,
        "is_st": bool(r.get("is_st")),
        "industry": r.get("industry"),
        "latest": {
            "trade_date": str(_d(r.get("trade_date"))) if r.get("trade_date") else None,
            "close": _f(r.get("close")),
            "pct_chg": _f(r.get("pct_chg")),
            "volume": _f(r.get("volume")),
            "amount": _f(r.get("amount")),
            "turnover_rate": _f(r.get("turnover_rate")),
            "pe_ratio": _f(r.get("pe_ratio")),
            "pb_ratio": _f(r.get("pb_ratio")),
            "total_market_cap": _f(r.get("total_market_cap")),
            "limit_up_price": _f(r.get("limit_up_price")),
            "limit_down_price": _f(r.get("limit_down_price")),
        },
    }

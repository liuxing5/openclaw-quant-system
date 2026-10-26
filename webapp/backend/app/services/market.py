# -*- coding: utf-8 -*-
"""全市场聚合服务（SQL 下推版）。

性能约束（实测）：本机到远端 PostgreSQL 的链路只有约 40~90 KB/s，
取回 5219 行 × 13 列需要 ~20 秒，而同一条查询在数据库端仅 2.3 毫秒。

因此本模块的铁律：
  1. 聚合、排序、分桶、窗口计算全部在 SQL 里完成；
  2. 每个函数返回的结果集不超过几百行、体积不超过几十 KB；
  3. 热点结果统一走进程内 TTL 缓存，盘中 30~60 秒刷新一次。

全部基于本地 PostgreSQL（daily_quotes / index_quotes / *_board_quotes），
不依赖任何外部实时接口，因此盘后与历史日期都能复现。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from ..cache import ttl_cache
from ..db import query, scalar

# 情绪分档（与总台展示口径一致）
SENTIMENT_BANDS = [
    (75, "亢奋", "情绪过热，警惕高位股分歧与切换"),
    (60, "活跃", "赚钱效应尚可，可跟随强势主线"),
    (45, "温和", "结构性行情，控制仓位精选个股"),
    (30, "低迷", "赚钱效应偏弱，等待情绪修复"),
    (0, "冰点", "极致缩量或恐慌，留意超跌反弹"),
]

INDEX_LIST = [
    ("000001.SH", "上证指数"),
    ("399001.SZ", "深证成指"),
    ("399006.SZ", "创业板指"),
    ("000300.SH", "沪深300"),
    ("000905.SH", "中证500"),
    ("000688.SH", "科创50"),
]

BREADTH_LABELS = [
    "涨停", "涨7~10%", "涨5~7%", "涨3~5%", "涨0~3%",
    "平盘", "跌0~3%", "跌3~5%", "跌5~7%", "跌7~10%", "跌停",
]

# 涨跌停兜底口径见 _FX_CTE；此处保留板块判断说明供前端/文档引用。
LIMIT_PCT_HINT = "ST 5% / 创业板·科创板 20% / 北交所 30% / 其余 10%"


def _f(v, default=None):
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _d(v):
    if v is None:
        return None
    return v.date() if hasattr(v, "date") else v


# --------------------------------------------------------------------------
# 基础：交易日 / 元数据
# --------------------------------------------------------------------------


@ttl_cache(30)
def latest_trade_date() -> date | None:
    return _d(scalar("SELECT MAX(trade_date) FROM daily_quotes"))


def trade_dates(limit: int = 120) -> list[date]:
    """交易日列表：走 trade_calendar 小表（500 行），避免扫 200 万行行情表。"""
    from .calendar import trade_dates as _cal_dates

    return _cal_dates(limit)


@ttl_cache(120)
def _stock_names(codes: list[str]) -> dict[str, str]:
    if not codes:
        return {}
    rows = query(
        "SELECT ts_code, stock_name FROM stock_basic_info WHERE ts_code = ANY(%s)",
        (codes,),
    )
    return {r["ts_code"]: r["stock_name"] for r in rows}


@ttl_cache(300)
def _boards(codes: list[str]) -> dict[str, str]:
    out = {}
    for c in codes:
        pure = c.split(".")[0]
        if pure.startswith(("688", "689")):
            out[c] = "科创板"
        elif pure.startswith(("300", "301", "302")):
            out[c] = "创业板"
        elif pure.startswith(("8", "4", "920")):
            out[c] = "北交所"
        else:
            out[c] = "A主板"
    return out


# --------------------------------------------------------------------------
# 涨跌广度 + 分布直方图（SQL 分桶，只回传 12 行）
# --------------------------------------------------------------------------

# 行情 + 涨跌停判定的公共 CTE，广度 / 分布 / 涨停梯队三处共用同一口径。
# 实测：全市场 5219 只里有 3719 只的交易所涨跌停价为空，因此统一用
# COALESCE(交易所价, 前收 × 涨跌幅) 兜底，并把 ST 股按 5% 计算。
# 这是一个「前缀」，调用方在其后追加自己的 CTE 或 SELECT。
_FX_CTE = """
WITH q AS (
  SELECT dq.ts_code, dq.trade_date, dq.pct_chg, dq.close, dq.high, dq.amount,
         dq.turnover_rate, dq.volume, dq.volume_ratio, dq.pre_close,
         dq.limit_up_price, dq.limit_down_price,
         CASE WHEN strpos(upper(COALESCE(sb.stock_name, '')), 'ST') > 0 THEN 5.0
              WHEN dq.ts_code ~ '^(300|301|688|689)' THEN 20.0
              WHEN dq.ts_code ~ '^(4|8|920)' THEN 30.0
              ELSE 10.0 END AS thr
  FROM daily_quotes dq
  LEFT JOIN stock_basic_info sb ON sb.ts_code = dq.ts_code
  WHERE {where}
), f AS (
  SELECT ts_code, trade_date, pct_chg, close, high, amount, turnover_rate, volume,
         volume_ratio,
         COALESCE(limit_up_price,
                  ROUND((pre_close * (1 + thr / 100.0))::numeric, 2)) AS lup,
         COALESCE(limit_down_price,
                  ROUND((pre_close * (1 - thr / 100.0))::numeric, 2)) AS ldp
  FROM q
), fx AS (
  SELECT f.*,
    (close IS NOT NULL AND lup IS NOT NULL AND abs(close - lup) < 0.005) AS is_lu,
    (close IS NOT NULL AND ldp IS NOT NULL AND abs(close - ldp) < 0.005) AS is_ld,
    (high  IS NOT NULL AND lup IS NOT NULL AND high >= lup - 0.005)      AS hit
  FROM f
)
"""

_BREADTH_SQL = _FX_CTE.format(where="dq.trade_date = %s") + """
SELECT
  COUNT(*)                                                        AS symbols,
  COUNT(*) FILTER (WHERE pct_chg IS NOT NULL)                     AS total,
  COUNT(*) FILTER (WHERE pct_chg > 0)                             AS up,
  COUNT(*) FILTER (WHERE pct_chg = 0)                             AS flat,
  COUNT(*) FILTER (WHERE pct_chg < 0)                             AS down,
  COUNT(*) FILTER (WHERE is_lu)                                   AS limit_up,
  COUNT(*) FILTER (WHERE is_ld)                                   AS limit_down,
  COUNT(*) FILTER (WHERE hit AND NOT is_lu)                       AS broken,
  COUNT(*) FILTER (WHERE hit OR is_lu)                            AS touched,
  COALESCE(SUM(amount) FILTER (WHERE pct_chg IS NOT NULL), 0)     AS amount
FROM fx
"""

_HIST_SQL = _FX_CTE.format(where="dq.trade_date = %s") + """,
b AS (
  SELECT CASE
    WHEN is_lu THEN 0
    WHEN is_ld THEN 10
    WHEN pct_chg >= 7 THEN 1
    WHEN pct_chg >= 5 THEN 2
    WHEN pct_chg >= 3 THEN 3
    WHEN pct_chg > 0 THEN 4
    WHEN pct_chg <= -7 THEN 9
    WHEN pct_chg <= -5 THEN 8
    WHEN pct_chg <= -3 THEN 7
    WHEN pct_chg < 0 THEN 6
    ELSE 5 END AS idx
  FROM fx WHERE pct_chg IS NOT NULL
), c AS (SELECT idx, COUNT(*) AS n FROM b GROUP BY idx)
SELECT s.idx AS idx, COALESCE(c.n, 0) AS n
FROM (SELECT generate_series(0, 10) AS idx) s
LEFT JOIN c ON c.idx = s.idx
ORDER BY s.idx
"""


@ttl_cache(120)
def breadth(day: date | None = None) -> dict:
    """涨跌广度 + 涨跌停 + 成交额 + 分布直方图（两条轻量 SQL）。"""
    day = day or latest_trade_date()
    if not day:
        return {}
    base = query(_BREADTH_SQL, (day,))
    row = base[0] if base else {}
    hist = query(_HIST_SQL, (day,))
    histogram = [
        {"label": BREADTH_LABELS[r["idx"]], "count": int(r["n"] or 0)}
        for r in hist
        if 0 <= r["idx"] < len(BREADTH_LABELS)
    ]

    total = int(row.get("total") or 0)
    touched = int(row.get("touched") or 0)
    seal_rate = (int(row.get("limit_up") or 0) / touched) if touched else None
    up = int(row.get("up") or 0)
    return {
        "trade_date": str(day),
        "symbols": int(row.get("symbols") or 0),
        "total": total,
        "up": up,
        "flat": int(row.get("flat") or 0),
        "down": int(row.get("down") or 0),
        "limit_up": int(row.get("limit_up") or 0),
        "limit_down": int(row.get("limit_down") or 0),
        "broken": int(row.get("broken") or 0),
        "touched": touched,
        "seal_rate": round(seal_rate, 4) if seal_rate is not None else None,
        "amount": _f(row.get("amount"), 0) or 0,
        "up_ratio": round(up / total, 4) if total else None,
        "histogram": histogram,
    }


# --------------------------------------------------------------------------
# 均线 / 新高新低（窗口函数后聚合，只回传 1 行）
# --------------------------------------------------------------------------

_TREND_SQL = """
WITH bounds AS (
  SELECT MIN(trade_date) AS start_dt FROM (
    SELECT DISTINCT trade_date FROM daily_quotes
    WHERE trade_date <= %s ORDER BY trade_date DESC LIMIT 61
  ) x
), win AS (
  SELECT ts_code, trade_date, close FROM daily_quotes, bounds
  WHERE daily_quotes.trade_date >= bounds.start_dt
    AND daily_quotes.trade_date <= %s
), calc AS (
  SELECT ts_code, trade_date, close,
         AVG(close)  OVER w5  AS ma5,
         AVG(close)  OVER w20 AS ma20,
         AVG(close)  OVER w60 AS ma60,
         MAX(close)  OVER w60 AS max60,
         MIN(close)  OVER w60 AS min60
  FROM win
  WINDOW w5  AS (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 4  PRECEDING AND CURRENT ROW),
         w20 AS (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW),
         w60 AS (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW)
), cur AS (
  SELECT close, ma5, ma20, ma60, max60, min60 FROM calc WHERE trade_date = %s
)
SELECT
  COUNT(*)                                                    AS total,
  COUNT(*) FILTER (WHERE close >= ma5)                        AS ma5_up,
  COUNT(*) FILTER (WHERE close >= ma20)                       AS ma20_up,
  COUNT(*) FILTER (WHERE close >= ma60)                       AS ma60_up,
  COUNT(*) FILTER (WHERE max60 IS NOT NULL AND close >= max60 - 1e-9)  AS new_high,
  COUNT(*) FILTER (WHERE min60 IS NOT NULL AND close <= min60 + 1e-9)  AS new_low
FROM cur
"""


@ttl_cache(120)
def trend_and_newhigh(day: date | None = None) -> dict:
    """均线多头比例 + 60 日新高/新低（SQL 窗口函数，只回传 1 行）。"""
    day = day or latest_trade_date()
    if not day:
        return {}
    base = query(_TREND_SQL, (day, day, day))
    row = base[0] if base else {}
    total = int(row.get("total") or 0)

    def ratio(n):
        return round(n / total, 4) if total else None

    ma5_up = int(row.get("ma5_up") or 0)
    ma20_up = int(row.get("ma20_up") or 0)
    ma60_up = int(row.get("ma60_up") or 0)
    new_high = int(row.get("new_high") or 0)
    new_low = int(row.get("new_low") or 0)
    return {
        "total": total,
        "ma5_up": ma5_up,
        "ma20_up": ma20_up,
        "ma60_up": ma60_up,
        "ma5_ratio": ratio(ma5_up),
        "ma20_ratio": ratio(ma20_up),
        "ma60_ratio": ratio(ma60_up),
        "new_high": new_high,
        "new_low": new_low,
        "hl_ratio": round(new_high / new_low, 4) if new_low else None,
    }


# --------------------------------------------------------------------------
# 成交额序列
# --------------------------------------------------------------------------


@ttl_cache(120)
def amount_series(day: date | None = None, limit: int = 21) -> list[dict]:
    day = day or latest_trade_date()
    if not day:
        return []
    rows = query(
        """
        SELECT trade_date, SUM(amount) AS amount, COUNT(*) AS stocks
        FROM daily_quotes WHERE trade_date <= %s
        GROUP BY trade_date ORDER BY trade_date DESC LIMIT %s
        """,
        (day, limit),
    )
    out = []
    for r in reversed(rows):
        out.append(
            {
                "date": str(_d(r["trade_date"])),
                "amount": _f(r["amount"], 0) or 0,
                "stocks": r["stocks"],
            }
        )
    return out


# --------------------------------------------------------------------------
# 连板梯队（SQL 计算连续封板天数，只回传涨停股）
# --------------------------------------------------------------------------

_LIMIT_UP_CODES_SQL = _FX_CTE.format(where="dq.trade_date = %s") + """
SELECT ts_code FROM fx WHERE is_lu
"""

_STREAK_SQL = _FX_CTE.format(
    where="dq.ts_code = ANY(%s) AND dq.trade_date = ANY(%s)"
) + """,
grp AS (
  SELECT ts_code, trade_date, is_lu AS sealed,
         SUM(CASE WHEN is_lu THEN 0 ELSE 1 END)
           OVER (PARTITION BY ts_code ORDER BY trade_date) AS grp_no
  FROM fx
),
tagged AS (
  SELECT ts_code, grp_no, sealed,
         MAX(grp_no) OVER (PARTITION BY ts_code) AS last_no
  FROM grp
)
SELECT ts_code, COUNT(*) AS streak
FROM tagged
WHERE sealed AND grp_no = last_no
GROUP BY ts_code
"""


@ttl_cache(120)
def limit_up_codes(day: date | None = None) -> list[str]:
    day = day or latest_trade_date()
    if not day:
        return []
    rows = query(_LIMIT_UP_CODES_SQL, (day,))
    return [r["ts_code"] for r in rows]


@ttl_cache(120)
def limit_ladder(day: date | None = None, detail: bool = True) -> dict:
    """连板梯队：SQL 计算截至当日的连续封板天数，再按板高分组。"""
    day = day or latest_trade_date()
    if not day:
        return {"trade_date": None, "tiers": [], "max_streak": 0, "limit_up": 0}

    codes = limit_up_codes(day)
    if not codes:
        return {"trade_date": str(day), "tiers": [], "max_streak": 0, "limit_up": 0}

    dates = trade_dates(120)
    window = [day] + [d for d in dates if d < day][:29]
    streak_rows = query(_STREAK_SQL, (codes, window))
    streaks = {r["ts_code"]: int(r["streak"]) for r in streak_rows}
    streaks = {c: s for c, s in streaks.items() if s >= 1}

    if not streaks:
        return {"trade_date": str(day), "tiers": [], "max_streak": 0, "limit_up": 0}

    stats = _ladder_stats(list(streaks.keys()), day)
    names = _stock_names(list(streaks.keys()))
    boards = _boards(list(streaks.keys()))

    tier_map: dict[int, list[dict]] = {}
    for code, n in streaks.items():
        item = {
            "ts_code": code,
            "name": names.get(code, code.split(".")[0]),
            "board": boards.get(code, ""),
            "streak": n,
            "status": "今日涨停" if n <= 1 else "昨日连板",
        }
        item.update(stats.get(code, {}))
        tier_map.setdefault(n, []).append(item)

    tiers = []
    for n in sorted(tier_map, reverse=True):
        members = sorted(tier_map[n], key=lambda x: -(x.get("pct_chg") or 0))
        tiers.append(
            {
                "streak": n,
                "count": len(members),
                "members": members if detail else members[:8],
            }
        )

    return {
        "trade_date": str(day),
        "tiers": tiers,
        "max_streak": max(tier_map),
        "limit_up": len(streaks),
    }


def _ladder_stats(codes: list[str], day: date) -> dict[str, dict]:
    """涨停股当日行情快照（只取几十行）。"""
    rows = query(
        """
        SELECT ts_code, close, pct_chg, amount, turnover_rate, volume, pre_close
        FROM daily_quotes WHERE trade_date = %s AND ts_code = ANY(%s)
        """,
        (day, codes),
    )
    out = {}
    for r in rows:
        out[r["ts_code"]] = {
            "close": _f(r["close"]),
            "pct_chg": round(_f(r["pct_chg"], 0), 2),
            "amount": _f(r["amount"], 0),
            "turnover_rate": round(_f(r["turnover_rate"], 0) or 0, 2),
            "volume": _f(r["volume"], 0),
        }
    return out


# --------------------------------------------------------------------------
# 榜单（SQL 排序 + LIMIT，只回传 N 行）
# --------------------------------------------------------------------------

_RANK_COLS = {
    "pct_chg_desc": "pct_chg DESC NULLS LAST",
    "pct_chg_asc": "pct_chg ASC NULLS LAST",
    "amount": "amount DESC NULLS LAST",
    "turnover_rate": "turnover_rate DESC NULLS LAST",
}


@ttl_cache(120)
def rankings(day: date | None = None, sort: str = "pct_chg_desc", limit: int = 8) -> list[dict]:
    day = day or latest_trade_date()
    if not day:
        return []
    order = _RANK_COLS.get(sort, _RANK_COLS["pct_chg_desc"])
    rows = query(
        f"""
        SELECT ts_code, close, pct_chg, amount, turnover_rate, volume,
               circulating_market_cap
        FROM daily_quotes
        WHERE trade_date = %s AND pct_chg IS NOT NULL
        ORDER BY {order}
        LIMIT %s
        """,
        (day, limit),
    )
    if not rows:
        return []
    codes = [r["ts_code"] for r in rows]
    names = _stock_names(codes)
    boards = _boards(codes)
    out = []
    for r in rows:
        out.append(
            {
                "ts_code": r["ts_code"],
                "name": names.get(r["ts_code"], r["ts_code"].split(".")[0]),
                "board": boards.get(r["ts_code"], ""),
                "close": _f(r["close"]),
                "pct_chg": round(_f(r["pct_chg"], 0), 2),
                "amount": _f(r["amount"], 0),
                "turnover_rate": round(_f(r["turnover_rate"], 0) or 0, 2),
                "volume": _f(r["volume"], 0),
                "circulating_market_cap": _f(r["circulating_market_cap"]),
            }
        )
    return out


# --------------------------------------------------------------------------
# 概念 / 行业热度
# --------------------------------------------------------------------------


@ttl_cache(300)
def _columns(table: str) -> set[str]:
    rows = query(
        "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
        (table,),
    )
    return {r["column_name"] for r in rows}


@ttl_cache(120)
def board_top(day: date | None = None, board_type: str = "concept", limit: int = 5) -> dict:
    table = "concept_board_quotes" if board_type == "concept" else "industry_board_quotes"
    name_col = "concept_name" if board_type == "concept" else "industry_name"
    code_col = "concept_code" if board_type == "concept" else "industry_code"
    latest = scalar(f"SELECT MAX(trade_date) FROM {table}")
    if not latest:
        return {"gainers": [], "losers": [], "trade_date": None, "total": 0}
    d = _d(latest)
    present = _columns(table)
    wanted = [
        "lead_stock_name", "stock_count", "net_inflow",
        "up_count", "down_count", "amount",
    ]
    select_extra = "".join(f", {c}" for c in wanted if c in present)

    def _page(order: str) -> list[dict]:
        # 只取前 limit 名，避免把整张板块快照（数百行）拉到客户端
        return query(
            f"""
            SELECT {code_col} AS code, {name_col} AS name, pct_chg{select_extra}
            FROM {table} WHERE trade_date = %s AND pct_chg IS NOT NULL
            ORDER BY pct_chg {order}
            LIMIT %s
            """,
            (d, limit),
        )

    gainers = [
        {
            "code": r["code"],
            "name": r["name"],
            "pct_chg": round(_f(r["pct_chg"], 0), 2),
            "stock_count": r.get("stock_count") or 0,
            "lead_stock": (r.get("lead_stock_name") or "").strip(),
            "net_inflow": _f(r.get("net_inflow")),
            "up_count": r.get("up_count"),
            "down_count": r.get("down_count"),
            "amount": _f(r.get("amount")),
            "rank": i + 1,
        }
        for i, r in enumerate(_page("DESC"))
    ]
    n = int(scalar(f"SELECT COUNT(*) FROM {table} WHERE trade_date = %s AND pct_chg IS NOT NULL", (d,)) or 0)
    losers = [
        {
            "code": r["code"],
            "name": r["name"],
            "pct_chg": round(_f(r["pct_chg"], 0), 2),
            "stock_count": r.get("stock_count") or 0,
            "lead_stock": (r.get("lead_stock_name") or "").strip(),
            "net_inflow": _f(r.get("net_inflow")),
            "up_count": r.get("up_count"),
            "down_count": r.get("down_count"),
            "amount": _f(r.get("amount")),
            "rank": n - i,
        }
        for i, r in enumerate(_page("ASC"))
    ]
    return {
        "gainers": gainers,
        "losers": losers,
        "trade_date": str(d),
        "total": n,
    }


# --------------------------------------------------------------------------
# 主要指数
# --------------------------------------------------------------------------


@ttl_cache(120)
def index_overview(day: date | None = None) -> list[dict]:
    day = day or latest_trade_date()
    if not day:
        return []
    since = day - timedelta(days=45)
    rows = query(
        """
        SELECT i.ts_code, i.trade_date, i.open, i.high, i.low, i.close,
               b.name,
               LAG(i.close) OVER (PARTITION BY i.ts_code ORDER BY i.trade_date) AS prev
        FROM index_quotes i
        LEFT JOIN index_basic b ON b.ts_code = i.ts_code
        WHERE i.trade_date >= %s AND i.trade_date <= %s
          AND i.ts_code = ANY(%s)
        ORDER BY i.ts_code, i.trade_date
        """,
        (since, day, [c for c, _ in INDEX_LIST]),
    )
    by_code: dict[str, list[dict]] = {}
    for r in rows:
        by_code.setdefault(r["ts_code"], []).append(r)

    out = []
    for code, name in INDEX_LIST:
        series = by_code.get(code) or []
        if not series:
            continue
        last = series[-1]
        close = _f(last["close"])
        prev = _f(last.get("prev"))
        pct = ((close - prev) / prev * 100) if (prev and close is not None) else None
        out.append(
            {
                "ts_code": code,
                "name": last.get("name") or name,
                "close": close,
                "pct_chg": round(pct, 2) if pct is not None else None,
                "open": _f(last["open"]),
                "high": _f(last["high"]),
                "low": _f(last["low"]),
                "prev_close": prev,
                "curve": [
                    {
                        "date": str(_d(s["trade_date"])),
                        "close": _f(s["close"]),
                    }
                    for s in series[-30:]
                ],
            }
        )
    return out


# --------------------------------------------------------------------------
# 市场情绪
# --------------------------------------------------------------------------


def sentiment(day: date | None = None) -> dict:
    """市场情绪：6 个 0-100 维度 + 综合分 + 分档提示。"""
    day = day or latest_trade_date()
    if not day:
        return {}
    br = breadth(day)
    trend = trend_and_newhigh(day)
    ladder = limit_ladder(day, detail=False)
    amounts = amount_series(day, 21)

    today_amount = amounts[-1]["amount"] if amounts else 0
    prev_amounts = [a["amount"] for a in amounts[:-1]]
    avg20 = sum(prev_amounts) / len(prev_amounts) if prev_amounts else 0

    max_streak = ladder.get("max_streak") or 0
    up_ratio = br.get("up_ratio") or 0
    seal_rate = br.get("seal_rate") or 0
    vol_ratio = (today_amount / avg20) if avg20 else 0
    hl = trend.get("hl_ratio") or 0
    ma5 = trend.get("ma5_ratio") or 0
    ma20 = trend.get("ma20_ratio") or 0

    dims = [
        {
            "key": "trend",
            "label": "趋势强度",
            "value": round(min(100, 60 * ma5 + 40 * ma20), 1),
            "hint": "站上 MA5 / MA20 的个股比例加权",
        },
        {
            "key": "breadth",
            "label": "上涨广度",
            "value": round(100 * up_ratio, 1),
            "hint": "全市场上涨家数占比",
        },
        {
            "key": "seal",
            "label": "涨停效应",
            "value": round(100 * (seal_rate or 0), 1),
            "hint": "触板封住比例（封板率）",
        },
        {
            "key": "streak",
            "label": "连板高度",
            "value": round(min(100, max_streak / 8 * 100), 1),
            "hint": f"最高 {max_streak} 板 / 8 板封顶",
        },
        {
            "key": "momentum",
            "label": "新高动能",
            "value": round(min(100, 100 * hl / 1.0), 1),
            "hint": "60 日新高 / 新低家数比",
        },
        {
            "key": "volume",
            "label": "量能活跃",
            "value": round(min(100, 100 * vol_ratio / 1.5), 1),
            "hint": "当日成交额 / 20 日均额",
        },
    ]

    score = round(sum(d["value"] for d in dims) / len(dims), 1)
    band = next(b for b in SENTIMENT_BANDS if score >= b[0])
    return {
        "trade_date": str(day),
        "score": score,
        "level": band[1],
        "hint": band[2],
        "dims": dims,
        "trend": trend,
        "amount_today": round(today_amount, 2),
        "amount_avg20": round(avg20, 2),
        "amount_ratio": round(vol_ratio, 4) if avg20 else None,
        "amount_series": amounts,
    }


# --------------------------------------------------------------------------
# 总台聚合
# --------------------------------------------------------------------------


def dashboard_overview(refresh: bool = False) -> dict:
    if refresh:
        from ..cache import cache_clear

        cache_clear()

    day = latest_trade_date()
    if not day:
        return {"trade_date": None, "empty": True}

    br = breadth(day)
    return {
        "trade_date": str(day),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "indexes": index_overview(day),
        "breadth": br,
        "sentiment": sentiment(day),
        "ladder": limit_ladder(day, detail=True),
        "rankings": {
            "pct_chg_desc": rankings(day, "pct_chg_desc", 8),
            "pct_chg_asc": rankings(day, "pct_chg_asc", 8),
            "amount": rankings(day, "amount", 8),
            "turnover_rate": rankings(day, "turnover_rate", 8),
        },
        "concepts": board_top(day, "concept", 5),
        "industries": board_top(day, "industry", 5),
        "amount_series": amount_series(day, 30),
    }


def amount_yi(amount) -> str:
    if not amount:
        return "—"
    if amount >= 1e12:
        return f"{amount / 1e12:.2f} 万亿"
    return f"{amount / 1e8:.2f} 亿"

# -*- coding: utf-8 -*-
"""行情类接口：全市场摘要、广度、榜单、个股日线、证券搜索、连板梯队、板块热度。"""
from datetime import date as _date

from fastapi import APIRouter, HTTPException, Query

from ..cache import ttl_cache
from ..db import query, scalar
from ..services import live_market
from ..services import market as svc

router = APIRouter(tags=["market"])


def _num(v, default=None):
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _parse_date(value):
    if not value:
        return None
    try:
        return _date.fromisoformat(value)
    except ValueError:
        return None


@router.get("/market-data/summary")
def market_summary(date: str | None = None):
    if not date and live_market.available():
        br = live_market.breadth()
        return {
            "trade_date": str(live_market.today()),
            "live": True,
            "as_of": live_market._as_of(),
            "indexes": live_market.indexes(),
            "breadth": br,
            "amount_series": live_market.amount_series(30),
        }
    day = _parse_date(date) or svc.latest_trade_date()
    if not day:
        return {"trade_date": None, "indexes": [], "breadth": {}}
    br = svc.breadth(day)
    return {
        "trade_date": str(day),
        "live": False,
        "indexes": svc.index_overview(day),
        "breadth": {k: v for k, v in br.items() if k != "limit_up_codes"},
        "amount_series": svc.amount_series(day, 30),
    }


@router.get("/market-data/breadth")
def market_breadth(date: str | None = None):
    if not date and live_market.available():
        br = live_market.breadth()
        br["trend"] = svc.trend_and_newhigh(svc.latest_trade_date())
        return br
    day = _parse_date(date) or svc.latest_trade_date()
    if not day:
        return {}
    br = svc.breadth(day)
    br.pop("limit_up_codes", None)
    br["trend"] = svc.trend_and_newhigh(day)
    return br


@router.get("/market-data/rankings")
def market_rankings(
    sort: str = Query("pct_chg_desc"),
    limit: int = Query(8, ge=1, le=50),
    date: str | None = None,
):
    if not date and live_market.available():
        return {
            "trade_date": str(live_market.today()),
            "live": True,
            "as_of": live_market._as_of(),
            "sort": sort,
            "items": live_market.rankings(sort, limit),
        }
    day = _parse_date(date) or svc.latest_trade_date()
    return {
        "trade_date": str(day) if day else None,
        "live": False,
        "sort": sort,
        "items": svc.rankings(day, sort, limit),
    }


@ttl_cache(60)
def _daily_series(ts_code: str, limit: int):
    return query(
        """
        SELECT trade_date, open, high, low, close, volume, amount, pct_chg,
               turnover_rate, pre_close, limit_up_price, limit_down_price
        FROM daily_quotes WHERE ts_code = %s
        ORDER BY trade_date DESC LIMIT %s
        """,
        (ts_code, limit),
    )


@router.get("/market-data/quotes/daily")
def quotes_daily(
    ts_code: str | None = Query(None, description="如 000001.SZ / 600519.SH"),
    symbol: str | None = Query(None, description="ts_code 的别名，兼容前端/原站口径"),
    days: int = Query(120, ge=5, le=500),
    limit: int | None = Query(None, ge=5, le=500, description="days 的别名"),
):
    code = (ts_code or symbol or "").strip().upper()
    if not code:
        raise HTTPException(status_code=422, detail="ts_code 或 symbol 必填")
    if "." not in code:
        code = f"{code}.SH" if code.startswith(("6", "9")) else f"{code}.SZ"
    if limit:
        days = limit
    rows = _daily_series(code, days)
    basic = query(
        """
        SELECT ts_code, stock_name, market, is_st, list_date
        FROM stock_basic_info WHERE ts_code = %s
        """,
        (code,),
    )
    info = basic[0] if basic else {}
    series = []
    for r in reversed(rows):
        d = r["trade_date"]
        series.append(
            {
                "date": str(d.date() if hasattr(d, "date") else d),
                "open": _num(r["open"]),
                "high": _num(r["high"]),
                "low": _num(r["low"]),
                "close": _num(r["close"]),
                "volume": _num(r["volume"]),
                "amount": _num(r["amount"]),
                "pct_chg": _num(r["pct_chg"]),
                "turnover_rate": _num(r["turnover_rate"]),
                "pre_close": _num(r["pre_close"]),
                "limit_up_price": _num(r["limit_up_price"]),
                "limit_down_price": _num(r["limit_down_price"]),
            }
        )
    last = series[-1] if series else {}
    return {
        "ts_code": code,
        "name": info.get("stock_name"),
        "market": info.get("market"),
        "is_st": bool(info.get("is_st")),
        "industry": info.get("industry"),
        "list_date": str(info.get("list_date")) if info.get("list_date") else None,
        "series": series,
        "latest": {
            "date": last.get("date"),
            "close": last.get("close"),
            "pct_chg": last.get("pct_chg"),
            "amount": last.get("amount"),
            "turnover_rate": last.get("turnover_rate"),
            "high": last.get("high"),
            "low": last.get("low"),
            "open": last.get("open"),
            "prev_close": last.get("pre_close"),
        },
    }


@router.get("/market-data/securities/search")
def securities_search(
    q: str = Query("", max_length=32),
    limit: int = Query(20, ge=1, le=50),
):
    q = (q or "").strip()
    if not q:
        return {"items": []}
    rows = query(
        """
        SELECT ts_code, stock_name, market, is_st, is_active, list_date
        FROM stock_basic_info
        WHERE ts_code ILIKE %s OR stock_name ILIKE %s
        ORDER BY (stock_name = %s) DESC, ts_code
        LIMIT %s
        """,
        (f"%{q}%", f"%{q}%", q, limit),
    )
    return {"items": rows}


# ---------------- 连板梯队 ----------------


@router.get("/limit-ladder")
def limit_ladder(
    date: str | None = None,
    board: str | None = Query(None, description="A主板/创业板/科创板/北交所"),
    streak: int | None = Query(None, ge=1, le=30, description="指定连板高度"),
    mode: str = Query("limit_up", pattern="^(limit_up|limit_down|broken|failed)$"),
):
    use_live = not date and live_market.available()
    if use_live:
        br = live_market.breadth()
        if mode != "limit_up":
            data = live_market.mode_ladder(mode)
        else:
            data = live_market.limit_ladder(detail=True)
    else:
        day = _parse_date(date) or svc.latest_trade_date()
        if not day:
            return {"trade_date": None, "tiers": [], "max_streak": 0, "limit_up": 0}
        data = svc.limit_ladder(day, detail=True)
        br = svc.breadth(day)

    bd = (board or "").strip()
    if bd:
        for tier in data["tiers"]:
            tier["members"] = [m for m in tier["members"] if m["board"] == bd]
        data["tiers"] = [t for t in data["tiers"] if t["members"]]
        for t in data["tiers"]:
            t["count"] = len(t["members"])
        data["limit_up"] = sum(t["count"] for t in data["tiers"])
    if streak and mode == "limit_up":
        data["tiers"] = [t for t in data["tiers"] if t["streak"] == streak]

    data["live"] = bool(use_live)
    data["mode"] = mode
    if use_live:
        data["as_of"] = live_market._as_of()
    data["limit_down"] = br["limit_down"]
    data["broken"] = br["broken"]
    data["touched"] = br["touched"]
    data["seal_rate"] = br["seal_rate"]
    return data


# ---------------- 板块热度 ----------------

_SORT_PATTERN = "^(pct_chg|pct_chg_desc|pct_chg_asc|net_inflow|stock_count)$"


def _norm_sort(sort: str) -> str:
    """`pct_chg` 是原站/前端的默认排序别名，统一映射成降序。"""
    s = (sort or "").strip()
    return "pct_chg_desc" if s in ("", "pct_chg", "pct_chg_desc") else s


@router.get("/boards/concepts")
def boards_concepts(
    sort: str = Query("pct_chg_desc", pattern=_SORT_PATTERN),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    page: int = Query(1, ge=1),
    size: int | None = Query(None, ge=1, le=200),
):
    if size is not None:
        limit, offset = size, (page - 1) * size
    return _boards("concept", _norm_sort(sort), limit, offset)


@router.get("/boards/industries")
def boards_industries(
    sort: str = Query("pct_chg_desc", pattern=_SORT_PATTERN),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    page: int = Query(1, ge=1),
    size: int | None = Query(None, ge=1, le=200),
):
    if size is not None:
        limit, offset = size, (page - 1) * size
    return _boards("industry", _norm_sort(sort), limit, offset)


def _boards(kind: str, sort: str, limit: int, offset: int) -> dict:
    if live_market.available():
        try:
            return live_market.boards_page(kind, sort, limit, offset)
        except Exception:  # noqa: BLE001 东财偶发断连 → 回退日线板块表
            pass
    table = "concept_board_quotes" if kind == "concept" else "industry_board_quotes"
    name_col = "concept_name" if kind == "concept" else "industry_name"
    code_col = "concept_code" if kind == "concept" else "industry_code"
    latest = scalar(f"SELECT MAX(trade_date) FROM {table}")
    if not latest:
        return {"trade_date": None, "items": [], "total": 0, "kind": kind}
    d = latest.date() if hasattr(latest, "date") else latest

    present = svc._columns(table)
    wanted = [
        "stock_count",
        "lead_stock_name",
        "net_inflow",
        "up_count",
        "down_count",
        "amount",
        "turnover_rate",
    ]
    select_extra = "".join(f", {c}" for c in wanted if c in present)

    order = {
        "pct_chg_desc": "pct_chg DESC NULLS LAST",
        "pct_chg_asc": "pct_chg ASC NULLS LAST",
        "net_inflow": "net_inflow DESC NULLS LAST" if "net_inflow" in present else "pct_chg DESC NULLS LAST",
        "stock_count": "stock_count DESC NULLS LAST" if "stock_count" in present else "pct_chg DESC NULLS LAST",
    }.get(sort, "pct_chg DESC NULLS LAST")

    total = scalar(f"SELECT COUNT(*) FROM {table} WHERE trade_date = %s", (d,)) or 0
    rows = query(
        f"""
        SELECT {code_col} AS code, {name_col} AS name, pct_chg{select_extra}
        FROM {table} WHERE trade_date = %s
        ORDER BY {order}
        LIMIT %s OFFSET %s
        """,
        (d, limit, offset),
    )

    up = down = 0
    for i, r in enumerate(rows):
        r["rank"] = offset + i + 1
        r["pct_chg"] = _num(r.get("pct_chg"))
        if r["pct_chg"] and r["pct_chg"] > 0:
            up += 1
        elif r["pct_chg"] and r["pct_chg"] < 0:
            down += 1
        for c in ("net_inflow", "amount"):
            if c in r:
                r[c] = _num(r.get(c))
    return {
        "trade_date": str(d),
        "items": rows,
        "total": total,
        "kind": kind,
        "up": up,
        "down": down,
    }


@router.get("/boards/{kind}/{code}")
def board_detail(kind: str, code: str, limit: int = Query(50, ge=1, le=500)):
    if kind not in ("concept", "industry"):
        return {"detail": "未知板块类型"}
    table = "concept_board_quotes" if kind == "concept" else "industry_board_quotes"
    code_col = "concept_code" if kind == "concept" else "industry_code"
    row = query(
        f"SELECT * FROM {table} WHERE {code_col} = %s ORDER BY trade_date DESC LIMIT 1",
        (code,),
    )
    if not row:
        live = live_market.board_detail(kind, code)
        if live:
            return live
        return {"detail": "板块不存在"}
    snap = dict(row[0])
    snap["trade_date"] = str(snap["trade_date"])
    for c in ("pct_chg", "net_inflow", "amount", "turnover_rate"):
        if c in snap:
            snap[c] = _num(snap.get(c))

    members = query(
        """
        SELECT ts_code, stock_name FROM board_member_cache
        WHERE board_type = %s AND board_code = %s
        ORDER BY ts_code LIMIT %s
        """,
        (kind, code, limit),
    )

    member_rows = []
    day = _parse_date(snap["trade_date"])
    if members and day:
        codes = [m["ts_code"] for m in members]
        quotes = query(
            """
            SELECT ts_code, close, pct_chg, amount, turnover_rate, volume
            FROM daily_quotes WHERE trade_date = %s AND ts_code = ANY(%s)
            """,
            (day, codes),
        )
        names = {m["ts_code"]: m["stock_name"] for m in members}
        for q in quotes:
            member_rows.append(
                {
                    "ts_code": q["ts_code"],
                    "name": names.get(q["ts_code"], ""),
                    "close": _num(q["close"]),
                    "pct_chg": _num(q["pct_chg"]),
                    "amount": _num(q["amount"]),
                    "turnover_rate": _num(q["turnover_rate"]),
                }
            )
        member_rows.sort(key=lambda x: -(x["pct_chg"] or 0))

    if not member_rows and kind == "industry":
        lm = live_market.industry_members(snap.get("industry_name") or snap.get("name"), limit=limit)
        member_rows = [
            {
                "ts_code": m["ts_code"],
                "name": m["name"],
                "close": m.get("close"),
                "pct_chg": m.get("pct_chg"),
                "amount": m.get("amount"),
                "turnover_rate": m.get("turnover_rate"),
            }
            for m in lm
        ]

    return {
        "kind": kind,
        "code": code,
        "snapshot": snap,
        "member_count": len(member_rows),
        "members": member_rows,
        "members_available": bool(member_rows),
    }


# ---------------- 板块成分股（BE-A 追加，只增不改） ----------------

# 一条 SQL 同时取「领涨股 + 分页成分股 + 总数」。
# 为什么合并：本机到远端库单次往返约 0.3~0.7s，两次往返就是双倍延迟；
# 领涨股来自板块行情表（一定有数），成分股来自两张空表（可能没数），
# 用 FULL JOIN ON TRUE 保证「成分股为空时也照样把领涨股带出来」。
# mem 里的 COUNT(*) OVER () 在 LIMIT/OFFSET 之前求值，所以拿到的是全量总数。
_MEMBER_SQL = """
WITH lead AS (
    SELECT trade_date, {name_col} AS board_name, lead_stock_code, lead_stock_name
    FROM {board_table}
    WHERE {code_col} = %s AND trade_date >= CURRENT_DATE - %s::int
    ORDER BY trade_date DESC LIMIT 1
),
mem AS (
    {mem_body}
)
SELECT m.ts_code, m.stock_name, COALESCE(m.board_name, l.board_name) AS board_name,
       l.lead_stock_code, l.lead_stock_name, l.trade_date, m.total
FROM lead l
FULL JOIN mem m ON TRUE
"""

_MEM_CONCEPT = """
    SELECT u.ts_code,
           COALESCE(NULLIF(i.stock_name, ''), '') AS stock_name,
           u.board_name,
           COUNT(*) OVER () AS total
    FROM (
        SELECT ts_code, concept_name AS board_name FROM concept_membership WHERE concept_code = %s
        UNION
        SELECT ts_code, COALESCE(NULLIF(board_name, ''), '') FROM board_member_cache
        WHERE board_type = 'concept' AND board_code = %s
    ) u
    LEFT JOIN stock_basic_info i ON i.ts_code = u.ts_code
    ORDER BY u.ts_code
    LIMIT %s OFFSET %s
"""

_MEM_CACHE = """
    SELECT m.ts_code,
           COALESCE(NULLIF(m.stock_name, ''), NULLIF(i.stock_name, ''), '') AS stock_name,
           m.board_name,
           COUNT(*) OVER () AS total
    FROM board_member_cache m
    LEFT JOIN stock_basic_info i ON i.ts_code = m.ts_code
    WHERE m.board_type = %s AND m.board_code = %s
    ORDER BY m.ts_code
    LIMIT %s OFFSET %s
"""

# 只取领涨股的降级 SQL：上面的成分股 CTE 依赖两张可能没建的表，
# 一旦整条 SQL 报错就用它把「板块名 + 领涨股」单独捞出来，保证降级时也有实信息。
_LEAD_SQL = """
    SELECT trade_date, {name_col} AS board_name, lead_stock_code, lead_stock_name,
           NULL::varchar AS ts_code, ''::varchar AS stock_name
    FROM {board_table}
    WHERE {code_col} = %s AND trade_date >= CURRENT_DATE - %s::int
    ORDER BY trade_date DESC LIMIT 1
"""


def _member_rows(sql: str, params: tuple) -> list[dict] | None:
    """跑一条成分股 SQL；表不存在/权限不足等 SQL 错误返回 ``None``（区别于「查到 0 行」）。

    为什么包异常：``concept_membership`` / ``board_member_cache`` 目前都是 0 行，
    甚至可能还没建表。成分股属于「锦上添花」的数据，宁可降级，
    也不能让板块详情页的子请求把接口打成 500。
    """
    try:
        return query(sql, params)
    except Exception:  # noqa: BLE001
        return None


def _fetch_members(kind: str, code: str, days: int, size: int, offset: int) -> list[dict]:
    """成分股分页 + 领涨股，**一条 SQL 一次往返**（远端库往返 0.3~0.7s，能省一次是一次）。

    概念的两张来源表在 SQL 里直接 UNION 合并，避免「第一张空表就跳过第二张」的漏查；
    合并 SQL 因表缺失整体失败时，再退化成只取领涨股的轻查询。
    """
    board_table = "concept_board_quotes" if kind == "concept" else "industry_board_quotes"
    name_col = "concept_name" if kind == "concept" else "industry_name"
    head = dict(board_table=board_table, name_col=name_col, code_col=name_col.replace("_name", "_code"))

    if kind == "concept":
        body, params = _MEM_CONCEPT, (code, days, code, code, size, offset)
    else:
        body, params = _MEM_CACHE, (code, days, "industry", code, size, offset)

    rows = _member_rows(_MEMBER_SQL.format(mem_body=body, **head), params)
    if rows is None:
        rows = _member_rows(_LEAD_SQL.format(**head), (code, days)) or []
    return rows


@router.get("/boards/{kind}/{code}/members")
def board_members(
    kind: str,
    code: str,
    days: int = Query(30, ge=1, le=365, description="回看板块行情天数，用于取板块名/领涨股"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
):
    """板块成分股（分页）。

    数据源优先级：``concept_membership``（概念专用）→ ``board_member_cache``（行业/概念通用）。
    两张表当前都是空表，因此**必须优雅降级**：返回空 items + ``degraded:true``，
    同时把板块当日行情里已有的 ``lead_stock_code/lead_stock_name`` 一起给出，
    前端至少还能显示「领涨股」这条可用信息。表里有数据时正常返回分页成分股。
    """
    if kind not in ("concept", "industry"):
        return {
            "kind": kind,
            "code": code,
            "page": page,
            "size": size,
            "items": [],
            "total": 0,
            "degraded": True,
            "message": "未知板块类型",
        }

    rows = _fetch_members(kind, code, days, size, (page - 1) * size)
    lead = rows[0] if rows else {}
    items = [
        {
            "ts_code": r["ts_code"],
            "stock_name": r.get("stock_name") or "",
            "board_name": r.get("board_name") or "",
        }
        for r in rows
        if r.get("ts_code")
    ]
    total = int(lead.get("total") or 0)

    if not items and kind == "industry":
        try:
            name_row = query(
                "SELECT industry_name FROM industry_board_quotes "
                "WHERE industry_code = %s ORDER BY trade_date DESC LIMIT 1",
                (code,),
            )
            name = name_row[0]["industry_name"] if name_row else None
        except Exception:  # noqa: BLE001
            name = None
        lm = live_market.industry_members(name) if name else []
        if lm:
            start = (page - 1) * size
            page_items = lm[start : start + size]
            return {
                "kind": kind,
                "code": code,
                "page": page,
                "size": size,
                "board_name": name,
                "items": [
                    {
                        "ts_code": m.get("ts_code"),
                        "stock_name": m.get("name") or m.get("ts_code"),
                        "board_name": name,
                        "close": m.get("close"),
                        "pct_chg": m.get("pct_chg"),
                        "amount": m.get("amount"),
                        "turnover_rate": m.get("turnover_rate"),
                    }
                    for m in page_items
                ],
                "total": len(lm),
                "degraded": False,
                "live": True,
            }

    if not items and live_market.available():
        live = live_market.board_detail(kind, code)
        lm = (live or {}).get("members") or []
        if lm:
            start = (page - 1) * size
            page_items = lm[start : start + size]
            snap_name = ((live or {}).get("snapshot") or {}).get("name")
            return {
                "kind": kind,
                "code": code,
                "page": page,
                "size": size,
                "board_name": snap_name,
                "items": [
                    {
                        "ts_code": m.get("ts_code"),
                        "stock_name": m.get("name") or m.get("ts_code"),
                        "board_name": snap_name,
                        "close": m.get("close"),
                        "pct_chg": m.get("pct_chg"),
                        "amount": m.get("amount"),
                        "turnover_rate": m.get("turnover_rate"),
                    }
                    for m in page_items
                ],
                "total": len(lm),
                "degraded": False,
                "live": True,
            }

    return {
        "kind": kind,
        "code": code,
        "page": page,
        "size": size,
        "board_name": lead.get("board_name"),
        "quote_date": str(lead["trade_date"]) if lead.get("trade_date") else None,
        "lead_stock_code": lead.get("lead_stock_code"),
        "lead_stock_name": lead.get("lead_stock_name"),
        "items": items,
        "total": total,
        "degraded": not items,
        "message": "成分股数据待接入" if not items else None,
    }

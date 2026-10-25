# -*- coding: utf-8 -*-
"""行情类接口：全市场摘要、广度、榜单、个股日线、证券搜索、连板梯队、板块热度。"""
from datetime import date as _date

from fastapi import APIRouter, Query

from ..cache import ttl_cache
from ..db import query, scalar
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
    day = _parse_date(date) or svc.latest_trade_date()
    if not day:
        return {"trade_date": None, "indexes": [], "breadth": {}}
    br = svc.breadth(day)
    return {
        "trade_date": str(day),
        "indexes": svc.index_overview(day),
        "breadth": {k: v for k, v in br.items() if k != "limit_up_codes"},
        "amount_series": svc.amount_series(day, 30),
    }


@router.get("/market-data/breadth")
def market_breadth(date: str | None = None):
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
    day = _parse_date(date) or svc.latest_trade_date()
    return {
        "trade_date": str(day) if day else None,
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
    ts_code: str = Query(..., description="如 000001.SZ / 600519.SH"),
    days: int = Query(120, ge=5, le=500),
):
    code = ts_code.strip().upper()
    if "." not in code:
        code = f"{code}.SH" if code.startswith(("6", "9")) else f"{code}.SZ"
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
):
    day = _parse_date(date) or svc.latest_trade_date()
    if not day:
        return {"trade_date": None, "tiers": [], "max_streak": 0, "limit_up": 0}
    data = svc.limit_ladder(day, detail=True)

    bd = (board or "").strip()
    if bd:
        for tier in data["tiers"]:
            tier["members"] = [m for m in tier["members"] if m["board"] == bd]
        data["tiers"] = [t for t in data["tiers"] if t["members"]]
        for t in data["tiers"]:
            t["count"] = len(t["members"])
        data["limit_up"] = sum(t["count"] for t in data["tiers"])
    if streak:
        data["tiers"] = [t for t in data["tiers"] if t["streak"] == streak]

    br = svc.breadth(day)
    data["limit_down"] = br["limit_down"]
    data["broken"] = br["broken"]
    data["touched"] = br["touched"]
    data["seal_rate"] = br["seal_rate"]
    return data


# ---------------- 板块热度 ----------------

_SORT_PATTERN = "^(pct_chg_desc|pct_chg_asc|net_inflow|stock_count)$"


@router.get("/boards/concepts")
def boards_concepts(
    sort: str = Query("pct_chg_desc", pattern=_SORT_PATTERN),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return _boards("concept", sort, limit, offset)


@router.get("/boards/industries")
def boards_industries(
    sort: str = Query("pct_chg_desc", pattern=_SORT_PATTERN),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return _boards("industry", sort, limit, offset)


def _boards(kind: str, sort: str, limit: int, offset: int) -> dict:
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

    return {
        "kind": kind,
        "code": code,
        "snapshot": snap,
        "member_count": len(members),
        "members": member_rows,
        "members_available": bool(members),
    }

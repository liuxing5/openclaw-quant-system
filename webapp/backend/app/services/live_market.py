# -*- coding: utf-8 -*-
"""盘中实时总台：用全市场快照覆盖 T-1 日线聚合。

为什么需要这一层
----------------
``daily_quotes`` 最新只到上一交易日（今天是 2026-10-26，表里是 10-23），
所以「每 60 秒刷新」如果只重算日线聚合，数字当然不会变。本模块把
``live_snapshot``（腾讯 5224 只、20 秒刷新）作为**当日行情底座**，实时计算：

* 涨跌广度 / 涨跌停 / 封板率 / 成交额 / 分布直方图
* 四类榜单（涨幅/跌幅/成交额/换手）
* 连板梯队（今日涨停 + 截至上一交易日的连板高度）
* 情绪六维（广度、涨停效应、连板高度来自实时；趋势/量能复用日线缓存）
* 概念/行业热度（东财板块实时榜单）

任何一步失败都回退到 ``market.py`` 的 T-1 口径，接口不会因此不可用。
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

from ..cache import ttl_cache
from ..db import query
from . import live_snapshot, market, realtime

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_BOARD_FS = {"concept": "m:90+t:3+f:!50", "industry": "m:90+t:2+f:!50"}


def _get_json(url: str, timeout: float = 8.0) -> dict:
    """东财接口偶发 RemoteDisconnected，重试一次再放弃。"""
    last = None
    for _ in range(2):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": _UA, "Referer": "https://quote.eastmoney.com/"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 域名固定
                return json.loads(resp.read())
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.3)
    raise last


def _f(v, default=None):
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def today() -> date:
    """实时口径的「今天」：工作日用自然日，周末沿用最近交易日。"""
    now = datetime.now()
    if now.weekday() < 5:
        return now.date()
    return market.latest_trade_date() or now.date()


def available() -> bool:
    return (live_snapshot.get_snapshot().get("count") or 0) >= 4000


def _items() -> list[dict]:
    return live_snapshot.get_snapshot().get("items") or []


def _as_of() -> str | None:
    return live_snapshot.get_snapshot().get("as_of")


# ---------------------------------------------------------------------------
# 广度
# ---------------------------------------------------------------------------

@ttl_cache(10)
def breadth() -> dict:
    items = _items()
    if not items:
        return {}
    day = today()
    up = flat = down = lu = ld = touched = broken = 0
    amount = 0.0
    total = 0
    for it in items:
        price = _f(it.get("price"))
        pct = _f(it.get("pct_chg"))
        if not price or pct is None:
            continue
        total += 1
        amount += _f(it.get("amount"), 0) or 0
        if pct > 0:
            up += 1
        elif pct < 0:
            down += 1
        else:
            flat += 1
        lup = _f(it.get("limit_up_price"))
        ldp = _f(it.get("limit_down_price"))
        high = _f(it.get("high"))
        is_lu = bool(lup and price >= lup - 0.005)
        is_ld = bool(ldp and price <= ldp + 0.005)
        hit = bool(lup and high and high >= lup - 0.005)
        if is_lu:
            lu += 1
        if is_ld:
            ld += 1
        if hit:
            touched += 1
            if not is_lu:
                broken += 1

    # 复用日线的分桶标签与顺序，保证「实时/日线」两张直方图可比
    labels = market.BREADTH_LABELS
    buckets = [0] * len(labels)
    for it in items:
        pct = _f(it.get("pct_chg"))
        price = _f(it.get("price"))
        if pct is None or not price:
            continue
        lup = _f(it.get("limit_up_price"))
        ldp = _f(it.get("limit_down_price"))
        if lup and price >= lup - 0.005:
            idx = 0
        elif ldp and price <= ldp + 0.005:
            idx = 10
        elif pct >= 7:
            idx = 1
        elif pct >= 5:
            idx = 2
        elif pct >= 3:
            idx = 3
        elif pct > 0:
            idx = 4
        elif pct <= -7:
            idx = 9
        elif pct <= -5:
            idx = 8
        elif pct <= -3:
            idx = 7
        elif pct < 0:
            idx = 6
        else:
            idx = 5
        buckets[idx] += 1

    return {
        "trade_date": str(day),
        "symbols": len(items),
        "total": total,
        "up": up,
        "flat": flat,
        "down": down,
        "limit_up": lu,
        "limit_down": ld,
        "broken": broken,
        "touched": touched,
        "seal_rate": round(lu / touched, 4) if touched else None,
        "amount": round(amount, 2),
        "up_ratio": round(up / total, 4) if total else None,
        "histogram": [{"label": labels[i], "count": buckets[i]} for i in range(len(labels))],
        "live": True,
        "as_of": _as_of(),
    }


# ---------------------------------------------------------------------------
# 榜单
# ---------------------------------------------------------------------------

_SORT_KEYS = {
    "pct_chg_desc": lambda x: -(x.get("pct_chg") or 0),
    "pct_chg_asc": lambda x: x.get("pct_chg") or 0,
    "amount": lambda x: -(x.get("amount") or 0),
    "turnover_rate": lambda x: -(x.get("turnover_rate") or 0),
    "volume": lambda x: -(x.get("volume") or 0),
}


@ttl_cache(10)
def rankings(sort: str = "pct_chg_desc", limit: int = 8) -> list[dict]:
    items = [x for x in _items() if _f(x.get("price")) and _f(x.get("pct_chg")) is not None]
    if not items:
        return []
    key = _SORT_KEYS.get(sort, _SORT_KEYS["pct_chg_desc"])
    rows = sorted(items, key=key)[: max(1, min(int(limit), 50))]
    codes = [r["ts_code"] for r in rows]
    boards = market._boards(codes)
    return [
        {
            "ts_code": r["ts_code"],
            "name": r.get("name") or r["ts_code"],
            "board": boards.get(r["ts_code"], ""),
            "close": round(_f(r.get("price"), 0) or 0, 2),
            "pct_chg": round(_f(r.get("pct_chg"), 0) or 0, 2),
            "amount": _f(r.get("amount"), 0) or 0,
            "turnover_rate": round(_f(r.get("turnover_rate"), 0) or 0, 2),
            "volume": _f(r.get("volume"), 0) or 0,
            "circulating_market_cap": _f(r.get("circulating_market_cap")),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# 成交额序列（历史 + 今日实时）
# ---------------------------------------------------------------------------

@ttl_cache(20)
def amount_series(limit: int = 30) -> list[dict]:
    db_day = market.latest_trade_date()
    rows = list(market.amount_series(db_day, limit)) if db_day else []
    br = breadth()
    day = today()
    if br and (not rows or str(rows[-1].get("date")) != str(day)):
        rows.append({"date": str(day), "amount": br.get("amount")})
    return rows[-limit:]


# ---------------------------------------------------------------------------
# 指数（实时价覆盖 + 保留 30 日曲线）
# ---------------------------------------------------------------------------

@ttl_cache(10)
def indexes() -> list[dict]:
    base = market.index_overview()
    by_code = {b["ts_code"]: b for b in base}
    codes = [c for c, _ in market.INDEX_LIST]
    try:
        live = {r["ts_code"]: r for r in realtime.snapshot_rows(codes)}
    except Exception:  # noqa: BLE001
        live = {}
    day = today()
    out = []
    for code, name in market.INDEX_LIST:
        b = dict(by_code.get(code) or {"ts_code": code, "name": name, "curve": []})
        l = live.get(code) or {}
        price = _f(l.get("price"))
        if price:
            b["close"] = price
            b["pct_chg"] = _f(l.get("pct_chg"))
            b["open"] = _f(l.get("open"))
            b["high"] = _f(l.get("high"))
            b["low"] = _f(l.get("low"))
            b["prev_close"] = _f(l.get("pre_close"))
            b["live"] = True
            curve = list(b.get("curve") or [])
            if not curve or str(curve[-1].get("date")) != str(day):
                curve.append({"date": str(day), "close": price})
            b["curve"] = curve[-31:]
        out.append(b)
    return out


# ---------------------------------------------------------------------------
# 连板梯队
# ---------------------------------------------------------------------------

@ttl_cache(10)
def limit_ladder(detail: bool = True) -> dict:
    items = _items()
    if not items:
        return {"trade_date": None, "tiers": [], "max_streak": 0, "limit_up": 0}
    lu = []
    for it in items:
        price = _f(it.get("price"))
        lup = _f(it.get("limit_up_price"))
        if price and lup and price >= lup - 0.005:
            lu.append(it)
    if not lu:
        return {
            "trade_date": str(today()),
            "tiers": [],
            "max_streak": 0,
            "limit_up": 0,
            "live": True,
            "as_of": _as_of(),
        }

    codes = [x["ts_code"] for x in lu]
    db_day = market.latest_trade_date()
    prior: dict[str, int] = {}
    if db_day:
        dates = market.trade_dates(120)
        window = [db_day] + [d for d in dates if d < db_day][:29]
        try:
            rows = query(market._STREAK_SQL, (codes, window))
            prior = {r["ts_code"]: int(r["streak"]) for r in rows if r.get("streak")}
        except Exception:  # noqa: BLE001
            prior = {}

    streaks = {
        x["ts_code"]: (prior.get(x["ts_code"], 0) if db_day == today() else prior.get(x["ts_code"], 0) + 1)
        or 1
        for x in lu
    }
    by_code = {x["ts_code"]: x for x in lu}
    boards = market._boards(codes)
    tier_map: dict[int, list[dict]] = {}
    for code, n in streaks.items():
        it = by_code[code]
        tier_map.setdefault(n, []).append(
            {
                "ts_code": code,
                "name": it.get("name") or code,
                "board": boards.get(code, ""),
                "streak": n,
                "status": "今日涨停" if n <= 1 else f"{n}连板",
                "close": round(_f(it.get("price"), 0) or 0, 2),
                "pct_chg": round(_f(it.get("pct_chg"), 0) or 0, 2),
                "amount": _f(it.get("amount"), 0) or 0,
                "turnover_rate": round(_f(it.get("turnover_rate"), 0) or 0, 2),
                "volume": _f(it.get("volume"), 0) or 0,
            }
        )

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
        "trade_date": str(today()),
        "tiers": tiers,
        "max_streak": max(tier_map),
        "limit_up": len(streaks),
        "live": True,
        "as_of": _as_of(),
    }


# ---------------------------------------------------------------------------
# 情绪六维
# ---------------------------------------------------------------------------

@ttl_cache(20)
def sentiment() -> dict:
    br = breadth()
    if not br:
        return {}
    day = today()
    db_day = market.latest_trade_date()
    trend = market.trend_and_newhigh(db_day) if db_day else {}
    amounts = market.amount_series(db_day, 21) if db_day else []
    ladder = limit_ladder(detail=False)

    avg20 = 0.0
    if amounts:
        prev = [_f(a.get("amount"), 0) or 0 for a in amounts[:-1]]
        avg20 = sum(prev) / len(prev) if prev else 0
    vol_ratio = (br["amount"] / avg20) if avg20 else 0
    max_streak = ladder.get("max_streak") or 0
    up_ratio = br.get("up_ratio") or 0
    seal_rate = br.get("seal_rate") or 0
    hl = trend.get("hl_ratio") or 0
    ma5 = trend.get("ma5_ratio") or 0
    ma20 = trend.get("ma20_ratio") or 0

    dims = [
        {"key": "trend", "label": "趋势强度", "value": round(min(100, 60 * ma5 + 40 * ma20), 1),
         "hint": "站上 MA5 / MA20 的个股比例加权"},
        {"key": "breadth", "label": "上涨广度", "value": round(100 * up_ratio, 1),
         "hint": "全市场上涨家数占比"},
        {"key": "seal", "label": "涨停效应", "value": round(100 * (seal_rate or 0), 1),
         "hint": "触板封住比例（封板率）"},
        {"key": "streak", "label": "连板高度", "value": round(min(100, max_streak / 8 * 100), 1),
         "hint": f"最高 {max_streak} 板 / 8 板封顶"},
        {"key": "momentum", "label": "新高动能", "value": round(min(100, 100 * hl), 1),
         "hint": "60 日新高 / 新低家数比"},
        {"key": "volume", "label": "量能活跃", "value": round(min(100, 100 * vol_ratio / 1.5), 1),
         "hint": "当日成交额 / 20 日均额"},
    ]
    score = round(sum(d["value"] for d in dims) / len(dims), 1)
    band = next(b for b in market.SENTIMENT_BANDS if score >= b[0])
    return {
        "trade_date": str(day),
        "score": score,
        "level": band[1],
        "hint": band[2],
        "dims": dims,
        "trend": trend,
        "amount_today": round(br["amount"], 2),
        "amount_avg20": round(avg20, 2),
        "amount_ratio": round(vol_ratio, 4) if avg20 else None,
        "amount_series": amounts,
        "live": True,
        "as_of": br.get("as_of"),
    }


# ---------------------------------------------------------------------------
# 板块热度（东财实时板块榜）
# ---------------------------------------------------------------------------

def _board_page(board_type: str, ascending: bool, limit: int) -> tuple[list[dict], int]:
    params = {
        "pn": "1",
        "pz": str(max(1, limit)),
        "po": "0" if ascending else "1",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "fid": "f3",
        "fs": _BOARD_FS.get(board_type, _BOARD_FS["concept"]),
        "fields": "f3,f12,f14,f62,f104,f105,f128,f136",
    }
    url = "https://push2.eastmoney.com/api/qt/clist/get?" + urllib.parse.urlencode(params)
    data = _get_json(url).get("data") or {}
    rows = data.get("diff") or []
    if isinstance(rows, dict):
        rows = list(rows.values())
    out = [
        {
            "code": r.get("f12"),
            "name": r.get("f14"),
            "pct_chg": round(_f(r.get("f3"), 0) or 0, 2),
            "stock_count": None,
            "lead_stock": (r.get("f128") or "").strip(),
            "net_inflow": _f(r.get("f62")),
            "up_count": r.get("f104"),
            "down_count": r.get("f105"),
            "amount": None,
        }
        for r in rows[:limit]
    ]
    return out, int(data.get("total") or 0)


@ttl_cache(30)
def board_detail(kind: str, code: str) -> dict | None:
    """东财板块实时详情 + 近 20 日 K 线；成分股接口不稳定，members 返回空。"""
    secid = f"90.{code}"
    try:
        qurl = (
            "https://push2.eastmoney.com/api/qt/stock/get?"
            + urllib.parse.urlencode(
                {
                    "secid": secid,
                    "fltt": "2",
                    "fields": "f43,f44,f45,f46,f47,f48,f57,f58,f60,f169,f170,f171,f168",
                }
            )
        )
        q = _get_json(qurl).get("data") or {}
        if not q.get("f58"):
            return None

        kurl = (
            "https://push2his.eastmoney.com/api/qt/stock/kline/get?"
            + urllib.parse.urlencode(
                {
                    "secid": secid,
                    "klt": "101",
                    "fqt": "1",
                    "lmt": "20",
                    "end": "20500101",
                    "fields1": "f1,f2,f3,f4,f5,f6",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57",
                }
            )
        )
        kd = _get_json(kurl).get("data") or {}
        history = []
        for line in kd.get("klines") or []:
            p = line.split(",")
            if len(p) < 7:
                continue
            history.append(
                {
                    "trade_date": p[0],
                    "open": _f(p[1]),
                    "close": _f(p[2]),
                    "high": _f(p[3]),
                    "low": _f(p[4]),
                    "volume": _f(p[5]),
                    "amount": _f(p[6]),
                }
            )
        snapshot = {
            "code": code,
            "concept_code" if kind == "concept" else "industry_code": code,
            "concept_name" if kind == "concept" else "industry_name": q.get("f58"),
            "name": q.get("f58"),
            "trade_date": str(today()),
            "close": _f(q.get("f43")),
            "high": _f(q.get("f44")),
            "low": _f(q.get("f45")),
            "open": _f(q.get("f46")),
            "amount": _f(q.get("f48")),
            "pre_close": _f(q.get("f60")),
            "pct_chg": _f(q.get("f170")),
            "change": _f(q.get("f169")),
            "turnover_rate": _f(q.get("f168")),
            "stock_count": None,
            "lead_stock_name": None,
            "net_inflow": None,
        }
        return {
            "kind": kind,
            "code": code,
            "snapshot": snapshot,
            "history": history,
            "member_count": 0,
            "members": [],
            "members_available": False,
            "live": True,
            "as_of": _as_of(),
            "note": "东财实时板块行情；成分股接口不稳定，暂不返回成分明细",
        }
    except Exception:  # noqa: BLE001
        return None


@ttl_cache(30)
def boards_page(board_type: str, sort: str = "pct_chg_desc", limit: int = 20, offset: int = 0) -> dict:
    """板块列表分页（东财实时榜单），字段与 market._boards 对齐。"""
    sort_map = {
        "pct_chg_desc": ("f3", "1"),
        "pct_chg_asc": ("f3", "0"),
        "net_inflow": ("f62", "1"),
        "stock_count": ("f104", "1"),
    }
    fid, po = sort_map.get(sort, sort_map["pct_chg_desc"])
    page = (offset // max(1, limit)) + 1
    params = {
        "pn": str(page),
        "pz": str(max(1, limit)),
        "po": po,
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "fid": fid,
        "fs": _BOARD_FS.get(board_type, _BOARD_FS["concept"]),
        "fields": "f3,f12,f14,f62,f104,f105,f128",
    }
    url = "https://push2.eastmoney.com/api/qt/clist/get?" + urllib.parse.urlencode(params)
    data = _get_json(url).get("data") or {}
    rows = data.get("diff") or []
    if isinstance(rows, dict):
        rows = list(rows.values())
    items = [
        {
            "code": r.get("f12"),
            "name": r.get("f14"),
            "pct_chg": round(_f(r.get("f3"), 0) or 0, 2),
            "stock_count": None,
            "lead_stock_name": (r.get("f128") or "").strip(),
            "net_inflow": _f(r.get("f62")),
            "up_count": r.get("f104"),
            "down_count": r.get("f105"),
            "amount": None,
            "rank": offset + i + 1,
        }
        for i, r in enumerate(rows[:limit])
    ]
    return {
        "trade_date": str(today()),
        "items": items,
        "total": int(data.get("total") or 0),
        "kind": "concept" if board_type == "concept" else "industry",
        "live": True,
        "as_of": _as_of(),
    }


@ttl_cache(30)
def board_top(board_type: str = "concept", limit: int = 5) -> dict:
    try:
        gainers, total = _board_page(board_type, False, limit)
        losers, _ = _board_page(board_type, True, limit)
        for i, g in enumerate(gainers):
            g["rank"] = i + 1
        for i, l in enumerate(losers):
            l["rank"] = total - i
        return {
            "gainers": gainers,
            "losers": losers,
            "trade_date": str(today()),
            "total": total,
            "live": True,
            "as_of": _as_of(),
        }
    except Exception:  # noqa: BLE001
        return market.board_top(None, board_type, limit)


# ---------------------------------------------------------------------------
# 聚合
# ---------------------------------------------------------------------------

def overview(refresh: bool = False) -> dict:
    if not available():
        return market.dashboard_overview(refresh=refresh)
    if refresh:
        try:
            live_snapshot.refresh_now()
        except Exception:  # noqa: BLE001
            pass

    day = today()
    br = breadth()
    amounts = amount_series(30)
    return {
        "trade_date": str(day),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "live": True,
        "as_of": _as_of(),
        "data_source": "tencent-live + daily_quotes",
        "indexes": indexes(),
        "breadth": br,
        "sentiment": sentiment(),
        "ladder": limit_ladder(detail=True),
        "rankings": {
            "pct_chg_desc": rankings("pct_chg_desc", 8),
            "pct_chg_asc": rankings("pct_chg_asc", 8),
            "amount": rankings("amount", 8),
            "turnover_rate": rankings("turnover_rate", 8),
        },
        "concepts": board_top("concept", 5),
        "industries": board_top("industry", 5),
        "amount_series": amounts,
    }


def summary() -> dict:
    if not available():
        return {}
    br = breadth()
    senti = sentiment()
    return {
        "trade_date": str(today()),
        "live": True,
        "as_of": _as_of(),
        "score": senti.get("score"),
        "level": senti.get("level"),
        "amount": br.get("amount"),
        "limit_up": br.get("limit_up"),
        "limit_down": br.get("limit_down"),
        "seal_rate": br.get("seal_rate"),
        "up": br.get("up"),
        "down": br.get("down"),
        "flat": br.get("flat"),
    }

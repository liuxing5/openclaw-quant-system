# -*- coding: utf-8 -*-
"""分钟级行情 + 多级别缠论（30分钟 / 5分钟）。

数据源：腾讯 ``ifzq.gtimg.cn`` 的分钟 K 线接口，单只股票一次 320 根，
无需登录。分钟数据只按需拉取（打开个股/多级别分析时），不落全市场大表；
日线结构仍来自本地 ``daily_quotes`` 的 260 根日线。
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

from ..cache import ttl_cache
from ..db import query
from . import technical

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://gu.qq.com/",
}


def _sym(ts_code: str) -> str:
    c = (ts_code or "").upper()
    if "." in c:
        code, market = c.split(".", 1)
    else:
        code, market = c, ("SH" if c.startswith(("6", "9")) else "SZ")
    if market == "SH":
        return f"sh{code}"
    if market == "BJ":
        return f"bj{code}"
    return f"sz{code}"


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@ttl_cache(60)
def fetch(ts_code: str, period: str = "m30", count: int = 320) -> list[dict]:
    sym = _sym(ts_code)
    qs = urllib.parse.urlencode({"param": f"{sym},{period},,{count}"})
    req = urllib.request.Request(f"https://ifzq.gtimg.cn/appstock/app/kline/mkline?{qs}", headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return []
    rows = ((data.get("data") or {}).get(sym) or {}).get(period) or []
    out = []
    for r in rows:
        if len(r) < 6:
            continue
        d = {
            "time": str(r[0]),
            "open": _num(r[1]),
            "close": _num(r[2]),
            "high": _num(r[3]),
            "low": _num(r[4]),
            "volume": _num(r[5]),
        }
        if None not in (d["open"], d["close"], d["high"], d["low"]):
            out.append(d)
    out.sort(key=lambda x: x["time"])
    return out


def _summary(bars: list[dict]) -> dict | None:
    if len(bars) < 30:
        return None
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    closes = [b["close"] for b in bars]
    m = technical.macd(closes)
    c = technical.chan(highs, lows, closes, m)
    return {
        "bars": len(bars),
        "last_time": bars[-1]["time"],
        "close": closes[-1],
        "macd": {
            "dif": m["dif"],
            "dea": m["dea"],
            "hist": m["hist"],
            "cross": m["cross"],
            "hist_turn": m["hist_turn"],
        },
        "chan": c,
    }


@ttl_cache(60)
def analyze(ts_code: str, period: str = "m30") -> dict | None:
    bars = fetch(ts_code, period)
    return _summary(bars) if bars else None


def _daily(ts_code: str) -> dict | None:
    rows = query(
        "SELECT high, low, close FROM daily_quotes WHERE ts_code = %s "
        "ORDER BY trade_date DESC LIMIT 260",
        (ts_code,),
    )
    if len(rows) < 30:
        return None
    rows = list(reversed(rows))
    highs = [float(r["high"]) for r in rows]
    lows = [float(r["low"]) for r in rows]
    closes = [float(r["close"]) for r in rows]
    m = technical.macd(closes)
    c = technical.chan(highs, lows, closes, m)
    return {
        "bars": len(rows),
        "macd": {
            "dif": m["dif"],
            "dea": m["dea"],
            "hist": m["hist"],
            "cross": m["cross"],
        },
        "chan": c,
    }


def _resonance(daily: dict | None, m30: dict | None, m5: dict | None) -> str:
    if not daily or not m30:
        return "数据不足"
    d = (daily.get("chan") or {}).get("trend_direction")
    a = (m30.get("chan") or {}).get("buy_point")
    b = (m5 or {}).get("chan", {}).get("buy_point")
    sell30 = (m30.get("chan") or {}).get("sell_point")
    sell5 = (m5 or {}).get("chan", {}).get("sell_point")
    if d == "up" and a and b:
        return "多级别共振买点"
    if d == "up" and a:
        return "日线+30分钟买点"
    if d == "down" and sell30 and sell5:
        return "多级别共振卖点"
    if d == "down" and sell30:
        return "日线+30分钟卖点"
    if d == "up":
        return "日线向上 · 等待30分钟买点"
    if d == "down":
        return "日线向下 · 等待卖点确认"
    return "级别分歧"


def multi_level(ts_code: str) -> dict:
    daily = _daily(ts_code)
    m30 = analyze(ts_code, "m30")
    m5 = analyze(ts_code, "m5")
    return {
        "ts_code": ts_code,
        "daily": daily,
        "m30": m30,
        "m5": m5,
        "resonance": _resonance(daily, m30, m5),
        "note": "日线来自本地 260 根日线；30m/5m 来自腾讯分钟 K 线（320 根，按需拉取）",
    }

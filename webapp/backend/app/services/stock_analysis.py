# -*- coding: utf-8 -*-
"""个股量价推演（AI 量价推演的后端算法）。

不依赖外部 LLM：用日线 + 实时价做一套可复现的量化推演，
输出目标价、概率、路径与触发条件，前端只负责展示。

模型口径
--------
* 趋势分：收盘价相对 MA5/MA10/MA20/MA60 的位置 + 均线多头排列，0~100；
* 量能：最近一日成交量 / 前 5 日均量；
* 阻力：枢轴 R1、20 日高点、布林上轨取最近的一个作为第一目标；
* 概率：50 + 趋势分×0.25 +（量比-1）×8，截断 35~88；
* 止损：S1 与 MA20 的较低者再下浮 1%。
"""
from __future__ import annotations

import math
import statistics
from datetime import date

from ..db import query
from . import realtime


def _f(v, default=None):
    try:
        if v is None:
            return default
        x = float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def _norm(code: str) -> str:
    c = (code or "").strip().upper()
    if not c:
        return ""
    if "." in c:
        return c
    return f"{c}.SH" if c.startswith(("6", "9")) else f"{c}.SZ"


def _ma(vals: list[float], n: int) -> float | None:
    if len(vals) < n or n <= 0:
        return None
    return sum(vals[-n:]) / n


def projection(code: str, target: float | None = None) -> dict | None:
    ts_code = _norm(code)
    if not ts_code:
        return None
    rows = query(
        """
        SELECT trade_date, open, high, low, close, pre_close, volume, amount,
               pct_chg, turnover_rate
        FROM daily_quotes WHERE ts_code = %s
        ORDER BY trade_date DESC LIMIT 250
        """,
        (ts_code,),
    )
    if not rows:
        return None
    rows = list(reversed(rows))
    closes = [_f(r["close"]) for r in rows if _f(r["close"]) is not None]
    highs = [_f(r["high"]) for r in rows if _f(r["high"]) is not None]
    lows = [_f(r["low"]) for r in rows if _f(r["low"]) is not None]
    vols = [_f(r["volume"], 0) or 0 for r in rows]
    if len(closes) < 20:
        return None

    live = {}
    try:
        live = (realtime.snapshot_rows([ts_code]) or [{}])[0]
    except Exception:  # noqa: BLE001
        live = {}
    last = rows[-1]
    price = _f(live.get("price")) or closes[-1]
    if not price:
        return None

    ma5, ma10, ma20, ma60 = (_ma(closes, n) for n in (5, 10, 20, 60))
    high20 = max(highs[-20:]) if highs else price
    low20 = min(lows[-20:]) if lows else price
    high60 = max(highs[-60:]) if len(highs) >= 60 else high20

    # 枢轴点（用最后一根完整日线）
    h, l, c = _f(last["high"], price), _f(last["low"], price), _f(last["close"], price)
    pivot = (h + l + c) / 3
    r1, s1 = 2 * pivot - l, 2 * pivot - h

    # 布林带
    mid = ma20 or price
    std = statistics.pstdev(closes[-20:]) if len(closes) >= 20 else 0
    boll_up, boll_low = mid + 2 * std, mid - 2 * std

    # 趋势分
    score = 50.0
    if ma5 and price > ma5:
        score += 8
    if ma10 and price > ma10:
        score += 8
    if ma20 and price > ma20:
        score += 10
    if ma60 and price > ma60:
        score += 8
    if ma5 and ma10 and ma20 and ma5 > ma10 > ma20:
        score += 10
    if ma10 and ma20 and ma10 > ma20:
        score += 6
    trend_score = max(0.0, min(100.0, score))

    avg5 = sum(vols[-6:-1]) / 5 if len(vols) >= 6 else (sum(vols) / len(vols) if vols else 0)
    volume_ratio = (vols[-1] / avg5) if avg5 else 1.0

    # 目标价：用户给定优先；否则取 R1 / 20 日高点 / 布林上轨中最近的「上方」阻力
    resistances = [x for x in (r1, high20, boll_up, high60) if x and x > price * 1.01]
    default_target = min(resistances) * 1.005 if resistances else price * 1.08
    target_price = _f(target) or default_target
    if target_price <= price:
        target_price = price * 1.05

    prob = 50 + (trend_score - 50) * 0.5 + (min(volume_ratio, 3) - 1) * 8
    prob = max(35, min(88, round(prob)))

    support = min(x for x in (s1, ma20 or price, low20) if x)
    stop = support * 0.99
    upside = (target_price / price - 1) * 100
    downside = (stop / price - 1) * 100

    path = [
        {"label": "当前", "price": round(price, 2), "prob": 100, "note": "实时/最新收盘价"},
        {"label": "突破 R1", "price": round(r1, 2), "prob": max(35, min(85, prob)), "note": "放量站上枢轴阻力"},
        {"label": "目标价", "price": round(target_price, 2), "prob": prob, "note": f"较现价 {upside:+.2f}%"},
    ]
    triggers = []
    if r1:
        triggers.append(
            {
                "condition": f"收盘价站上 R1 {r1:.2f} 且成交量 > 5 日均量 1.2 倍",
                "action": "确认突破，可按计划仓位跟进",
                "tone": "up",
            }
        )
    if ma20:
        triggers.append(
            {
                "condition": f"收盘价跌破 MA20 {ma20:.2f}",
                "action": "趋势转弱，减仓/离场",
                "tone": "down",
            }
        )
    triggers.append(
        {
            "condition": f"跌破止损位 {stop:.2f}",
            "action": f"严格止损（距现价 {downside:.2f}%）",
            "tone": "down",
        }
    )

    return {
        "ts_code": ts_code,
        "name": live.get("name") or ts_code,
        "trade_date": str(last["trade_date"]),
        "as_of": live.get("as_of"),
        "price": round(price, 2),
        "pct_chg": _f(live.get("pct_chg")),
        "target": round(target_price, 2),
        "upside_pct": round(upside, 2),
        "probability": prob,
        "stop": round(stop, 2),
        "downside_pct": round(downside, 2),
        "trend_score": round(trend_score, 1),
        "volume_ratio": round(volume_ratio, 2),
        "levels": {
            "pivot": round(pivot, 2),
            "r1": round(r1, 2),
            "s1": round(s1, 2),
            "high20": round(high20, 2),
            "low20": round(low20, 2),
            "high60": round(high60, 2),
            "ma5": round(ma5, 2) if ma5 else None,
            "ma10": round(ma10, 2) if ma10 else None,
            "ma20": round(ma20, 2) if ma20 else None,
            "ma60": round(ma60, 2) if ma60 else None,
            "boll_up": round(boll_up, 2),
            "boll_low": round(boll_low, 2),
        },
        "path": path,
        "triggers": triggers,
        "basis": {
            "ma5": round(ma5, 2) if ma5 else None,
            "ma20": round(ma20, 2) if ma20 else None,
            "ma60": round(ma60, 2) if ma60 else None,
            "volume_ratio": round(volume_ratio, 2),
            "trend_score": round(trend_score, 1),
        },
        "disclaimer": "由本地量化引擎按均线/量价/枢轴推演，仅供研究参考，不构成投资建议。",
    }

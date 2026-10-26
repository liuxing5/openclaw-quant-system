# -*- coding: utf-8 -*-
"""真实技术指标：MACD 与缠论结构（分型 / 笔 / 中枢 / 买卖点）。

数据口径：日线前复权收盘/最高/最低，最多取最近 260 根。
MACD 用标准 EMA12/EMA26/DEA9；缠论先做包含关系合并，再识别顶底分型、
按「至少 5 根合并 K 线」连笔，最后用最近三笔重叠区找中枢。

这个模块只做纯计算，不碰数据库；批量落库由
``scripts/refresh_technical_snapshot.py`` 负责。
"""
from __future__ import annotations


def ema(values: list[float], n: int) -> list[float]:
    if not values:
        return []
    k = 2.0 / (n + 1.0)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def macd(closes: list[float]) -> dict:
    """返回 DIF/DEA/HIST 序列及最新交叉状态。"""
    if len(closes) < 26:
        return {
            "dif": None,
            "dea": None,
            "hist": None,
            "cross": None,
            "hist_turn": None,
            "dif_series": [],
            "dea_series": [],
            "hist_series": [],
        }
    e12, e26 = ema(closes, 12), ema(closes, 26)
    dif = [a - b for a, b in zip(e12, e26)]
    dea = ema(dif, 9)
    hist = [2 * (a - b) for a, b in zip(dif, dea)]
    cross = None
    if len(dif) >= 2:
        if dif[-2] <= dea[-2] and dif[-1] > dea[-1]:
            cross = "golden"
        elif dif[-2] >= dea[-2] and dif[-1] < dea[-1]:
            cross = "dead"
        elif dif[-1] > dea[-1]:
            cross = "above"
        else:
            cross = "below"
    hist_turn = None
    if len(hist) >= 2:
        if hist[-2] <= 0 < hist[-1]:
            hist_turn = "positive"
        elif hist[-2] >= 0 > hist[-1]:
            hist_turn = "negative"
    return {
        "dif": round(dif[-1], 4),
        "dea": round(dea[-1], 4),
        "hist": round(hist[-1], 4),
        "cross": cross,
        "hist_turn": hist_turn,
        "dif_series": [round(x, 4) for x in dif[-60:]],
        "dea_series": [round(x, 4) for x in dea[-60:]],
        "hist_series": [round(x, 4) for x in hist[-60:]],
    }


def _merge_bars(highs: list[float], lows: list[float]) -> list[dict]:
    """缠论包含关系合并；方向由前两根合并 K 线决定。"""
    bars: list[dict] = []
    for i, (h, l) in enumerate(zip(highs, lows)):
        if h is None or l is None:
            continue
        if not bars:
            bars.append({"i": i, "h": h, "l": l})
            continue
        prev = bars[-1]
        # 无包含：直接追加
        if (h > prev["h"] and l > prev["l"]) or (h < prev["h"] and l < prev["l"]):
            bars.append({"i": i, "h": h, "l": l})
            continue
        # 有包含：按当前方向合并
        up = True
        if len(bars) >= 2:
            up = bars[-2]["h"] < prev["h"]
        if up:
            prev["h"] = max(prev["h"], h)
            prev["l"] = max(prev["l"], l)
        else:
            prev["h"] = min(prev["h"], h)
            prev["l"] = min(prev["l"], l)
        prev["i"] = i
    return bars


def _fractals(bars: list[dict]) -> list[dict]:
    out = []
    for i in range(1, len(bars) - 1):
        a, b, c = bars[i - 1], bars[i], bars[i + 1]
        if b["h"] > a["h"] and b["h"] > c["h"]:
            out.append({"type": "top", "idx": i, "price": b["h"], "bar_i": b["i"]})
        elif b["l"] < a["l"] and b["l"] < c["l"]:
            out.append({"type": "bottom", "idx": i, "price": b["l"], "bar_i": b["i"]})
    return out


def _strokes(fractals: list[dict], min_gap: int = 4) -> list[dict]:
    """分型连笔：同类型取更极端，异类型且间隔 >= min_gap 才成笔。"""
    if not fractals:
        return []
    points = [fractals[0]]
    for f in fractals[1:]:
        last = points[-1]
        if f["type"] == last["type"]:
            if (f["type"] == "top" and f["price"] >= last["price"]) or (
                f["type"] == "bottom" and f["price"] <= last["price"]
            ):
                points[-1] = f
            continue
        if f["idx"] - last["idx"] < min_gap:
            continue
        points.append(f)
    return [
        {
            "from": points[i]["type"],
            "to": points[i + 1]["type"],
            "start": points[i]["price"],
            "end": points[i + 1]["price"],
            "start_idx": points[i]["idx"],
            "end_idx": points[i + 1]["idx"],
        }
        for i in range(len(points) - 1)
    ]


def _pivot(strokes: list[dict]) -> dict | None:
    """最近三笔重叠区作为中枢；返回 zg/zd 与区间。"""
    if len(strokes) < 3:
        return None
    for i in range(len(strokes) - 3, -1, -1):
        a, b, c = strokes[i], strokes[i + 1], strokes[i + 2]
        lows = [min(a["start"], a["end"]), min(b["start"], b["end"]), min(c["start"], c["end"])]
        highs = [max(a["start"], a["end"]), max(b["start"], b["end"]), max(c["start"], c["end"])]
        zd, zg = max(lows), min(highs)
        if zd < zg:
            return {"zd": round(zd, 2), "zg": round(zg, 2), "start_idx": a["start_idx"], "end_idx": c["end_idx"]}
    return None


def chan(highs: list[float], lows: list[float], closes: list[float]) -> dict:
    bars = _merge_bars(highs, lows)
    fractals = _fractals(bars)
    strokes = _strokes(fractals)
    pivot = _pivot(strokes)
    trend = None
    if len(strokes) >= 3:
        up = strokes[-1]["end"] > strokes[-3]["end"] and strokes[-2]["end"] > strokes[-4]["end"] if len(strokes) >= 4 else strokes[-1]["end"] > strokes[-2]["start"]
        trend = "up" if up else "down"
    last = fractals[-1] if fractals else None
    signal = "hold"
    if last and last["type"] == "bottom":
        signal = "buy2"
        if pivot and closes and closes[-1] > pivot["zg"]:
            signal = "buy3"
        elif len([f for f in fractals if f["type"] == "bottom"]) >= 2:
            bottoms = [f for f in fractals if f["type"] == "bottom"]
            if bottoms[-1]["price"] < bottoms[-2]["price"]:
                signal = "buy1"
    elif last and last["type"] == "top":
        signal = "sell2"
        if pivot and closes and closes[-1] < pivot["zd"]:
            signal = "sell3"
        elif len([f for f in fractals if f["type"] == "top"]) >= 2:
            tops = [f for f in fractals if f["type"] == "top"]
            if tops[-1]["price"] > tops[-2]["price"]:
                signal = "sell1"
    return {
        "chan_signal": signal,
        "chan_trend": trend,
        "pivot_low": pivot["zd"] if pivot else None,
        "pivot_high": pivot["zg"] if pivot else None,
        "last_bottom": round(last["price"], 2) if last and last["type"] == "bottom" else None,
        "last_top": round(last["price"], 2) if last and last["type"] == "top" else None,
        "stroke_count": len(strokes),
        "fractal_count": len(fractals),
        "bars_merged": len(bars),
    }

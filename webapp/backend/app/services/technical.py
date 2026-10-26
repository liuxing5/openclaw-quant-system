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


def _direction(s: dict) -> int:
    return 1 if s["end"] >= s["start"] else -1


def _feature(s: dict) -> tuple[float, float]:
    return max(s["start"], s["end"]), min(s["start"], s["end"])


def _segments(strokes: list[dict], strict: bool = True) -> list[dict]:
    """线段：至少 3 笔，用特征序列分型终结，端点取同向笔的极值。

    上涨线段的特征序列是其中的向下笔：连续三根向下笔若中间高点最高，
    线段在特征序列顶分型确认后结束；下跌线段对称。

    ``strict=True`` 时实现「线段破坏的第二种情况」：特征序列分型若出现缺口
    （f3 与 f2 无重叠），必须等后续反向特征笔跌破 f2 低点 / 升破 f2 高点
    才确认线段结束；否则继续延伸。
    """
    n = len(strokes)
    out: list[dict] = []
    i = 0
    while i < n - 1:
        d = _direction(strokes[i])
        end_k = None
        end_price = None
        break_type = "normal"
        for j in range(i + 2, n):
            feats = [s for s in strokes[i + 1 : j + 1] if _direction(s) != d]
            if len(feats) < 3:
                continue
            f1, f2, f3 = feats[-3:]
            h1, l1 = _feature(f1)
            h2, l2 = _feature(f2)
            h3, l3 = _feature(f3)
            fractal = (d == 1 and h2 > h1 and h2 > h3) or (d == -1 and l2 < l1 and l2 < l3)
            if not fractal:
                continue
            gap = (d == 1 and h3 < l2) or (d == -1 and l3 > h2)
            if strict and gap:
                # 缺口后的回抽确认：后续反向特征笔必须破坏 f2 的极值
                confirmed = False
                for k in range(j + 1, n):
                    if _direction(strokes[k]) == d:
                        continue
                    hk, lk = _feature(strokes[k])
                    if d == 1 and lk < l2:
                        confirmed = True
                        break
                    if d == -1 and hk > h2:
                        confirmed = True
                        break
                if not confirmed:
                    continue
                break_type = "gap_confirmed"
            cand = [
                (s["end"], k)
                for k, s in enumerate(strokes)
                if i <= k < j and _direction(s) == d
            ]
            if cand:
                end_price, end_k = max(cand) if d == 1 else min(cand)
            break
        if end_k is None:
            cand = [(s["end"], k) for k, s in enumerate(strokes) if k >= i and _direction(s) == d]
            if not cand:
                break
            end_price, end_k = max(cand) if d == 1 else min(cand)
            break_type = "tail"
        if end_k <= i:
            end_k = i + 1
        out.append(
            {
                "direction": "up" if d == 1 else "down",
                "start": strokes[i]["start"],
                "end": end_price,
                "start_idx": i,
                "end_idx": end_k,
                "break_type": break_type,
            }
        )
        i = end_k + 1
    return out


def _seg_pivots(segments: list[dict]) -> list[dict]:
    out = []
    for i in range(len(segments) - 2):
        a, b, c = segments[i], segments[i + 1], segments[i + 2]
        lows = [min(a["start"], a["end"]), min(b["start"], b["end"]), min(c["start"], c["end"])]
        highs = [max(a["start"], a["end"]), max(b["start"], b["end"]), max(c["start"], c["end"])]
        zd, zg = max(lows), min(highs)
        if zd < zg:
            out.append({"zd": round(zd, 2), "zg": round(zg, 2), "start_idx": i, "end_idx": i + 2})
    return out


def _trend_type(segments: list[dict], pivots: list[dict]) -> tuple[str, str]:
    if len(pivots) >= 2:
        last, prev = pivots[-1], pivots[-2]
        if last["zd"] > prev["zg"]:
            return "上涨趋势", "up"
        if last["zg"] < prev["zd"]:
            return "下跌趋势", "down"
    if len(segments) >= 2:
        last, prev = segments[-1], segments[-2]
        if last["direction"] == prev["direction"] == "up" and last["end"] > prev["end"]:
            return "上涨趋势", "up"
        if last["direction"] == prev["direction"] == "down" and last["end"] < prev["end"]:
            return "下跌趋势", "down"
    if pivots:
        return "盘整", "flat"
    return "无结构", "flat"


def _recursive(segments: list[dict]) -> dict:
    """把线段当作高级别笔，再跑一次线段/中枢，得到线段级别递归结构。"""
    if len(segments) < 3:
        return {
            "level2_segment_count": 0,
            "level2_pivot_count": 0,
            "level2_pivot_low": None,
            "level2_pivot_high": None,
            "level2_trend_type": "无结构",
            "level2_direction": "flat",
            "level2_segment_trend": None,
        }
    higher = [
        {"start": s["start"], "end": s["end"], "start_idx": s["start_idx"], "end_idx": s["end_idx"]}
        for s in segments
    ]
    level2 = _segments(higher, strict=True)
    piv2 = _seg_pivots(level2)
    ttype, tdir = _trend_type(level2, piv2)
    return {
        "level2_segment_count": len(level2),
        "level2_pivot_count": len(piv2),
        "level2_pivot_low": piv2[-1]["zd"] if piv2 else None,
        "level2_pivot_high": piv2[-1]["zg"] if piv2 else None,
        "level2_trend_type": ttype,
        "level2_direction": tdir,
        "level2_segment_trend": level2[-1]["direction"] if level2 else None,
    }


def _points(segments: list[dict], pivots: list[dict], trend: str, macd_info: dict | None) -> tuple[str | None, str | None]:
    if not segments:
        return None, None
    last = segments[-1]
    pivot = pivots[-1] if pivots else None
    dif = (macd_info or {}).get("dif") or 0
    dea = (macd_info or {}).get("dea") or 0
    buy = sell = None
    if pivot and last["direction"] == "up" and last["end"] > pivot["zg"] and last["start"] <= pivot["zg"]:
        buy = "3"
    elif trend == "下跌趋势" and last["direction"] == "down" and dif > dea:
        buy = "1"
    elif trend == "上涨趋势" and last["direction"] == "down" and pivot and last["end"] > pivot["zg"]:
        buy = "2"
    elif pivot and last["direction"] == "down" and last["end"] < pivot["zd"] and last["start"] >= pivot["zd"]:
        sell = "3"
    elif trend == "上涨趋势" and last["direction"] == "up" and dif < dea:
        sell = "1"
    elif trend == "下跌趋势" and last["direction"] == "up" and pivot and last["end"] < pivot["zd"]:
        sell = "2"
    return buy, sell


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


def chan(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    macd_info: dict | None = None,
) -> dict:
    bars = _merge_bars(highs, lows)
    fractals = _fractals(bars)
    strokes = _strokes(fractals)
    segments = _segments(strokes)
    seg_pivots = _seg_pivots(segments)
    trend_type, trend_dir = _trend_type(segments, seg_pivots)
    lvl2 = _recursive(segments)
    buy_point, sell_point = _points(segments, seg_pivots, trend_type, macd_info)
    pivot = _pivot(strokes)
    last = fractals[-1] if fractals else None

    if buy_point:
        signal = f"buy{buy_point}"
    elif sell_point:
        signal = f"sell{sell_point}"
    elif segments and segments[-1]["direction"] == "up":
        signal = "hold"
    else:
        signal = "hold"

    return {
        "chan_signal": signal,
        "chan_trend": trend_dir,
        "pivot_low": pivot["zd"] if pivot else None,
        "pivot_high": pivot["zg"] if pivot else None,
        "last_bottom": round(last["price"], 2) if last and last["type"] == "bottom" else None,
        "last_top": round(last["price"], 2) if last and last["type"] == "top" else None,
        "stroke_count": len(strokes),
        "fractal_count": len(fractals),
        "bars_merged": len(bars),
        # 线段 / 线段中枢 / 走势类型（递归结构）
        "segment_count": len(segments),
        "segment_trend": segments[-1]["direction"] if segments else None,
        "pivot_count": len(seg_pivots),
        "seg_pivot_low": seg_pivots[-1]["zd"] if seg_pivots else None,
        "seg_pivot_high": seg_pivots[-1]["zg"] if seg_pivots else None,
        "trend_type": trend_type,
        "trend_direction": trend_dir,
        "buy_point": buy_point,
        "sell_point": sell_point,
        **lvl2,
    }

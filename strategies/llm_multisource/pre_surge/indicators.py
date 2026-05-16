"""
指标库 — 全部向量化,严格 PIT(无 look-ahead)
================================================
- MACD 8/17/9 短线参数
- 移动均线、量比、ATR
- 缺口检测、连阳检测、涨停检测(支持创业板/科创板/ST)
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ============================================================
# MACD (8/17/9)
# ============================================================
def macd(close: pd.Series, fast: int = 8, slow: int = 17,
         signal: int = 9) -> pd.DataFrame:
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = (dif - dea) * 2
    return pd.DataFrame({"DIF": dif, "DEA": dea, "HIST": hist})


# ============================================================
# 移动均线
# ============================================================
def sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n, min_periods=n).mean()


def ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()


# ============================================================
# ATR (用于动态止损)
# ============================================================
def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


# ============================================================
# 量比
# ============================================================
def volume_ratio(volume: pd.Series, n: int = 20) -> pd.Series:
    avg = volume.rolling(n, min_periods=n).mean()
    return volume / avg.replace(0, float('nan'))


# ============================================================
# 涨停幅度判定
# ============================================================
def detect_limit_up_pct(symbol: str, name: str = "") -> float:
    """
    根据股票代码和名称返回涨停阈值
    - 创业板(300)/科创板(688): 20%
    - ST/*ST: 5%
    - 主板: 10%
    """
    name_upper = (name or "").upper()
    if "ST" in name_upper or "*" in name_upper:
        return 0.048
    if symbol.startswith(("300", "688")):
        return 0.197
    return 0.097


def find_limit_up_days(df: pd.DataFrame, limit_pct: float) -> pd.DataFrame:
    """返回出现涨停的所有日期(按收盘涨幅判断)"""
    pct = df["close"].pct_change()
    return df.loc[pct >= limit_pct - 0.003].copy()


# ============================================================
# 跳空缺口检测
# ============================================================
def find_unfilled_gaps(df: pd.DataFrame,
                       min_gap_pct: float = 0.015,
                       no_fill_days: int = 5,
                       min_volume_ratio: float = 1.5,
                       search_window: int = 20) -> list[dict]:
    """
    寻找最近 search_window 日内的有效未回补向上缺口
    缺口定义: 当日 low > 前日 high
    回补定义: 后续 no_fill_days 内最低价跌破前日 high
    """
    if len(df) < search_window + 2:
        return []
    window = df.tail(search_window).reset_index(drop=True)
    vol_ratio = volume_ratio(df["volume"], 20).tail(search_window).reset_index(drop=True)

    gaps = []
    for i in range(1, len(window) - 1):
        prev_high = window.loc[i - 1, "high"]
        today_low = window.loc[i, "low"]
        today_open = window.loc[i, "open"]
        if today_low <= prev_high:
            continue
        gap_pct = (today_open - prev_high) / prev_high if prev_high > 0 else 0
        vr = vol_ratio.iloc[i] if pd.notna(vol_ratio.iloc[i]) else 0
        if gap_pct < min_gap_pct or vr < min_volume_ratio:
            continue
        # 回补检查
        after = window.iloc[i + 1: i + 1 + no_fill_days]
        if len(after) > 0 and after["low"].min() <= prev_high:
            continue
        gaps.append({
            "date": window.loc[i, "date"],
            "gap_pct": gap_pct,
            "volume_ratio": vr,
            "prev_high": prev_high,
        })
    return gaps


# ============================================================
# 连阳检测
# ============================================================
def longest_yang_run(df: pd.DataFrame, search_window: int = 10,
                     check_ma5: bool = True) -> tuple[int, bool]:
    """
    返回 (最长连阳天数, 期间是否破 5MA)
    """
    if len(df) < search_window + 5:
        return 0, False
    ma5 = sma(df["close"], 5)
    sub = df.tail(search_window).copy()
    sub["yang"] = sub["close"] > sub["open"]
    sub["ma5"] = ma5.tail(search_window).values

    max_run = run = 0
    break_ma5_in_max = False
    cur_break = False
    for _, row in sub.iterrows():
        if row["yang"]:
            run += 1
            if check_ma5 and pd.notna(row["ma5"]) and row["close"] < row["ma5"]:
                cur_break = True
            if run > max_run:
                max_run = run
                break_ma5_in_max = cur_break
        else:
            run = 0
            cur_break = False
    return max_run, break_ma5_in_max


# ============================================================
# MACD 共振判定
# ============================================================
def macd_resonance(macd_df: pd.DataFrame) -> tuple[bool, str]:
    """
    返回 (是否共振, 共振类型)
    共振条件: 金叉 OR DIF 上穿零轴
    """
    if len(macd_df) < 2:
        return False, ""
    dif_t, dif_y = macd_df["DIF"].iloc[-1], macd_df["DIF"].iloc[-2]
    dea_t, dea_y = macd_df["DEA"].iloc[-1], macd_df["DEA"].iloc[-2]
    if dif_y <= dea_y and dif_t > dea_t:
        return True, "golden_cross"
    if dif_y <= 0 < dif_t:
        return True, "zero_cross"
    return False, ""


def macd_dead_cross(macd_df: pd.DataFrame) -> bool:
    """死叉: DIF 下穿 DEA"""
    if len(macd_df) < 2:
        return False
    dif_t, dif_y = macd_df["DIF"].iloc[-1], macd_df["DIF"].iloc[-2]
    dea_t, dea_y = macd_df["DEA"].iloc[-1], macd_df["DEA"].iloc[-2]
    return dif_y >= dea_y and dif_t < dea_t


# ============================================================
# 龙头断板专用指标
# ============================================================
def count_consecutive_limit_ups(df: pd.DataFrame, limit_pct: float) -> int:
    """
    返回最近连续涨停天数(从末尾往前数,遇到非涨停即停)
    如 [6, 涨停, 涨停, 涨停, 非涨停] 返回 3
    """
    if len(df) < 2:
        return 0
    pct = df["close"].pct_change()
    count = 0
    # 从末尾往前,第 -1 是最新日
    for i in range(len(df) - 1, 0, -1):
        if pct.iloc[i] >= limit_pct - 0.003:
            count += 1
        else:
            break
    return count


def max_consecutive_limit_ups_in_window(df: pd.DataFrame, limit_pct: float,
                                         window: int) -> int:
    """
    返回窗口内最长连板数
    (不要求是末尾,寻找任意连续段)
    """
    if len(df) < 2:
        return 0
    sub = df.tail(window + 1).copy()  # +1 因为 pct_change 会丢第一行
    pct = sub["close"].pct_change()
    is_limit = pct >= limit_pct - 0.003
    max_run = run = 0
    for flag in is_limit.fillna(False):
        if flag:
            run += 1
            max_run = max(max_run, run)
        else:
            run = 0
    return max_run


def find_break_board_days(df: pd.DataFrame, limit_pct: float,
                           min_pct: float = -0.03,
                           max_pct: float = 0.09,
                           lookback: int = 3) -> list[dict]:
    """
    寻找最近 lookback 日内的"有效断板日":
      条件 1: 当日未涨停(pct_change < limit_pct)
      条件 2: 当日涨跌幅在 [min_pct, max_pct] 之间
      条件 3: 前一日必须是涨停(否则不算断板,只是普通下跌)

    返回日期和相关信息的列表,按日期新→旧排序
    """
    if len(df) < lookback + 2:
        return []

    pct = df["close"].pct_change()
    sub = df.tail(lookback + 1).copy()  # 多取一行用于看前一日
    sub_pct = pct.tail(lookback + 1).reset_index(drop=True)
    sub = sub.reset_index(drop=True)

    results = []
    # i 从 1 开始(需要前一日参照),跳过最老的那一行
    for i in range(1, len(sub)):
        today_pct = sub_pct.iloc[i]
        prev_pct = sub_pct.iloc[i - 1]
        if pd.isna(today_pct) or pd.isna(prev_pct):
            continue
        # 前一日是涨停
        if prev_pct < limit_pct - 0.003:
            continue
        # 今日未涨停,且在 [min, max] 区间
        if today_pct >= limit_pct - 0.003:
            continue
        if today_pct < min_pct or today_pct > max_pct:
            continue
        results.append({
            "date": sub.iloc[i]["date"],
            "close": float(sub.iloc[i]["close"]),
            "pct_change": float(today_pct),
            "volume": int(sub.iloc[i]["volume"]),
            "prev_volume": int(sub.iloc[i - 1]["volume"]) if i >= 1 else 0,
        })
    # 按日期从新到旧返回
    return sorted(results, key=lambda x: x["date"], reverse=True)


def has_one_word_crash(df: pd.DataFrame, lookback: int = 3,
                       threshold: float = -0.095) -> bool:
    """
    检测最近 lookback 日是否出现一字跌停(炸板信号)
    一字跌停定义: pct_change ≤ -9.5% 且当日 high-low 振幅 < 1%
    """
    if len(df) < lookback + 1:
        return False
    pct = df["close"].pct_change()
    sub = df.tail(lookback).copy()
    sub_pct = pct.tail(lookback).reset_index(drop=True)
    sub = sub.reset_index(drop=True)
    for i in range(len(sub)):
        p = sub_pct.iloc[i]
        if pd.isna(p):
            continue
        if p <= threshold:
            row = sub.iloc[i]
            amplitude = (row["high"] - row["low"]) / row["low"] if row["low"] > 0 else 0
            if amplitude < 0.01:
                return True
    return False

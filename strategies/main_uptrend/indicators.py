"""
技术指标库
===========
全部向量化实现，严格 PIT（无未来函数）。
包含：均线、MACD、量比、箱体、涨停检测、1分钟线日内形态等。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n, min_periods=n).mean()


def ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()


def macd(close: pd.Series, fast: int = 8, slow: int = 17,
         signal: int = 9) -> pd.DataFrame:
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = (dif - dea) * 2
    return pd.DataFrame({"DIF": dif, "DEA": dea, "HIST": hist})


def volume_ratio(volume: pd.Series, n: int = 20) -> pd.Series:
    avg = volume.rolling(n, min_periods=n).mean()
    return volume / avg.replace(0, float('nan'))


def volume_ma(volume: pd.Series, n: int = 60) -> pd.Series:
    return volume.rolling(n, min_periods=n).mean()


def detect_limit_up_pct(symbol: str, name: str = "") -> float:
    name_upper = (name or "").upper()
    if "ST" in name_upper or "*" in name_upper:
        return 0.048
    if symbol.startswith(("300", "688")):
        return 0.197
    return 0.097


def is_limit_up(pct_chg: float, limit_pct: float) -> bool:
    return pct_chg >= limit_pct - 0.003


def box_range(high: pd.Series, low: pd.Series, n: int = 60) -> pd.DataFrame:
    upper = high.rolling(n, min_periods=n).max()
    lower = low.rolling(n, min_periods=n).min()
    return pd.DataFrame({"box_high": upper, "box_low": lower})


def above_box(close: pd.Series, box_high: pd.Series,
              box_low: pd.Series) -> pd.Series:
    return (close > box_high.shift(1)) & (close.shift(1) <= box_high.shift(2))


def near_ma(close: pd.Series, ma: pd.Series, max_pct: float = 0.08) -> pd.Series:
    return (close > ma) & ((close - ma) / ma < max_pct)


def intraday_up_ratio(df_1min: pd.DataFrame) -> float:
    """
    日内上行占比 = 上涨分钟数 / 总分钟数
    df_1min: 1分钟K线 DataFrame (含 close 列)
    返回: 0~1
    """
    if df_1min is None or len(df_1min) < 10:
        return 0.0
    pct_changes = df_1min["close"].pct_change().fillna(0)
    up_bars = (pct_changes > 0).sum()
    total_bars = len(pct_changes)
    return up_bars / total_bars if total_bars > 0 else 0.0


def intraday_morning_checks(df_1min: pd.DataFrame,
                            morning_pct: float = 0.03,
                            morning_amplitude_max: float = 0.02) -> dict:
    """
    上午强势日内走势检测
    条件：10:00前涨幅>3%、且10:00-11:30振幅<2%
    """
    if df_1min is None or len(df_1min) < 30:
        return {"passed": False, "morning_pct": 0, "morning_amp": 0}

    open_price = df_1min["close"].iloc[0]

    before_10 = df_1min[df_1min["time"].astype(str) <= "10:00"]
    if len(before_10) == 0:
        return {"passed": False, "morning_pct": 0, "morning_amp": 0}

    price_at_10 = before_10["close"].iloc[-1]
    morning_pct_val = (price_at_10 - open_price) / open_price if open_price > 0 else 0

    between_10_1130 = df_1min[
        (df_1min["time"].astype(str) >= "10:00") &
        (df_1min["time"].astype(str) <= "11:30")
    ]
    if len(between_10_1130) > 0:
        morning_amp_val = (
            (between_10_1130["high"].max() - between_10_1130["low"].min()) /
            open_price
        ) if open_price > 0 else 0
    else:
        morning_amp_val = 0

    passed = (
        morning_pct_val > morning_pct and
        morning_amp_val < morning_amplitude_max
    )
    return {
        "passed": passed,
        "morning_pct": morning_pct_val,
        "morning_amp": morning_amp_val
    }


def volume_shrink_check(curr_volume: float, prev_volume: float,
                        curr_pct_chg: float) -> bool:
    """
    缩量上涨检测：价涨量缩（缩到前一日60-80%）
    """
    if prev_volume <= 0:
        return False
    ratio = curr_volume / prev_volume
    return curr_pct_chg > 0 and 0.60 <= ratio <= 0.80


def seal_quality_check(seal_time: str, open_times: int,
                       max_seal_time: str = "10:00",
                       max_open_times: int = 0) -> bool:
    """
    封板质量：封板时间早、不开板
    seal_time: "09:35" 格式
    """
    early = seal_time <= max_seal_time
    tight = open_times <= max_open_times
    return early and tight


def sector_peers_rising(sector_peers_pct: List[float],
                        sector_rise_min: float = 0.03,
                        peer_count_min: int = 2) -> dict:
    """
    同板块联动检测
    sector_peers_pct: 同概念板块所有标的当日涨幅
    """
    if not sector_peers_pct:
        return {"passed": False, "sector_avg": 0, "rising_count": 0}

    sector_avg = np.mean(sector_peers_pct)
    rising_count = sum(1 for p in sector_peers_pct if p > 0)

    passed = (
        sector_avg > sector_rise_min and
        rising_count >= peer_count_min
    )
    return {
        "passed": passed,
        "sector_avg": sector_avg,
        "rising_count": rising_count
    }


def consecutive_yang_count(close: pd.Series, n: int = 5) -> int:
    """最近连续阳线天数"""
    count = 0
    for i in range(len(close) - 1, max(len(close) - n - 1, 0), -1):
        if i > 0 and close.iloc[i] > close.iloc[i - 1]:
            count += 1
        else:
            break
    return count
"""不复权行情 → 前复权(qfq)换算共享模块

背景（2026-10-14 审查）：
  daily_quotes 全表为不复权(raw)价且无除权处理，样本内实测 7189 个除权日
  （raw 环比与 pct_chg 背离 >4pp）。除权日会导致：
    - MA/EMA/箱体/布林/ATR 等指标断裂，误判破位；
    - 持仓期止损被除权缺口误触发；
    - 前向收益把会计除权当成亏损（现金分红、送转）。

方案：利用库内 pct_chg（覆盖 99.5%，交易所口径、已含除权信息）复原
总收益链，按"锚定最新交易日"做前复权——最后一根 K 线价 = 原始价，
历史价按累计收益链缩放。无除权时 qfq == raw，完全向后兼容。

用法（单只股票、按交易日升序的 DataFrame）：
    from strategies.shared.adjust import qfq_adjust
    df = qfq_adjust(df)                    # 原地把 open/high/low/close 覆盖为前复权
    raw_close = df["raw_close"].values     # 原始收盘价（除权缺口检测/涨停价仍用 raw）

批量（含 ts_code 分组）：
    df = qfq_grouped(df)                   # 按 ts_code 分组分别换算

矩阵式（forward_stats 的 QuoteMatrix）：
    factors = cum_returns(pct_chg_matrix)  # 返回与矩阵同形的累计收益链
"""
from __future__ import annotations

import numpy as np
import pandas as pd

PRICE_COLS = ("open", "high", "low", "close")


def cum_returns(pct_chg) -> np.ndarray:
    """pct_chg(%) 序列 → 累计收益链 cum_t = Π(1+pct/100)，NaN 按 0 处理。

    输入可为 1D 或 2D ndarray；对 2D 沿 axis=-1 累乘。
    返回与输入同形的 float64 数组（首日 cum = 1+r_0）。
    """
    r = np.asarray(pct_chg, dtype=np.float64)
    r = np.where(np.isfinite(r), r, 0.0) / 100.0
    return np.cumprod(1.0 + r, axis=-1)


def qfq_factors(pct_chg) -> np.ndarray:
    """累计收益链相对末日的比例：factor_t = cum_t / cum_T，末日因子 = 1。

    配合末日收盘价可直接还原前复权收盘：adj_close_t = close_T * factor_t。
    首日 pct_chg 缺失按 0 处理（不影响后续日间收益比）。
    """
    cum = cum_returns(pct_chg)
    last = cum[..., -1:]
    with np.errstate(divide="ignore", invalid="ignore"):
        factor = np.where(np.abs(last) > 1e-12, cum / np.where(np.abs(last) > 1e-12, last, 1.0), 1.0)
    return factor


def qfq_adjust(df: pd.DataFrame, price_cols=PRICE_COLS,
               keep_raw: bool = True) -> pd.DataFrame:
    """单只股票 DataFrame（按交易日升序）原地前复权。

    原理：前复权收盘 adj_t = close_T * cum_t / cum_T（收益链锚定末日），
    同日 OHLC 统一乘当日缩放 s_t = adj_t / close_t。
    无除权时 s_t ≡ 1，qfq == raw。
    依赖列：close、pct_chg。pct_chg 缺失时退化为 close 环比。
    keep_raw=True 时新增 raw_close/raw_open 列保存原始价（涨停价计算、
    除权日检测必须用 raw）。
    """
    if df.empty:
        return df
    close = df["close"].astype("float64")
    if "pct_chg" in df.columns:
        pct = pd.to_numeric(df["pct_chg"], errors="coerce")
        fallback = close.pct_change() * 100.0
        pct = pct.where(pct.notna(), fallback)
    else:
        pct = close.pct_change() * 100.0
    factor = qfq_factors(pct.values)                 # cum_t / cum_T
    close_last = float(close.iloc[-1]) if np.isfinite(close.iloc[-1]) else 1.0
    adj_close = close_last * factor                  # 前复权收盘
    with np.errstate(divide="ignore", invalid="ignore"):
        scale = np.where(np.isfinite(close.values) & (np.abs(close.values) > 1e-12),
                         adj_close / np.where(np.abs(close.values) > 1e-12,
                                              close.values, 1.0), 1.0)
    scale = np.where(np.isfinite(scale), scale, 1.0)

    if keep_raw:
        df["raw_close"] = df["close"]
        if "open" in df.columns:
            df["raw_open"] = df["open"]
    for col in price_cols:
        if col in df.columns:
            df[col] = df[col].astype("float64") * scale
    return df


def qfq_grouped(df: pd.DataFrame, by: str = "ts_code",
                price_cols=PRICE_COLS, keep_raw: bool = True) -> pd.DataFrame:
    """按 ts_code 分组前复权（要求组内已按 trade_date 升序）。"""
    if df.empty or by not in df.columns:
        return df
    parts = []
    for _, g in df.groupby(by, sort=False):
        parts.append(qfq_adjust(g.copy(), price_cols=price_cols,
                                keep_raw=keep_raw))
    return pd.concat(parts, ignore_index=True)

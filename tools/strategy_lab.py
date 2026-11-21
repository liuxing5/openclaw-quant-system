"""Daily portfolio lab for legal (T+1) overnight / 1-day patterns.

Signal at T close; buy at T+1 open; sell at T+2 close (r_oc2) unless stated.
NO look-ahead: selection uses only signal-time columns (`rank_col`).
"""
from __future__ import annotations

import os
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PANEL = os.path.join(ROOT, "data", "research", "panel_cache.parquet")
COST = 0.0015


def load() -> pd.DataFrame:
    df = pd.read_parquet(PANEL)
    df = df[(df.trade_date >= "2024-09-01") & (df.pct_chg.abs() < 21)].copy()
    g = df.groupby("ts_code", sort=False)
    df["vol_ratio"] = df["amount"] / g["amount"].shift(1).rolling(10).mean().reset_index(level=0, drop=True)
    df["gap"] = df["r_overnight"]
    df["r_oo1"] = df["f_open2"] / df["f_open1"] - 1
    df["r_oc2"] = df["f_close2"] / df["f_open1"] - 1
    df["r_oc3"] = df["f_close3"] / df["f_open1"] - 1
    df["ret60"] = g["close"].transform(lambda s: s / s.shift(60) - 1)
    df["buyable_open"] = df["f_open1"].notna() & (df["f_open1"] > 0)
    df["sellable2"] = df["f_close2"].notna()
    df["limit_up_open"] = df["f_open1"] >= df["close"] * (1 + df["board_limit"] - 0.005)
    df["limit_down_open"] = df["f_open1"] <= df["close"] * (1 - df["board_limit"] + 0.005)
    return df


def universe(df: pd.DataFrame) -> pd.Series:
    return (
        (df.amount >= 1e8)
        & (df.close >= 3)
        & (df.circ_mcap_yi >= 20) & (df.circ_mcap_yi <= 3000)
        & (df.pct_chg < df.board_limit * 100 - 0.3)
        & df["buyable_open"] & df["sellable2"]
        & ~df["limit_up_open"] & ~df["limit_down_open"]
    )


def metrics(daily: pd.Series, picks: pd.DataFrame | None = None, ret: str = "r_oc2") -> dict:
    daily = daily.dropna()
    eq = (1 + daily).cumprod()
    dd = eq / eq.cummax() - 1
    years = (daily.index.max() - daily.index.min()).days / 365.25
    out = {
        "days": len(daily), "total": round(eq.iloc[-1] - 1, 4),
        "ann": round(eq.iloc[-1] ** (1 / years) - 1, 4) if years > 0 else np.nan,
        "maxdd": round(dd.min(), 4),
        "sharpe": round(daily.mean() / daily.std() * np.sqrt(252), 2) if daily.std() > 0 else np.nan,
        "win_day": round((daily > 0).mean(), 3), "avg_day": round(daily.mean(), 4),
        "t": round(daily.mean() / (daily.std() / np.sqrt(len(daily))), 2) if len(daily) > 3 else np.nan,
    }
    if picks is not None and len(picks):
        out["trades"] = len(picks)
        out["trades_per_day"] = round(len(picks) / len(daily), 1)
        out["avg_trade"] = round(picks[ret].mean() - COST, 4)
        out["win_trade"] = round((picks[ret] > COST).mean(), 3)
    return out


def simulate(df: pd.DataFrame, mask: pd.Series, rank_col: str | None = None,
             top_n: int = 5, ret: str = "r_oc2", cost: float = COST):
    d = df[mask & df["buyable_open"] & df["sellable2"]].dropna(subset=[ret]).copy()
    if rank_col and rank_col in d.columns:
        d = d.sort_values(["trade_date", rank_col], ascending=[True, True])
        picks = d.groupby("trade_date", sort=True).head(top_n)
    else:
        picks = d
    daily = picks.groupby("trade_date")[ret].mean() - cost
    return metrics(daily, picks, ret), daily, picks


def yearly(daily: pd.Series) -> pd.DataFrame:
    y = daily.groupby(daily.index.year).agg(["size", "mean", lambda s: (s > 0).mean()])
    y.columns = ["days", "mean", "win"]
    y["total"] = (1 + daily).groupby(daily.index.year).prod() - 1
    return y.round(4)


def bench(daily_ref: pd.DataFrame, ret: str = "r_oc2") -> pd.Series:
    return daily_ref.groupby("trade_date")[ret].mean() - COST

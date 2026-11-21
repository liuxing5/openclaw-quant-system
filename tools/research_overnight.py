"""Exploratory analysis of overnight (close -> next open) returns.

Usage:
  python tools/research_overnight.py basic
  python tools/research_overnight.py buckets
  python tools/research_overnight.py rule
  python tools/research_overnight.py ic
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

PANEL = "data/research/panel_cache.parquet"


def load() -> pd.DataFrame:
    df = pd.read_parquet(PANEL)
    df = df[df["trade_date"] >= "2024-09-01"].copy()
    df["vol_ratio"] = df["amount"] / df.groupby("ts_code")["amount"].shift(1).rolling(10).mean().reset_index(level=0, drop=True)
    return df


def tstat(x: pd.Series) -> float:
    x = x.dropna()
    if len(x) < 3:
        return np.nan
    return x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))


def summarize(df: pd.DataFrame, col: str = "r_overnight", by: str | None = None, q: int = 10):
    if by:
        df = df.dropna(subset=[by, col]).copy()
        df["bucket"] = pd.qcut(df[by], q, duplicates="drop")
        out = df.groupby("bucket", observed=True).agg(
            n=(col, "size"), mean=(col, "mean"), median=(col, "median"),
            win=(col, lambda s: (s > 0).mean()), std=(col, "std"),
        )
        out["t"] = out["mean"] / (out["std"] / np.sqrt(out["n"]))
        return out
    s = df[col].dropna()
    return pd.Series({
        "n": len(s), "mean": s.mean(), "median": s.median(),
        "win": (s > 0).mean(), "std": s.std(), "t": tstat(s),
    })


def basic(df: pd.DataFrame):
    print("== overall (2024-09+, all stock-days) ==")
    for col in ["r_overnight", "r_next_intraday", "r_1d", "r_5d", "r_10d", "r_20d"]:
        print(col, summarize(df, col).round(4).to_dict())
    print("\n== by year-half ==")
    df["half"] = df["trade_date"].dt.year.astype(str) + "H" + ((df["trade_date"].dt.month > 6).astype(int) + 1).astype(str)
    for h, sub in df.groupby("half"):
        print(h, summarize(sub, "r_overnight").round(4).to_dict(), "n", len(sub))
    print("\n== by day of week ==")
    df["dow"] = df["trade_date"].dt.dayofweek
    print(df.groupby("dow")["r_overnight"].agg(["size", "mean", "median"]).round(4))
    print("\n== by market breadth quintile ==")
    df["b20q"] = pd.qcut(df["b20"], 5, duplicates="drop")
    print(df.groupby("b20q", observed=True)["r_overnight"].agg(["size", "mean", "median", lambda s: (s > 0).mean()]).round(4))


def buckets(df: pd.DataFrame):
    df = df[df["trade_date"] >= "2025-01-01"]
    for by in ["pct_chg", "turnover_rate", "amount", "circ_mcap_yi", "vol_ratio", "bias_ma5", "ret5", "ret20", "dist_high20", "limit_streak"]:
        print(f"\n== overnight by {by} decile ==")
        print(summarize(df, "r_overnight", by, 10).round(4))
    print("\n== by limit-up status ==")
    print(df.groupby("is_limit_up")["r_overnight"].agg(["size", "mean", "median", lambda s: (s > 0).mean()]).round(4))
    print("\n== by prev-day limit-up ==")
    print(df.groupby("prev_limit_up")["r_overnight"].agg(["size", "mean", "median", lambda s: (s > 0).mean()]).round(4))


def rule(df: pd.DataFrame):
    df = df[df["trade_date"] >= "2025-01-01"].copy()
    m = (
        (((df.pct_chg >= 3) & (df.pct_chg <= 6)) | ((df.pct_chg >= 6) & (df.pct_chg <= 9.7)))
        & (df.amount >= 0.5e8) & (df.amount <= 50e8)
        & (df.turnover_rate >= 5) & (df.turnover_rate <= 10)
        & (df.circ_mcap_yi >= 100) & (df.circ_mcap_yi <= 2000)
        & (df.vol_ratio >= 1.5) & (df.vol_ratio <= 8)
        & (df.close > df.ma5) & (df.ma5 > df.ma10)
        & (df.dist_high20 > -0.08)
    )
    sub = df[m]
    print("8step-like signals", len(sub), "days", sub.trade_date.nunique())
    print(summarize(sub, "r_overnight").round(4))
    print(summarize(sub, "r_1d").round(4))
    print(summarize(sub, "r_5d").round(4))
    print("\nby year:")
    print(sub.groupby(sub.trade_date.dt.year)["r_overnight"].agg(["size", "mean", "median", lambda s: (s > 0).mean()]).round(4))
    print("\nby path-ish pct bucket:")
    sub = sub.copy()
    sub["pctb"] = pd.cut(sub.pct_chg, [3, 4, 5, 6, 7, 8, 9.7])
    print(sub.groupby("pctb", observed=True)["r_overnight"].agg(["size", "mean", "median", lambda s: (s > 0).mean()]).round(4))


def ic(df: pd.DataFrame):
    df = df[df["trade_date"] >= "2025-01-01"].copy()
    feats = ["pct_chg", "turnover_rate", "amount", "circ_mcap_yi", "vol_ratio", "bias_ma5",
             "ret5", "ret20", "dist_high20", "limit_streak", "close_pos", "amount_ratio", "b20"]
    rows = []
    for f in feats:
        tmp = df.dropna(subset=[f, "r_overnight"])
        ic_by_day = tmp.groupby("trade_date").apply(
            lambda g: g[f].rank(pct=True).corr(g["r_overnight"].rank(pct=True)) if len(g) > 30 else np.nan,
            include_groups=False,
        ).dropna()
        rows.append((f, len(ic_by_day), ic_by_day.mean(), tstat(ic_by_day)))
    out = pd.DataFrame(rows, columns=["feature", "n_days", "rank_ic", "t"]).sort_values("rank_ic")
    print(out.round(4).to_string(index=False))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "basic"
    d = load()
    {"basic": basic, "buckets": buckets, "rule": rule, "ic": ic}[cmd](d)

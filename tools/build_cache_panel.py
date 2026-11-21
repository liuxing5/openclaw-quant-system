"""Build research panel from local qfq kline cache (2024-09-02..2026-04-23, 3072 codes).

Output: data/research/panel_cache.parquet
"""
import glob
import os

import numpy as np
import pandas as pd

OUT = "data/research/panel_cache.parquet"


def main():
    mp = pd.read_parquet("data/research/cache_map.parquet")
    fmap = dict(zip(mp.file, mp.ts_code))
    frames = []
    for f in glob.glob("data/*.parquet"):
        fid = os.path.basename(f)[:-8]
        if fid not in fmap:
            continue
        df = pd.read_parquet(f)
        if "date" not in df.columns:
            continue
        df["ts_code"] = fmap[fid]
        frames.append(df[["ts_code", "date", "open", "high", "low", "close", "volume", "amount", "turnover", "pct_change"]])
    df = pd.concat(frames, ignore_index=True)
    df = df.rename(columns={"date": "trade_date", "turnover": "turnover_rate", "pct_change": "pct_chg"})
    df["pct_chg"] = df["pct_chg"] * 100.0  # cache stores fraction; project convention is percent
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    print("rows", len(df), "codes", df.ts_code.nunique(), df.trade_date.min(), df.trade_date.max())

    g = df.groupby("ts_code", sort=False)
    for k in (1, 2, 3, 5, 10, 20):
        df[f"f_open{k}"] = g["open"].shift(-k)
        df[f"f_close{k}"] = g["close"].shift(-k)
        df[f"f_high{k}"] = g["high"].shift(-k)
        df[f"f_low{k}"] = g["low"].shift(-k)
        df[f"f_adj{k}"] = g["close"].shift(-k)
        df[f"f_turn{k}"] = g["turnover_rate"].shift(-k)
        df[f"f_date{k}"] = g["trade_date"].shift(-k)

    df["r_overnight"] = df["f_open1"] / df["close"] - 1.0
    df["r_next_intraday"] = df["f_close1"] / df["f_open1"] - 1.0
    df["r_1d"] = df["f_close1"] / df["close"] - 1.0
    for k in (2, 3, 5, 10, 20):
        df[f"r_{k}d"] = df[f"f_adj{k}"] / df["close"] - 1.0

    df["ma5"] = g["close"].transform(lambda s: s.rolling(5).mean())
    df["ma10"] = g["close"].transform(lambda s: s.rolling(10).mean())
    df["ma20"] = g["close"].transform(lambda s: s.rolling(20).mean())
    df["ma60"] = g["close"].transform(lambda s: s.rolling(60).mean())
    df["ret5"] = g["close"].transform(lambda s: s / s.shift(5) - 1)
    df["ret10"] = g["close"].transform(lambda s: s / s.shift(10) - 1)
    df["ret20"] = g["close"].transform(lambda s: s / s.shift(20) - 1)
    df["high20"] = g["high"].transform(lambda s: s.rolling(20).max())
    df["dist_high20"] = df["close"] / df["high20"] - 1
    df["bias_ma5"] = df["close"] / df["ma5"] - 1
    df["above_ma20"] = (df["close"] > df["ma20"]).astype(float)
    df["ma5_gt_ma10"] = (df["ma5"] > df["ma10"]).astype(float)
    df["close_pos"] = (df["close"] - df["low"]) / (df["high"] - df["low"]).replace(0, np.nan)
    df["amount_ma20"] = g["amount"].transform(lambda s: s.rolling(20).mean())
    df["amount_ratio"] = df["amount"] / df["amount_ma20"]
    df["circ_mcap_yi"] = np.where(df["turnover_rate"] > 0, df["amount"] / (df["turnover_rate"] / 100.0) / 1e8, np.nan)

    board = np.where(df["ts_code"].str.startswith(("300", "301", "688", "689")), 0.20, 0.10)
    df["board_limit"] = board
    df["is_limit_up"] = (df["pct_chg"] >= board * 100 - 0.4).astype(int)
    streak = np.zeros(len(df), dtype=int)
    prev = None
    cur = 0
    for i, (c, lu) in enumerate(zip(df["ts_code"].values, df["is_limit_up"].values)):
        if c != prev:
            cur = 0
            prev = c
        cur = cur + 1 if lu else 0
        streak[i] = cur
    df["limit_streak"] = streak
    df["prev_limit_up"] = g["is_limit_up"].shift(1).fillna(0)
    df["mkt_ret"] = df.groupby("trade_date")["pct_chg"].transform("median")
    breadth = df.groupby("trade_date")["above_ma20"].mean().rename("b20")
    df = df.merge(breadth, left_on="trade_date", right_index=True, how="left")
    df.to_parquet(OUT, index=False)
    print("saved", OUT, df.shape)


if __name__ == "__main__":
    main()

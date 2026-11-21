"""Build a research panel from daily_quotes (2024-09 ~ latest) with total-return
adjusted features and forward overnight / multi-day returns.

Cache: data/research/panel.parquet
"""
from __future__ import annotations

import io
import os
from datetime import date

import numpy as np
import pandas as pd
import psycopg2

DSN = os.environ.get("DATABASE_URL") or (
    "postgresql://postgres.qoakbxswwjqfsgbcgepr:wYFBB91zViSrk2vl"
    "@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres"
)
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "research", "panel.parquet")

SQL = """
select ts_code, trade_date, open, high, low, close, pre_close, pct_chg,
       turnover_rate, amount, volume, volume_ratio, amplitude,
       circulating_market_cap, total_market_cap, limit_up_price, limit_down_price
from daily_quotes
where trade_date >= %s and trade_date < %s and close > 0 and open > 0
"""

MONTHS = [
    "2024-09-01", "2024-10-01", "2024-11-01", "2024-12-01",
    "2025-01-01", "2025-02-01", "2025-03-01", "2025-04-01", "2025-05-01", "2025-06-01",
    "2025-07-01", "2025-08-01", "2025-09-01", "2025-10-01", "2025-11-01", "2025-12-01",
    "2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01", "2026-05-01", "2026-06-01",
    "2026-07-01", "2026-08-01", "2026-09-01", "2026-10-01", "2026-11-01", "2026-12-01",
]
COLS = ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "pct_chg",
        "turnover_rate", "amount", "volume", "volume_ratio", "amplitude",
        "circulating_market_cap", "total_market_cap", "limit_up_price", "limit_down_price"]


def _fetch(start: str, end: str, tries: int = 4) -> pd.DataFrame:
    last = None
    for attempt in range(tries):
        conn = None
        try:
            conn = psycopg2.connect(DSN, connect_timeout=40)
            conn.set_session(readonly=True, autocommit=True)
            cur = conn.cursor()
            cur.execute("set statement_timeout = '300s'")
            cur.execute(SQL, (start, end))
            rows = cur.fetchall()
            cur.close()
            return pd.DataFrame(rows, columns=COLS)
        except Exception as exc:  # noqa: BLE001
            last = exc
            print(f"  retry {start} ({attempt + 1}/{tries}): {exc}")
        finally:
            if conn is not None:
                conn.close()
    raise RuntimeError(f"failed {start}: {last}")


def load() -> pd.DataFrame:
    chunks = []
    for start, end in zip(MONTHS[:-1], MONTHS[1:]):
        df = _fetch(start, end)
        print(f"  {start[:7]}: {len(df)}")
        chunks.append(df)
    return pd.concat(chunks, ignore_index=True)


def build(df: pd.DataFrame) -> pd.DataFrame:
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)

    g = df.groupby("ts_code", sort=False)
    df["prev_close"] = g["close"].shift(1)
    # pre_close is authoritative adjusted previous close; fall back to prev close
    df["pclose"] = df["pre_close"].where(df["pre_close"] > 0, df["prev_close"])
    df["ao"] = df["open"] / df["pclose"]
    df["ac"] = df["close"] / df["pclose"]
    df["ac"] = df["ac"].where(df["ac"].notna(), 1.0)
    df["adj"] = g["ac"].transform(lambda s: s.fillna(1.0).cumprod())

    # forward rows (row adjacency within stock)
    for k in (1, 2, 3, 5, 10, 20):
        df[f"f_ao{k}"] = g["ao"].shift(-k)
        df[f"f_ac{k}"] = g["ac"].shift(-k)
        df[f"f_adj{k}"] = g["adj"].shift(-k)
        df[f"f_date{k}"] = g["trade_date"].shift(-k)
        df[f"f_open{k}"] = g["open"].shift(-k)
        df[f"f_high{k}"] = g["high"].shift(-k)
        df[f"f_low{k}"] = g["low"].shift(-k)
        df[f"f_close{k}"] = g["close"].shift(-k)
        df[f"f_amount{k}"] = g["amount"].shift(-k)
        df[f"f_turn{k}"] = g["turnover_rate"].shift(-k)

    # ---- returns ----
    df["r_overnight"] = df["f_ao1"] - 1.0                # close(t) -> open(t+1)
    df["r_next_intraday"] = df["f_ac1"] / df["f_ao1"] - 1.0   # open(t+1) -> close(t+1)
    df["r_1d"] = df["f_ac1"] - 1.0                        # close(t) -> close(t+1)
    df["r_open1_close1"] = df["f_close1"] / df["f_open1"] - 1.0
    for k in (2, 3, 5, 10, 20):
        df[f"r_{k}d"] = df[f"f_adj{k}"] / df["adj"] - 1.0
    df["r_open1_close2"] = df["f_adj2"] / df["f_ao1"] - 1.0
    df["r_open1_close3"] = df["f_adj3"] / df["f_ao1"] - 1.0
    df["r_open1_close5"] = df["f_adj5"] / df["f_ao1"] - 1.0

    # intraday high after next open (for take-profit simulation)
    df["next_high_gain"] = df["f_high1"] / df["f_open1"] - 1.0
    df["next_low_loss"] = df["f_low1"] / df["f_open1"] - 1.0

    # ---- features (all known at close of t) ----
    window = 20
    df["ma5"] = g["adj"].transform(lambda s: s.rolling(5).mean())
    df["ma10"] = g["adj"].transform(lambda s: s.rolling(10).mean())
    df["ma20"] = g["adj"].transform(lambda s: s.rolling(20).mean())
    df["ma60"] = g["adj"].transform(lambda s: s.rolling(60).mean())
    df["ret5"] = g["adj"].transform(lambda s: s / s.shift(5) - 1)
    df["ret10"] = g["adj"].transform(lambda s: s / s.shift(10) - 1)
    df["ret20"] = g["adj"].transform(lambda s: s / s.shift(20) - 1)
    df["ret60"] = g["adj"].transform(lambda s: s / s.shift(60) - 1)
    df["high20"] = g["high"].transform(lambda s: s.rolling(20).max())
    df["low20"] = g["low"].transform(lambda s: s.rolling(20).min())
    df["dist_high20"] = df["close"] / df["high20"] - 1
    df["bias_ma5"] = df["close"] / df["ma5"] - 1
    df["bias_ma20"] = df["close"] / df["ma20"] - 1
    df["above_ma20"] = (df["close"] > df["ma20"]).astype(float)
    df["ma5_gt_ma10"] = (df["ma5"] > df["ma10"]).astype(float)
    df["close_pos"] = (df["close"] - df["low"]) / (df["high"] - df["low"]).replace(0, np.nan)
    df["amount_ma20"] = g["amount"].transform(lambda s: s.rolling(20).mean())
    df["amount_ratio"] = df["amount"] / df["amount_ma20"]
    df["turn_ma20"] = g["turnover_rate"].transform(lambda s: s.rolling(20).mean())
    df["vol_ma5_prev"] = g["volume"].transform(lambda s: s.shift(1).rolling(5).mean())
    df["vol_ratio_calc"] = df["volume"] / g["volume"].transform(lambda s: s.shift(1).rolling(10).mean())

    # limit-up detection with board-aware threshold (10% / 20% / 30%)
    code_num = df["ts_code"].str[:3]
    board = np.where(
        df["ts_code"].str.startswith(("300", "301", "688", "689")), 0.20,
        np.where(df["ts_code"].str.startswith(("8", "4", "92")), 0.30, 0.10),
    )
    df["board_limit"] = board
    df["is_limit_up"] = (df["pct_chg"] >= board * 100 - 0.4).astype(int)
    df["is_limit_down"] = (df["pct_chg"] <= -board * 100 + 0.4).astype(int)
    # consecutive limit-up streak ending at t
    streak = np.zeros(len(df), dtype=int)
    prev_code = None
    cur = 0
    for i, (code, lu) in enumerate(zip(df["ts_code"].values, df["is_limit_up"].values)):
        if code != prev_code:
            cur = 0
            prev_code = code
        cur = cur + 1 if lu else 0
        streak[i] = cur
    df["limit_streak"] = streak
    # previous day limit-up (首板基因 variants)
    df["prev_limit_up"] = g["is_limit_up"].shift(1).fillna(0)
    df["prev2_limit_up"] = g["is_limit_up"].shift(2).fillna(0)

    # index / market aggregates
    df["mkt_ret"] = df.groupby("trade_date")["pct_chg"].transform("median")
    return df


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    raw = load()
    print("raw rows", len(raw), "codes", raw.ts_code.nunique())
    panel = build(raw)
    # market breadth on eligible universe (non-ST handled later; use all)
    breadth = panel.groupby("trade_date")["above_ma20"].mean().rename("b20")
    panel = panel.merge(breadth, left_on="trade_date", right_index=True, how="left")
    panel.to_parquet(OUT, index=False)
    print("saved", OUT, panel.shape)
    print(panel[["trade_date"]].agg(["min", "max"]))


if __name__ == "__main__":
    main()

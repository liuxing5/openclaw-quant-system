"""从 1 年 A 级名单分层抽 150 只，并计算 next_date。"""
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, ".")
from core.utils.env import load_project_env
from core.db.connection import get_db_fresh

N = 150
SEED = 42


def main():
    a = pd.read_parquet("data/research/a_list_1y.parquet")
    a["trade_date"] = pd.to_datetime(a.trade_date)
    a["ym"] = a.trade_date.dt.strftime("%Y-%m")
    counts = a.groupby("ym").size()
    total = counts.sum()
    rng = np.random.default_rng(SEED)
    parts = []
    for ym, n in counts.items():
        k = max(1, int(round(N * n / total)))
        g = a[a.ym == ym]
        k = min(k, len(g))
        parts.append(g.sample(k, random_state=int(rng.integers(1e9))))
    s = pd.concat(parts).drop_duplicates("ts_code")
    if len(s) < N:
        rest = a[~a.ts_code.isin(s.ts_code)].sample(N - len(s), random_state=SEED)
        s = pd.concat([s, rest])
    s = s.head(N).copy()

    load_project_env()
    conn = get_db_fresh()
    try:
        dates = pd.read_sql(
            "select distinct trade_date from daily_quotes where trade_date between '2025-09-25' and '2026-11-25' order by trade_date",
            conn)
    finally:
        conn.close()
    ds = [pd.Timestamp(x).date() for x in dates.trade_date]
    def nxt(d):
        d = pd.Timestamp(d).date()
        for x in ds:
            if x > d:
                return x
        return None
    s["next_date"] = [nxt(d) for d in s.trade_date]
    s = s.dropna(subset=["next_date"])
    s["sig_date"] = s.trade_date.dt.strftime("%Y-%m-%d")
    s["next_date"] = pd.to_datetime(s.next_date).dt.strftime("%Y-%m-%d")
    cols = ["sig_date", "next_date", "ts_code", "close", "pct_chg", "turnover_rate", "amount",
            "pos", "amount_ratio", "lu20", "prev_ret5", "b20"]
    s = s[[c for c in cols if c in s.columns]]
    s.to_parquet("data/research/a_1y_sample.parquet", index=False)
    print("sample", len(s), "unique codes", s.ts_code.nunique())
    print(s.groupby(s.sig_date.str[:7]).size().to_string())
    print(s.head(3).to_string(index=False))


if __name__ == "__main__":
    main()

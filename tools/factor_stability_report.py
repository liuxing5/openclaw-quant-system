#!/usr/bin/env python3
"""因子稳定性报告:逐月截面 IC vs 20 日 forward return,输出 IC 均值/std/T。

Run:
  python3 tools/factor_stability_report.py \
      --source llm_multisource \
      --table daily_candidates \
      --horizon 20
"""
import argparse
import bisect
import math
import os
from collections import defaultdict

import numpy as np
import pandas as pd
import psycopg2

FACTORS = [
    "mention_count",
    "source_diversity",
    "consensus_score",
    "llm_score",
    "quant_score",
    "final_score",
    "position_pct",
]


def get_conn():
    return psycopg2.connect(
        host=os.environ["POSTGRES_HOST"],
        port=os.environ["POSTGRES_PORT"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ["POSTGRES_DB"],
        sslmode="require",
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", action="append", default=None)
    ap.add_argument("--since", default="2024-01-01")
    ap.add_argument("--horizon", type=int, default=20)
    args = ap.parse_args()

    src_filter = ",".join(["'" + s + "'" for s in (args.source or ["llm_multisource", "overnight_8step"])])
    conn = get_conn()
    df = pd.read_sql(
        f"""
        select snapshot_date, ts_code, mention_count, source_diversity, consensus_score,
               llm_score, quant_score, final_score, position_pct, source
        from daily_candidates
        where source in ({src_filter}) and snapshot_date >= %s
        """,
        conn,
        params=(args.since,),
    )
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"])
    codes = df["ts_code"].unique()
    q = pd.read_sql(
        "select ts_code, trade_date, close from daily_quotes where ts_code in ("
        + ",".join(["'" + c + "'" for c in codes])
        + ")",
        conn,
    )
    q["trade_date"] = pd.to_datetime(q["trade_date"])
    q = q.sort_values(["ts_code", "trade_date"])
    dates_by = {}
    close_by = {}
    for code, g in q.groupby("ts_code"):
        dates_by[code] = g["trade_date"].tolist()
        close_by[code] = g["close"].astype(float).tolist()

    def fwd(code, date, horizon=args.horizon):
        ds = dates_by.get(code)
        if not ds:
            return np.nan
        i = bisect.bisect_left(ds, date)
        if i >= len(ds) or ds[i] != date:
            if i > 0 and ds[i - 1] <= date:
                i -= 1
            else:
                return np.nan
        j = i + horizon
        return close_by[code][j] / close_by[code][i] - 1 if j < len(ds) else np.nan

    df["ret"] = [fwd(r["ts_code"], r["snapshot_date"]) for _, r in df.iterrows()]
    df = df.dropna(subset=["ret"])
    print(f"valid rows: {len(df)}")
    df["ym"] = df["snapshot_date"].dt.to_period("M").astype(str)
    for src in sorted(df["source"].unique()):
        sub = df[df["source"] == src]
        print(f"\n== {src} (n={len(sub)}, 20d mean={sub['ret'].mean():.4f}) ==")
        for f in FACTORS:
            ics = []
            for _, g in sub.groupby("ym"):
                if len(g) < 25:
                    continue
                x = g[f].astype(float)
                y = g["ret"].astype(float)
                if x.std() > 0:
                    ics.append(x.corr(y))
            if not ics:
                continue
            a = np.array(ics)
            mean = a.mean()
            std = a.std(ddof=1) if len(a) > 1 else np.nan
            t = mean / std * math.sqrt(len(a)) if std and std > 0 else np.nan
            print(f"{f:18s} IC_mean={mean:+.3f} std={std:.3f} t={t if not np.isnan(t) else float('nan'):+.2f} months={len(a)}")
    conn.close()


if __name__ == "__main__":
    main()

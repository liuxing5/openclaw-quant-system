"""Map hash-named kline cache files to ts_code by matching daily-return vectors
(pct_change), which are invariant to price adjustment.

Output: data/research/cache_map.parquet
"""
import glob
import os

import numpy as np
import pandas as pd
import psycopg2

DSN = "postgresql://postgres.qoakbxswwjqfsgbcgepr:wYFBB91zViSrk2vl@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres"
OUT = "data/research"


def db_returns(n_days: int = 40) -> pd.DataFrame:
    conn = psycopg2.connect(DSN, connect_timeout=30)
    try:
        cur = conn.cursor()
        cur.execute("""
            select ts_code, trade_date, pct_chg::float8
            from daily_quotes
            where trade_date <= '2026-04-23'
              and trade_date >= (select min(d) from (
                    select distinct trade_date d from daily_quotes
                    where trade_date <= '2026-04-23' order by d desc limit %s) x)
              and (ts_code like '%%.SH' or ts_code like '%%.SZ')
        """, (n_days,))
        rows = cur.fetchall()
    finally:
        conn.close()
    return pd.DataFrame(rows, columns=["ts_code", "trade_date", "pct_chg"])


def main():
    os.makedirs(OUT, exist_ok=True)
    db = db_returns(40)
    pivot = db.pivot_table(index="trade_date", columns="ts_code", values="pct_chg")
    dates = list(pivot.index)
    print("db dates", len(dates), dates[0], dates[-1], "codes", pivot.shape[1])

    files = sorted(glob.glob("data/*.parquet"))
    rows = []
    for f in files:
        df = pd.read_parquet(f)
        if len(df) < 40 or "date" not in df.columns or "pct_change" not in df.columns:
            continue
        s = df.set_index("date")["pct_change"].reindex(dates)
        rows.append((os.path.basename(f)[:-8], s.values))
    names = [r[0] for r in rows]
    mat = np.vstack([r[1] for r in rows]).astype(float)
    print("cache matrix", mat.shape)

    # correlation-based match on the overlapping non-nan part
    w = pivot.values.T.astype(float)  # codes x dates
    mask_db = ~np.isnan(w)
    # normalize per row over valid entries (require full coverage for simplicity)
    full_db = mask_db.all(axis=1)
    w_full = w[full_db]
    codes = pivot.columns[full_db]
    print("db full-coverage codes", len(codes))

    mask_c = ~np.isnan(mat)
    full_c = mask_c.all(axis=1)
    m_full = mat[full_c]
    names_full = [n for n, ok in zip(names, full_c) if ok]
    print("cache full-coverage files", len(names_full))

    wz = (w_full - w_full.mean(axis=1, keepdims=True)) / (w_full.std(axis=1, keepdims=True) + 1e-9)
    mz = (m_full - m_full.mean(axis=1, keepdims=True)) / (m_full.std(axis=1, keepdims=True) + 1e-9)
    corr = mz @ wz.T / w_full.shape[1]  # files x codes
    best = corr.argmax(axis=1)
    best_corr = corr.max(axis=1)
    print("corr quantiles", np.quantile(best_corr, [0.01, 0.1, 0.5, 0.9, 0.99]))

    out = pd.DataFrame({
        "file": names_full,
        "ts_code": [codes[i] for i in best],
        "corr": best_corr,
    })
    out = out[out["corr"] > 0.999]
    # resolve duplicate assignments by highest corr
    out = out.sort_values("corr", ascending=False).drop_duplicates("file").drop_duplicates("ts_code")
    print("matched", len(out), "unique codes", out.ts_code.nunique())
    out.to_parquet(os.path.join(OUT, "cache_map.parquet"), index=False)


if __name__ == "__main__":
    main()

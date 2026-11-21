"""Download daily_quotes into monthly parquet caches (research use).

Usage: python tools/fetch_panel.py 2025-01 2026-11
Writes data/research/raw/YYYY-MM.parquet
"""
from __future__ import annotations

import os
import sys
import time
from datetime import date

import pandas as pd
import psycopg2

DSN = os.environ.get("DATABASE_URL") or (
    "postgresql://postgres.qoakbxswwjqfsgbcgepr:wYFBB91zViSrk2vl"
    "@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres"
)
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "research", "raw")

SQL = """
select ts_code, trade_date, open::float8, high::float8, low::float8, close::float8,
       pre_close::float8, pct_chg::float8, turnover_rate::float8,
       (amount/1e4)::float8 as amount_wan, (volume/1e4)::float8 as volume_wan,
       volume_ratio::float8, (circulating_market_cap/1e8)::float8 as circ_mcap_yi
from daily_quotes
where trade_date >= %s and trade_date < %s
  and close > 0 and open > 0 and (ts_code like '%%.SH' or ts_code like '%%.SZ')
"""

COLS = ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "pct_chg",
        "turnover_rate", "amount_wan", "volume_wan", "volume_ratio", "circ_mcap_yi"]


def months(start: str, end: str) -> list[tuple[str, str]]:
    y, m = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    out = []
    while (y, m) <= (ey, em):
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        out.append((f"{y:04d}-{m:02d}-01", f"{ny:04d}-{nm:02d}-01"))
        y, m = ny, nm
    return out


def fetch(start: str, end: str, tries: int = 3) -> pd.DataFrame:
    last = None
    for attempt in range(tries):
        conn = None
        try:
            conn = psycopg2.connect(DSN, connect_timeout=30)
            conn.set_session(readonly=True, autocommit=True)
            cur = conn.cursor()
            cur.execute("set statement_timeout = '600s'")
            cur.execute(SQL, (start, end))
            rows = cur.fetchall()
            cur.close()
            return pd.DataFrame(rows, columns=COLS)
        except Exception as exc:  # noqa: BLE001
            last = exc
            print(f"  retry {start}: {exc}", flush=True)
            time.sleep(5)
        finally:
            if conn is not None:
                conn.close()
    raise RuntimeError(last)


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else "2025-01"
    end = sys.argv[2] if len(sys.argv) > 2 else "2026-11"
    os.makedirs(OUT, exist_ok=True)
    for s, e in months(start, end):
        tag = s[:7]
        path = os.path.join(OUT, f"{tag}.parquet")
        if os.path.exists(path):
            print(f"skip {tag}", flush=True)
            continue
        t0 = time.time()
        df = fetch(s, e)
        df.to_parquet(path, index=False)
        print(f"{tag}: {len(df)} rows in {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()

"""Download daily_quotes in small date windows (pooler-friendly).

Usage: python tools/fetch_panel_chunks.py 2026-05-11 2026-11-21
Writes data/research/raw/chunk_YYYY-MM-DD.parquet (10 calendar days each).
"""
from __future__ import annotations

import os
import sys
import time
from datetime import date, timedelta

import pandas as pd
import psycopg2

DSN = os.environ.get("DATABASE_URL") or (
    "postgresql://postgres.qoakbxswwjqfsgbcgepr:wYFBB91zViSrk2vl"
    "@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres"
)
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "research", "raw")

SQL = """
select ts_code, trade_date, open::float8, high::float8, low::float8, close::float8,
       pct_chg::float8, turnover_rate::float8, (amount/1e4)::float8 as amount_wan,
       (circulating_market_cap/1e8)::float8 as circ_mcap_yi
from daily_quotes
where trade_date >= %s and trade_date < %s
  and close > 0 and open > 0 and (ts_code like '%%.SH' or ts_code like '%%.SZ')
"""

COLS = ["ts_code", "trade_date", "open", "high", "low", "close", "pct_chg",
        "turnover_rate", "amount_wan", "circ_mcap_yi"]


def fetch(start: str, end: str, tries: int = 2) -> pd.DataFrame:
    last = None
    for attempt in range(tries):
        conn = None
        try:
            conn = psycopg2.connect(DSN, connect_timeout=30, keepalives=1, keepalives_idle=20, keepalives_interval=5, keepalives_count=3)
            conn.set_session(readonly=True, autocommit=True)
            cur = conn.cursor()
            cur.execute("set statement_timeout = '180s'")
            cur.execute(SQL, (start, end))
            rows = cur.fetchall()
            cur.close()
            return pd.DataFrame(rows, columns=COLS)
        except Exception as exc:  # noqa: BLE001
            last = exc
            print(f"  retry {start}: {exc}", flush=True)
            time.sleep(3)
        finally:
            if conn is not None:
                conn.close()
    raise RuntimeError(last)


def main():
    start = date.fromisoformat(sys.argv[1] if len(sys.argv) > 1 else "2026-05-11")
    end = date.fromisoformat(sys.argv[2] if len(sys.argv) > 2 else "2026-11-21")
    os.makedirs(OUT, exist_ok=True)
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=5), end)
        path = os.path.join(OUT, f"chunk_{cur.isoformat()}.parquet")
        if os.path.exists(path):
            print(f"skip {cur}", flush=True)
        else:
            t0 = time.time()
            df = fetch(cur.isoformat(), nxt.isoformat())
            df.to_parquet(path, index=False)
            print(f"{cur}..{nxt}: {len(df)} rows in {time.time()-t0:.0f}s", flush=True)
        cur = nxt


if __name__ == "__main__":
    main()


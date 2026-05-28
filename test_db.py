import psycopg2
import pandas as pd
import time
from datetime import timedelta, datetime
from psycopg2.extras import RealDictCursor

DB_URL = "postgresql://postgres.qoakbxswwjqfsgbcgepr:wYFBB91zViSrk2vl@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres"

print("Connecting to database...")
t0 = time.time()
conn = psycopg2.connect(DB_URL, connect_timeout=60)
conn.autocommit = True
print(f"Connected in {time.time()-t0:.1f}s")

cur = conn.cursor()
print("Setting timeout...")
cur.execute("SET statement_timeout = '300000'")
cur.close()

start_date = "2025-05-01"
end_date = "2025-08-31"
sd = datetime.strptime(start_date, "%Y-%m-%d")
lookback_start = (sd - timedelta(days=30)).strftime("%Y-%m-%d")

print(f"Executing query: {lookback_start} to {end_date}...")
t0 = time.time()
cur = conn.cursor(cursor_factory=RealDictCursor)
cur.execute("""
    SELECT ts_code, trade_date, open, high, low, close,
           volume, amount, pct_chg, turnover_rate
    FROM daily_quotes
    WHERE trade_date >= %s AND trade_date <= %s
      AND pct_chg IS NOT NULL
      AND amount IS NOT NULL
      AND turnover_rate IS NOT NULL
    ORDER BY ts_code, trade_date
""", (lookback_start, end_date))

print("Fetching data...")
rows = cur.fetchall()
cur.close()
conn.close()
print(f"Fetched {len(rows)} rows in {time.time()-t0:.1f}s")

print("Creating DataFrame...")
df = pd.DataFrame(rows)
print(f"DataFrame shape: {df.shape}")
print("Done!")

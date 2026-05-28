import psycopg2
import pandas as pd
import time
import pickle
from datetime import timedelta, datetime
from psycopg2.extras import RealDictCursor

DB_URL = "postgresql://postgres.qoakbxswwjqfsgbcgepr:wYFBB91zViSrk2vl@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres"

print("Waiting for connection pool to free up...")
time.sleep(30)

print("Connecting to database...")
max_retries = 10
for attempt in range(max_retries):
    try:
        conn = psycopg2.connect(DB_URL, connect_timeout=60)
        conn.autocommit = True
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SET statement_timeout = '300s'")
        print(f"Connected on attempt {attempt+1}")
        break
    except Exception as e:
        print(f"Attempt {attempt+1} failed: {e}")
        time.sleep(10)
else:
    print("Failed to connect after all retries")
    exit(1)

start_date = "2025-05-01"
end_date = "2025-08-31"
sd = datetime.strptime(start_date, "%Y-%m-%d")
lookback_start = (sd - timedelta(days=30)).strftime("%Y-%m-%d")

print(f"Loading data from {lookback_start} to {end_date}...")
t0 = time.time()

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

rows = cur.fetchall()
cur.close()
conn.close()

print(f"Fetched {len(rows)} rows in {time.time()-t0:.1f}s")

df = pd.DataFrame(rows)
for col in ["open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover_rate"]:
    df[col] = pd.to_numeric(df[col], errors="coerce")

df = df.dropna(subset=["close", "pct_chg", "amount", "turnover_rate"])
df["trade_date"] = pd.to_datetime(df["trade_date"])
df = df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)

print(f"DataFrame shape: {df.shape}")
print(f"Stocks: {df['ts_code'].nunique()}")
print(f"Dates: {df['trade_date'].min().date()} ~ {df['trade_date'].max().date()}")

# Save to pickle for fast loading
with open("data_cache.pkl", "wb") as f:
    pickle.dump(df, f)
print("Data saved to data_cache.pkl")

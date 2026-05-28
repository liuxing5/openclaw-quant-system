import psycopg2
import pandas as pd
import time
from datetime import timedelta, datetime

DB_URL = "postgresql://postgres.qoakbxswwjqfsgbcgepr:wYFBB91zViSrk2vl@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres"

print("Connecting to database...")
conn = psycopg2.connect(DB_URL, connect_timeout=60)
conn.autocommit = True
cur = conn.cursor()

start_date = "2025-05-01"
end_date = "2026-05-25"
sd = datetime.strptime(start_date, "%Y-%m-%d")
lookback_start = (sd - timedelta(days=60)).strftime("%Y-%m-%d")

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

print(f"Loaded {len(rows)} rows in {time.time()-t0:.1f}s")

df = pd.DataFrame(rows)
for col in ["open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover_rate"]:
    df[col] = pd.to_numeric(df[col], errors="coerce")

df = df.dropna(subset=["close", "pct_chg", "amount", "turnover_rate"])
df["trade_date"] = pd.to_datetime(df["trade_date"])
df = df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)

print(f"DataFrame shape: {df.shape}")
print(f"Stocks: {df['ts_code'].nunique()}")
print(f"Dates: {df['trade_date'].min().date()} ~ {df['trade_date'].max().date()}")

print("Computing MA5...")
t0 = time.time()
df["ma5"] = df.groupby("ts_code")["close"].transform(
    lambda x: x.rolling(5, min_periods=5).mean().shift(1)
)
print(f"MA5 done in {time.time()-t0:.1f}s")

print("Computing MA20...")
t0 = time.time()
df["ma20"] = df.groupby("ts_code")["close"].transform(
    lambda x: x.rolling(20, min_periods=20).mean().shift(1)
)
print(f"MA20 done in {time.time()-t0:.1f}s")

print("Computing vol_ratio...")
t0 = time.time()
avg_vol = df.groupby("ts_code")["volume"].transform(
    lambda x: x.rolling(10, min_periods=5).mean().shift(1)
)
df["vol_ratio"] = df["volume"] / avg_vol
print(f"vol_ratio done in {time.time()-t0:.1f}s")

print("Computing down_days...")
t0 = time.time()

def calc_down_days(pct_series):
    down_days = [0] * len(pct_series)
    for i in range(len(pct_series)):
        if pct_series.iloc[i] < 0:
            down_days[i] = down_days[i - 1] + 1 if i > 0 else 1
        else:
            down_days[i] = 0
    return pd.Series(down_days, index=pct_series.index)

df["down_days"] = df.groupby("ts_code")["pct_chg"].transform(calc_down_days)
print(f"down_days done in {time.time()-t0:.1f}s")

print("Computing no_new_low_days...")
t0 = time.time()

def calc_no_new_low(low_series):
    no_new_low = [0] * len(low_series)
    for i in range(1, len(low_series)):
        if low_series.iloc[i] >= low_series.iloc[i-1]:
            no_new_low[i] = no_new_low[i-1] + 1
        else:
            no_new_low[i] = 0
    return pd.Series(no_new_low, index=low_series.index)

df["no_new_low_days"] = df.groupby("ts_code")["low"].transform(calc_no_new_low)
print(f"no_new_low_days done in {time.time()-t0:.1f}s")

print("All indicators computed successfully!")
print(f"Final shape: {df.shape}")

# Test filter
day_df = df[df["trade_date"] == pd.Timestamp("2025-05-06")]
print(f"\nTest date 2025-05-06: {len(day_df)} rows")

# Dip filter
mask = (
    (day_df["pct_chg"] >= -6.0) &
    (day_df["pct_chg"] <= -3.0) &
    (day_df["vol_ratio"] <= 0.8) &
    (day_df["vol_ratio"] > 0) &
    (day_df["ma20"].notna()) &
    (day_df["close"] >= day_df["ma20"] * 0.95) &
    (day_df["close"] <= day_df["ma20"] * 1.05) &
    (day_df["ma5"].notna()) &
    (day_df["close"] < day_df["ma5"] * 0.97) &
    (day_df["down_days"] >= 2) &
    (day_df["turnover_rate"] >= 3.0) &
    (day_df["turnover_rate"] <= 15.0) &
    (day_df["amount"] >= 30_000_000) &
    (day_df["close"] > 3.0)
)
dip_cands = day_df[mask]
print(f"Dip candidates: {len(dip_cands)}")

# Stabilize filter
mask2 = (
    (day_df["no_new_low_days"] >= 2) &
    (day_df["pct_chg"] >= 2.0) &
    (day_df["vol_ratio"] >= 1.5) &
    (day_df["ma5"].notna()) &
    (day_df["close"] > day_df["ma5"]) &
    (day_df["turnover_rate"] >= 3.0) &
    (day_df["turnover_rate"] <= 15.0) &
    (day_df["amount"] >= 30_000_000) &
    (day_df["close"] > 3.0)
)
stab_cands = day_df[mask2]
print(f"Stabilize candidates: {len(stab_cands)}")

print("\nDone!")

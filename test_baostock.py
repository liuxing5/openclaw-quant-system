"""Test baostock connection and basic queries."""
import sys, os
sys.path.insert(0, '.')
from core.utils.env import load_project_env
load_project_env()

import baostock as bs

print("Logging in to baostock...", flush=True)
lg = bs.login()
print(f"Login: error_code={lg.error_code}, error_msg={lg.error_msg}", flush=True)

# Test trading calendar
print("\nQuerying trading dates 2026-01-02 to 2026-01-10...", flush=True)
rs = bs.query_trade_dates(start_date="2026-01-02", end_date="2026-01-10")
data = []
while rs.next():
    data.append(rs.get_row_data())
print(f"Trading dates: {data}", flush=True)

# Test stock list with a known trading day
print("\nQuerying all stocks for 2026-01-02...", flush=True)
rs = bs.query_all_stock(day="2026-01-02")
count = 0
sample = []
while rs.next():
    row = rs.get_row_data()
    count += 1
    if count <= 5:
        sample.append(row)
print(f"Total stocks: {count}, sample: {sample}", flush=True)

# Test K-line data
print("\nQuerying K-line for sh.600519 (Jan 2026)...", flush=True)
rs = bs.query_history_k_data_plus(
    "sh.600519",
    "date,code,close,pctChg,turn,amount,peTTM,pbMRQ,isST",
    start_date="2026-01-01",
    end_date="2026-01-10",
    frequency="d",
    adjustflag="3",
)
data = []
while rs.next():
    data.append(rs.get_row_data())
print(f"K-line data: {data}", flush=True)

bs.logout()
print("\nDone!", flush=True)

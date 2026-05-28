"""测试：逐步加载模块后的内存情况"""
import sys, time, os
sys.path.insert(0, '.')

# Step 1: 基础
import baostock as bs
print(f"Step 1 - after baostock import: OK")

# Step 2: load_project_env
from core.utils.env import load_project_env
load_project_env()
print(f"Step 2 - after load_project_env: OK")

# Step 3: db connection
from core.db.connection import get_db_fresh, db_configured
print(f"Step 3 - after db import: OK, db_configured={db_configured()}")

# Step 4: candidates
from core.db.candidates import write_candidates
print(f"Step 4 - after candidates import: OK")

# Step 5: zuiyou1
from strategies.overnight_8step.zuiyou1 import analyze_industry
print(f"Step 5 - after zuiyou1 import: OK")

# Step 6: 测试 baostock
lg = bs.login()
print(f"Step 6 - baostock login: {lg.error_code}")

FIELDS = "date,code,open,high,low,close,volume,amount,turn,pctChg,peTTM,pbMRQ,isST"
rs = bs.query_history_k_data_plus(
    'sh.600000', FIELDS,
    start_date='2025-11-02', end_date='2026-05-27',
    frequency='d', adjustflag='3',
)
rows = []
while rs.next():
    rows.append(rs.get_row_data())
print(f"Step 6 - baostock query: {len(rows)} rows")

bs.logout()
print("All OK!")

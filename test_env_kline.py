"""测试：在 load_project_env 后 baostock 是否正常"""
import sys, time
sys.path.insert(0, '.')
from core.utils.env import load_project_env
load_project_env()

import baostock as bs

lg = bs.login()
print(f"login: {lg.error_code} {lg.error_msg}")

FIELDS = "date,code,open,high,low,close,volume,amount,turn,pctChg,peTTM,pbMRQ,isST"

# 测试 20 只股票
codes = ['sh.600000', 'sh.600009', 'sh.600010', 'sh.600011', 'sh.600015',
         'sh.600016', 'sh.600018', 'sh.600019', 'sh.600023', 'sh.600025',
         'sh.600027', 'sh.600028', 'sh.600029', 'sh.600030', 'sh.600031',
         'sh.600036', 'sh.600048', 'sh.600050', 'sh.600061', 'sh.600066']

t0 = time.time()
for i, code in enumerate(codes):
    t1 = time.time()
    rs = bs.query_history_k_data_plus(
        code, FIELDS,
        start_date='2025-11-02', end_date='2026-05-27',
        frequency='d', adjustflag='3',
    )
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    elapsed = time.time() - t1
    print(f"  {i+1}/20 {code}: {len(rows)} rows ({elapsed:.2f}s) err={rs.error_code}")

print(f"\nTotal: {time.time()-t0:.1f}s")
bs.logout()

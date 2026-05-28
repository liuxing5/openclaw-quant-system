"""快速测试批量K线加载"""
import sys, time
sys.path.insert(0, '.')
from core.utils.env import load_project_env
load_project_env()

import baostock as bs
lg = bs.login()
print(f"login: {lg.error_code}")

# 获取股票池
t0 = time.time()
rs = bs.query_hs300_stocks()
hs300 = []
while rs.next():
    hs300.append(rs.get_row_data()[1])
print(f"hs300: {len(hs300)} ({time.time()-t0:.1f}s)")

rs = bs.query_zz500_stocks()
zz500 = []
while rs.next():
    zz500.append(rs.get_row_data()[1])
print(f"zz500: {len(zz500)} ({time.time()-t0:.1f}s)")

all_codes = sorted(set(hs300) | set(zz500))
print(f"pool: {len(all_codes)}")

# 测试批量K线加载（前50只）
FIELDS = "date,code,open,high,low,close,volume,amount,turn,pctChg,peTTM,pbMRQ,isST"
t1 = time.time()
ok = 0
total_rows = 0
for i, code in enumerate(all_codes[:50]):
    rs = bs.query_history_k_data_plus(
        code, FIELDS,
        start_date='2025-11-02', end_date='2026-01-31',
        frequency='d', adjustflag='3',
    )
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    if rows:
        ok += 1
        total_rows += len(rows)
    if (i+1) % 10 == 0:
        print(f"  progress: {i+1}/50, ok={ok}, rows={total_rows} ({time.time()-t1:.1f}s)")

print(f"\n50 stocks: ok={ok}, total_rows={total_rows} ({time.time()-t1:.1f}s)")
print(f"Estimated 800 stocks: {(time.time()-t1)/50*800:.0f}s")

bs.logout()

"""测试完整字段 K 线获取速度"""
import baostock as bs
import time

FIELDS = "date,code,open,high,low,close,volume,amount,turn,pctChg,peTTM,pbMRQ,isST"

lg = bs.login()
print(f"login: {lg.error_code}")

# Test: 10 stocks, full date range
codes = ['sh.600000', 'sh.600009', 'sh.600010', 'sh.600011', 'sh.600015',
         'sh.600016', 'sh.600018', 'sh.600019', 'sh.600023', 'sh.600025']

t0 = time.time()
for code in codes:
    t1 = time.time()
    rs = bs.query_history_k_data_plus(
        code, FIELDS,
        start_date='2025-11-02', end_date='2026-05-27',
        frequency='d', adjustflag='3',
    )
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    print(f"  {code}: {len(rows)} rows ({time.time()-t1:.2f}s)")

print(f"\nTotal: {time.time()-t0:.1f}s for 10 stocks")
print(f"Estimated 800 stocks: {(time.time()-t0)/10*800:.0f}s")

bs.logout()

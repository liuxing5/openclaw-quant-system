"""测试 hs300 前50只股票的K线获取"""
import baostock as bs
import time

lg = bs.login()
print(f"login: {lg.error_code}")

FIELDS = "date,code,open,high,low,close,volume,amount,turn,pctChg,peTTM,pbMRQ,isST"

# 获取 hs300
rs = bs.query_hs300_stocks()
hs300 = []
while rs.next():
    hs300.append(rs.get_row_data()[1])
print(f"hs300: {len(hs300)} stocks")

# 测试前50只
t0 = time.time()
for i, code in enumerate(hs300[:50]):
    t1 = time.time()
    try:
        rs = bs.query_history_k_data_plus(
            code, FIELDS,
            start_date='2025-11-02', end_date='2026-05-27',
            frequency='d', adjustflag='3',
        )
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        elapsed = time.time() - t1
        print(f"  {i+1}/50 {code}: {len(rows)} rows ({elapsed:.2f}s) err={rs.error_code}")
        if elapsed > 5:
            print(f"    ⚠️ SLOW!")
    except Exception as e:
        print(f"  {i+1}/50 {code}: ERROR {e}")

print(f"\nTotal: {time.time()-t0:.1f}s")
bs.logout()

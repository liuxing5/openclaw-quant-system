"""测试 baostock 2026 年数据"""
import baostock as bs
import time

lg = bs.login()
print(f"login: {lg.error_code} {lg.error_msg}")

# Test 1: 2025年数据
print("\n=== Test 1: 2025-11-02 ~ 2025-12-31 ===")
t0 = time.time()
rs = bs.query_history_k_data_plus(
    'sh.600000', 'date,close',
    start_date='2025-11-02', end_date='2025-12-31',
    frequency='d', adjustflag='3',
)
rows = []
while rs.next():
    rows.append(rs.get_row_data())
print(f"  rows: {len(rows)} ({time.time()-t0:.1f}s)")
if rows:
    print(f"  last: {rows[-1]}")

# Test 2: 2026年1月
print("\n=== Test 2: 2026-01-01 ~ 2026-01-31 ===")
t0 = time.time()
rs = bs.query_history_k_data_plus(
    'sh.600000', 'date,close',
    start_date='2026-01-01', end_date='2026-01-31',
    frequency='d', adjustflag='3',
)
rows = []
while rs.next():
    rows.append(rs.get_row_data())
print(f"  rows: {len(rows)} ({time.time()-t0:.1f}s)")
if rows:
    print(f"  last: {rows[-1]}")

# Test 3: 2026年2月
print("\n=== Test 3: 2026-02-01 ~ 2026-02-28 ===")
t0 = time.time()
rs = bs.query_history_k_data_plus(
    'sh.600000', 'date,close',
    start_date='2026-02-01', end_date='2026-02-28',
    frequency='d', adjustflag='3',
)
rows = []
while rs.next():
    rows.append(rs.get_row_data())
print(f"  rows: {len(rows)} ({time.time()-t0:.1f}s)")
if rows:
    print(f"  last: {rows[-1]}")

# Test 4: 2026年5月
print("\n=== Test 4: 2026-05-01 ~ 2026-05-27 ===")
t0 = time.time()
rs = bs.query_history_k_data_plus(
    'sh.600000', 'date,close',
    start_date='2026-05-01', end_date='2026-05-27',
    frequency='d', adjustflag='3',
)
rows = []
while rs.next():
    rows.append(rs.get_row_data())
print(f"  rows: {len(rows)} ({time.time()-t0:.1f}s)")
if rows:
    print(f"  last: {rows[-1]}")

bs.logout()
print("\nDone!")

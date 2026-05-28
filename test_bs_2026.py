"""测试 baostock 对 2026 年数据的支持"""
import baostock as bs

lg = bs.login()
print(f"Login: {lg.error_code} {lg.error_msg}")

# 1. query_all_stock
print("\n=== query_all_stock 2026-01-05 ===")
rs = bs.query_all_stock(day='2026-01-05')
data = []
while rs.next():
    data.append(rs.get_row_data())
print(f"Total: {len(data)}")
if data:
    for r in data[:5]:
        print(r)

# 2. query_hs300_stocks
print("\n=== query_hs300_stocks ===")
rs = bs.query_hs300_stocks()
data = []
while rs.next():
    data.append(rs.get_row_data())
print(f"Total: {len(data)}")
if data:
    for r in data[:3]:
        print(r)

# 3. query_zz500_stocks
print("\n=== query_zz500_stocks ===")
rs = bs.query_zz500_stocks()
data = []
while rs.next():
    data.append(rs.get_row_data())
print(f"Total: {len(data)}")

# 4. K线数据
print("\n=== K线 sh.600000 2026-01-02~2026-01-10 ===")
rs = bs.query_history_k_data_plus(
    'sh.600000', 'date,close,volume,amount,turn,pctChg',
    start_date='2026-01-02', end_date='2026-01-10',
    frequency='d', adjustflag='3',
)
data = []
while rs.next():
    data.append(rs.get_row_data())
print(f"Total: {len(data)}")
for r in data:
    print(r)

bs.logout()
print("\nDone!")

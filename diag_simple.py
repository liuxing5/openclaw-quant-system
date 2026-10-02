"""诊断关键股票数据 - 写文件"""
import sys, os
sys.path.insert(0, '.')


from core.db.connection import get_db
import pandas as pd

conn = get_db()

lines = []

stocks = [
    ('002565.SZ', '2026-01-08', '2026-01-20'),
    ('002361.SZ', '2026-03-20', '2026-04-01'),
    ('002384.SZ', '2026-03-25', '2026-04-10'),
    ('301308.SZ', '2025-09-01', '2025-09-15'),
    ('301308.SZ', '2026-01-01', '2026-01-15'),
]

for code, start, end in stocks:
    lines.append(f"\n=== {code} {start} ~ {end} ===")
    df = pd.read_sql(f"""
        SELECT ts_code, trade_date, close, amount/10000 as amt_w, pct_chg
        FROM daily_quotes
        WHERE ts_code='{code}' AND trade_date >= '{start}' AND trade_date <= '{end}'
        ORDER BY trade_date
    """, conn)
    if len(df) > 0:
        lines.append(df.to_string(index=False))
    else:
        lines.append("NO DATA!")

conn.close()
lines.append("\nDONE")

result = '\n'.join(lines)
with open('diag_result.txt', 'w', encoding='utf-8') as f:
    f.write(result)
print(result)

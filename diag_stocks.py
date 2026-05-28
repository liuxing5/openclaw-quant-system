"""诊断用户反馈的4只股票为何入场偏晚"""
import sys

# 重定向输出到文件
out = open('diag_stocks_result.txt', 'w', encoding='utf-8')
_orig_stdout = sys.stdout
sys.stdout = out

from core.db.connection import get_db

db = get_db()
cur = db.cursor()

stocks = [
    ('002361.SZ', '2026-03-20', '2026-04-10'),
    ('002384.SZ', '2026-03-25', '2026-04-10'),
    ('301308.SZ', '2025-09-01', '2026-02-01'),
    ('002565.SZ', '2026-01-06', '2026-01-15'),
]

for ts_code, start, end in stocks:
    cur.execute("""
        SELECT trade_date, open, close, pct_chg, amount
        FROM daily_quotes
        WHERE ts_code = %s AND trade_date BETWEEN %s AND %s
        ORDER BY trade_date
    """, (ts_code, start, end))
    rows = cur.fetchall()
    print(f"\n=== {ts_code} ({start} ~ {end}) ===")
    for r in rows:
        amt_wan = r['amount'] / 10000 if r['amount'] else 0
        print(f"  {r['trade_date']} C={r['close']:.2f} pct={r['pct_chg']:.2f}% amt={amt_wan:.0f}万")

# 检查这些股票在目标日期是否满足趋势启动条件
print("\n\n=== 趋势启动条件检查 ===")
for ts_code, start, end in stocks:
    cur.execute("""
        SELECT trade_date, pct_chg, amount
        FROM daily_quotes
        WHERE ts_code = %s
          AND pct_chg BETWEEN 1 AND 7
          AND amount > 50000000
          AND trade_date BETWEEN %s AND %s
        ORDER BY trade_date
    """, (ts_code, start, end))
    rows = cur.fetchall()
    print(f"\n{ts_code} 满足趋势启动条件的日期:")
    for r in rows:
        print(f"  {r['trade_date']} pct={r['pct_chg']:.2f}% amt={r['amount']/10000:.0f}万")

# 检查002361在3月25-26日的具体数据
print("\n\n=== 002361 详细分析 ===")
cur.execute("""
    SELECT trade_date, open, close, high, low, pct_chg, amount, vol
    FROM daily_quotes
    WHERE ts_code = '002361.SZ'
      AND trade_date BETWEEN '2026-03-23' AND '2026-03-27'
    ORDER BY trade_date
""")
rows = cur.fetchall()
for r in rows:
    print(f"  {r['trade_date']} O={r['open']:.2f} C={r['close']:.2f} H={r['high']:.2f} L={r['low']:.2f} pct={r['pct_chg']:.2f}% amt={r['amount']/10000:.0f}万")

# 检查301308的长期走势
print("\n\n=== 301308 长期走势(月度) ===")
cur.execute("""
    SELECT DATE_TRUNC('month', trade_date) as m,
           MIN(close) as low_c, MAX(close) as high_c,
           AVG(pct_chg) as avg_pct, SUM(amount) as total_amt
    FROM daily_quotes
    WHERE ts_code = '301308.SZ'
      AND trade_date BETWEEN '2025-09-01' AND '2026-05-26'
    GROUP BY DATE_TRUNC('month', trade_date)
    ORDER BY m
""")
rows = cur.fetchall()
for r in rows:
    print(f"  {r['m']} 低={r['low_c']:.2f} 高={r['high_c']:.2f} 均涨={r['avg_pct']:.2f}% 总额={r['total_amt']/100000000:.1f}亿")

print("\nDone")
sys.stdout.flush()
out.close()

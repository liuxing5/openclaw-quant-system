"""诊断：检查002361、002384、301308在目标时间段的数据"""
import sys, os
sys.path.insert(0, '.')

os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '5432'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

from core.db.connection import get_db
import pandas as pd

out = open(r'D:\pythonProject\openclaw-quant-system\diag_key_stocks_out.txt', 'w', encoding='utf-8')

def w(msg):
    out.write(str(msg) + '\n')
    print(msg)

conn = get_db()

# 检查002361在3月25-26日的数据
w("=== 002361.SZ 2026-03-20 ~ 2026-04-01 ===")
df1 = pd.read_sql("""
    SELECT ts_code, trade_date, open, high, low, close, amount, pct_chg
    FROM daily_quotes
    WHERE ts_code='002361.SZ' AND trade_date >= '2026-03-20' AND trade_date <= '2026-04-01'
    ORDER BY trade_date
""", conn)
w(df1.to_string(index=False))

# 检查002384在4月2-4日的数据
w("\n=== 002384.SZ 2026-03-25 ~ 2026-04-10 ===")
df2 = pd.read_sql("""
    SELECT ts_code, trade_date, open, high, low, close, amount, pct_chg
    FROM daily_quotes
    WHERE ts_code='002384.SZ' AND trade_date >= '2026-03-25' AND trade_date <= '2026-04-10'
    ORDER BY trade_date
""", conn)
w(df2.to_string(index=False))

# 检查301308在2025年9月和2026年1月的数据
w("\n=== 301308.SZ 2025-09-01 ~ 2025-09-15 ===")
df3 = pd.read_sql("""
    SELECT ts_code, trade_date, open, high, low, close, amount, pct_chg
    FROM daily_quotes
    WHERE ts_code='301308.SZ' AND trade_date >= '2025-09-01' AND trade_date <= '2025-09-15'
    ORDER BY trade_date
""", conn)
w(df3.to_string(index=False))

w("\n=== 301308.SZ 2026-01-01 ~ 2026-01-15 ===")
df4 = pd.read_sql("""
    SELECT ts_code, trade_date, open, high, low, close, amount, pct_chg
    FROM daily_quotes
    WHERE ts_code='301308.SZ' AND trade_date >= '2026-01-01' AND trade_date <= '2026-01-15'
    ORDER BY trade_date
""", conn)
w(df4.to_string(index=False))

# 检查002565在1月13日的数据
w("\n=== 002565.SZ 2026-01-08 ~ 2026-01-20 ===")
df5 = pd.read_sql("""
    SELECT ts_code, trade_date, open, high, low, close, amount, pct_chg
    FROM daily_quotes
    WHERE ts_code='002565.SZ' AND trade_date >= '2026-01-08' AND trade_date <= '2026-01-20'
    ORDER BY trade_date
""", conn)
w(df5.to_string(index=False))

# 检查002565在回测期间是否被选到过
w("\n=== 002565.SZ 全回测期间 ===")
df6 = pd.read_sql("""
    SELECT ts_code, trade_date, open, high, low, close, amount, pct_chg
    FROM daily_quotes
    WHERE ts_code='002565.SZ' AND trade_date >= '2025-05-01' AND trade_date <= '2026-05-26'
    ORDER BY trade_date
""", conn)
w(f"Total rows: {len(df6)}")
w(f"Date range: {df6['trade_date'].min()} ~ {df6['trade_date'].max()}")
w(df6.to_string(index=False))

conn.close()
w("\nDONE")
out.close()

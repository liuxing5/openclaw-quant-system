"""检查trade_date列类型和索引使用情况"""
import sys, os
sys.path.insert(0, 'd:/pythonProject/openclaw-quant-system')

import psycopg2

conn = psycopg2.connect(
    host='aws-1-ap-northeast-1.pooler.supabase.com',
    port=5432,
    user='postgres.qoakbxswwjqfsgbcgepr',
    password='wYFBB91zViSrk2vl',
    dbname='postgres',
    sslmode='require',
)
cur = conn.cursor()

# 1. 检查列类型
cur.execute("""
    SELECT column_name, data_type 
    FROM information_schema.columns 
    WHERE table_name = 'daily_quotes' AND column_name = 'trade_date';
""")
row = cur.fetchone()
print(f'trade_date type: {row}')

# 2. 检查索引使用 - EXPLAIN ANALYZE
cur.execute("""
    EXPLAIN ANALYZE 
    SELECT COUNT(*) FROM daily_quotes
    WHERE trade_date >= '2025-08-01' AND trade_date <= '2026-05-15';
""")
rows = cur.fetchall()
print('\nEXPLAIN ANALYZE for COUNT:')
for r in rows:
    print(r[0])

# 3. 检查索引使用 - SELECT
cur.execute("""
    EXPLAIN ANALYZE
    SELECT ts_code, trade_date, open, high, low, close
    FROM daily_quotes
    WHERE trade_date >= '2025-08-01' AND trade_date <= '2026-05-15'
    ORDER BY ts_code, trade_date
    LIMIT 100;
""")
rows = cur.fetchall()
print('\nEXPLAIN ANALYZE for SELECT:')
for r in rows:
    print(r[0])

# 4. 检查样本数据
cur.execute("""
    SELECT ts_code, trade_date, typeof(trade_date) as dtype
    FROM daily_quotes LIMIT 3;
""")
rows = cur.fetchall()
print('\nSample data:')
for r in rows:
    print(r)

cur.close()
conn.close()

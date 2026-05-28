"""直接测试数据库查询速度"""
import sys, os, time
sys.path.insert(0, 'd:/pythonProject/openclaw-quant-system')

import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import date

print('Connecting...')
t0 = time.time()
conn = psycopg2.connect(
    host='aws-1-ap-northeast-1.pooler.supabase.com',
    port=6543,
    user='postgres.qoakbxswwjqfsgbcgepr',
    password='wYFBB91zViSrk2vl',
    dbname='postgres',
    sslmode='require',
)
print(f'Connected in {time.time()-t0:.1f}s')

cur = conn.cursor(cursor_factory=RealDictCursor)

# Test 1: trading days
t1 = time.time()
print('Query 1: trading days...')
cur.execute("""
    SELECT DISTINCT trade_date FROM daily_quotes
    WHERE trade_date >= %s AND trade_date <= %s
    ORDER BY trade_date;
""", (date(2025, 6, 1), date(2026, 5, 15)))
rows = cur.fetchall()
print(f'  Got {len(rows)} trading days in {time.time()-t1:.1f}s')

# Test 2: count all data in range
t2 = time.time()
print('Query 2: count all data...')
cur.execute("""
    SELECT COUNT(*) as cnt FROM daily_quotes
    WHERE trade_date >= %s AND trade_date <= %s;
""", (date(2024, 12, 1), date(2026, 5, 15)))
row = cur.fetchone()
print(f'  Total rows: {row["cnt"]} in {time.time()-t2:.1f}s')

# Test 3: fetch all data
t3 = time.time()
print('Query 3: fetch all data...')
cur.execute("""
    SELECT ts_code, trade_date, open, high, low, close,
           volume, amount, pct_chg, turnover_rate
    FROM daily_quotes
    WHERE trade_date >= %s AND trade_date <= %s
    ORDER BY trade_date, ts_code;
""", (date(2024, 12, 1), date(2026, 5, 15)))
rows = cur.fetchall()
print(f'  Got {len(rows)} rows in {time.time()-t3:.1f}s')

cur.close()
conn.close()
print('Done')

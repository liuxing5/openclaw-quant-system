"""直接测试数据库查询速度 - 直连模式"""
import sys, os, time
sys.path.insert(0, 'd:/pythonProject/openclaw-quant-system')

import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import date

print('Connecting via port 5432 (direct)...')
t0 = time.time()
conn = psycopg2.connect(
    host='aws-1-ap-northeast-1.pooler.supabase.com',
    port=5432,
    user='postgres.qoakbxswwjqfsgbcgepr',
    password='wYFBB91zViSrk2vl',
    dbname='postgres',
    sslmode='require',
)
print(f'Connected in {time.time()-t0:.1f}s')

cur = conn.cursor(cursor_factory=RealDictCursor)

# Test 1: count rows
t1 = time.time()
print('Query 1: count total rows...')
cur.execute("SELECT COUNT(*) as cnt FROM daily_quotes;")
row = cur.fetchone()
print(f'  Total rows: {row["cnt"]} in {time.time()-t1:.1f}s')

# Test 2: check trade_date type and range
t2 = time.time()
print('Query 2: date range...')
cur.execute("SELECT MIN(trade_date) as min_d, MAX(trade_date) as max_d, COUNT(DISTINCT trade_date) as ndays FROM daily_quotes;")
row = cur.fetchone()
print(f'  Range: {row["min_d"]} to {row["max_d"]}, {row["ndays"]} days in {time.time()-t2:.1f}s')

# Test 3: trading days query
t3 = time.time()
print('Query 3: trading days...')
cur.execute("""
    SELECT DISTINCT trade_date FROM daily_quotes
    WHERE trade_date >= %s AND trade_date <= %s
    ORDER BY trade_date;
""", (date(2025, 6, 1), date(2026, 5, 15)))
rows = cur.fetchall()
print(f'  Got {len(rows)} trading days in {time.time()-t3:.1f}s')

# Test 4: count data in range
t4 = time.time()
print('Query 4: count in range...')
cur.execute("""
    SELECT COUNT(*) as cnt FROM daily_quotes
    WHERE trade_date >= %s AND trade_date <= %s;
""", (date(2024, 12, 1), date(2026, 5, 15)))
row = cur.fetchone()
print(f'  Rows in range: {row["cnt"]} in {time.time()-t4:.1f}s')

# Test 5: fetch sample
t5 = time.time()
print('Query 5: fetch 100 rows...')
cur.execute("""
    SELECT ts_code, trade_date, open, high, low, close, amount, pct_chg
    FROM daily_quotes
    WHERE trade_date >= %s AND trade_date <= %s
    ORDER BY trade_date, ts_code
    LIMIT 100;
""", (date(2025, 6, 1), date(2025, 6, 10)))
rows = cur.fetchall()
print(f'  Got {len(rows)} rows in {time.time()-t5:.1f}s')
if rows:
    print(f'  Sample: ts_code={rows[0]["ts_code"]}, trade_date={rows[0]["trade_date"]}')

cur.close()
conn.close()
print('Done')

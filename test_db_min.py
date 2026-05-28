"""最小化测试"""
import sys, os, time
sys.path.insert(0, 'd:/pythonProject/openclaw-quant-system')

import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import date

print('Connecting...')
conn = psycopg2.connect(
    host='aws-1-ap-northeast-1.pooler.supabase.com',
    port=5432,
    user='postgres.qoakbxswwjqfsgbcgepr',
    password='wYFBB91zViSrk2vl',
    dbname='postgres',
    sslmode='require',
)
print('Connected')

cur = conn.cursor(cursor_factory=RealDictCursor)

# Quick test: just get 5 rows
t0 = time.time()
print('Getting 5 rows...')
cur.execute("SELECT ts_code, trade_date, close FROM daily_quotes LIMIT 5;")
rows = cur.fetchall()
print(f'Got {len(rows)} rows in {time.time()-t0:.1f}s')
for r in rows:
    print(f'  {r["ts_code"]} | {r["trade_date"]} | {r["close"]}')

# Check indexes
t1 = time.time()
print('Checking indexes...')
cur.execute("""
    SELECT indexname, indexdef FROM pg_indexes 
    WHERE tablename = 'daily_quotes';
""")
idx_rows = cur.fetchall()
print(f'Found {len(idx_rows)} indexes in {time.time()-t1:.1f}s')
for r in idx_rows:
    print(f'  {r["indexname"]}: {r["indexdef"][:100]}')

# Check table size
t2 = time.time()
print('Checking table size...')
cur.execute("""
    SELECT pg_size_pretty(pg_total_relation_size('daily_quotes')) as size;
""")
row = cur.fetchone()
print(f'Table size: {row["size"]} in {time.time()-t2:.1f}s')

cur.close()
conn.close()
print('Done')

"""优化数据库：添加索引加速查询"""
import sys, os, time
sys.path.insert(0, 'd:/pythonProject/openclaw-quant-system')

import psycopg2
from psycopg2.extras import RealDictCursor

print('Connecting...')
conn = psycopg2.connect(
    host='aws-1-ap-northeast-1.pooler.supabase.com',
    port=5432,
    user='postgres.qoakbxswwjqfsgbcgepr',
    password='wYFBB91zViSrk2vl',
    dbname='postgres',
    sslmode='require',
)
conn.autocommit = True  # 需要autocommit来创建索引
print('Connected')

cur = conn.cursor()

# Check existing indexes
cur.execute("""
    SELECT indexname FROM pg_indexes WHERE tablename = 'daily_quotes';
""")
existing = [r[0] for r in cur.fetchall()]
print(f'Existing indexes: {existing}')

# Create index on trade_date if not exists
if 'idx_daily_quotes_trade_date' not in existing:
    t0 = time.time()
    print('Creating index on trade_date...')
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_daily_quotes_trade_date 
        ON daily_quotes (trade_date);
    """)
    print(f'Index created in {time.time()-t0:.1f}s')
else:
    print('Index on trade_date already exists')

# Create index on ts_code for faster stock lookups
if 'idx_daily_quotes_ts_code' not in existing:
    t1 = time.time()
    print('Creating index on ts_code...')
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_daily_quotes_ts_code 
        ON daily_quotes (ts_code);
    """)
    print(f'Index created in {time.time()-t1:.1f}s')
else:
    print('Index on ts_code already exists')

# Test query speed after indexing
t2 = time.time()
print('Testing query speed...')
cur.execute("""
    SELECT COUNT(*) FROM daily_quotes
    WHERE trade_date >= %s AND trade_date <= %s;
""", ('2025-06-01', '2026-05-15'))
row = cur.fetchone()
print(f'Count in range: {row[0]} in {time.time()-t2:.1f}s')

cur.close()
conn.close()
print('Done')

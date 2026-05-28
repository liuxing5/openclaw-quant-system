"""测试最小范围数据加载"""
import sys, os, time
sys.path.insert(0, 'd:/pythonProject/openclaw-quant-system')

os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '5432'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import date, timedelta

print('Connecting...')
conn = psycopg2.connect(
    host=os.getenv('POSTGRES_HOST'),
    port=int(os.getenv('POSTGRES_PORT')),
    user=os.getenv('POSTGRES_USER'),
    password=os.getenv('POSTGRES_PASSWORD'),
    dbname=os.getenv('POSTGRES_DB'),
    sslmode=os.getenv('POSTGRES_SSLMODE', 'require'),
)
print('Connected')

cur = conn.cursor(cursor_factory=RealDictCursor)

# 先测试小范围: 仅1个月
start = date(2026, 3, 1)
end = date(2026, 5, 15)
preload_start = start - timedelta(days=150)

print(f'Querying {preload_start} ~ {end}...')
t0 = time.time()
cur.execute("""
    SELECT ts_code, trade_date, open, high, low, close,
           volume, amount, pct_chg, turnover_rate
    FROM daily_quotes
    WHERE trade_date >= %s AND trade_date <= %s
    ORDER BY ts_code, trade_date;
""", (preload_start, end))

print('Fetching...')
rows = cur.fetchall()
t1 = time.time()
print(f'Got {len(rows)} rows in {t1-t0:.1f}s')

cur.close()
conn.close()
print('Done')

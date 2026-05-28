"""最小化回测测试 - 仅1个月数据"""
import sys, os, time
sys.path.insert(0, 'd:/pythonProject/openclaw-quant-system')

os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '5432'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', force=True)

import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import date, timedelta
import pandas as pd

# 测试数据加载速度
print('测试数据加载...')
t0 = time.time()
conn = psycopg2.connect(
    host=os.getenv('POSTGRES_HOST'),
    port=int(os.getenv('POSTGRES_PORT')),
    user=os.getenv('POSTGRES_USER'),
    password=os.getenv('POSTGRES_PASSWORD'),
    dbname=os.getenv('POSTGRES_DB'),
    sslmode=os.getenv('POSTGRES_SSLMODE', 'require'),
)
cur = conn.cursor(cursor_factory=RealDictCursor)

preload_start = date(2025, 8, 1)
end = date(2026, 5, 15)

print(f'查询 {preload_start} ~ {end}...')
cur.execute("""
    SELECT ts_code, trade_date, open, high, low, close,
           volume, amount, pct_chg, turnover_rate
    FROM daily_quotes
    WHERE trade_date >= %s AND trade_date <= %s
    ORDER BY ts_code, trade_date;
""", (preload_start, end))

rows = cur.fetchall()
t1 = time.time()
print(f'获取 {len(rows)} 条记录, 耗时 {t1-t0:.1f}s')

# 测试DataFrame处理
print('处理DataFrame...')
df = pd.DataFrame(rows)
for c in ['open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg', 'turnover_rate']:
    df[c] = pd.to_numeric(df[c], errors='coerce')
t2 = time.time()
print(f'DataFrame处理完成, 耗时 {t2-t1:.1f}s')

# 测试分组
print('按股票分组...')
stock_history = {}
for ts_code, grp in df.groupby('ts_code'):
    stock_history[ts_code] = grp.sort_values('trade_date').reset_index(drop=True)
t3 = time.time()
print(f'分组完成: {len(stock_history)} 只股票, 耗时 {t3-t2:.1f}s')

# 测试价格查找表构建
print('构建价格查找表...')
price_lookup = {}
date_to_codes = {}
for ts_code, sdf in stock_history.items():
    price_lookup[ts_code] = {}
    for _, row in sdf.iterrows():
        td = row['trade_date']
        if not isinstance(td, date):
            td = date.fromisoformat(str(td))
        price_lookup[ts_code][td] = {
            'open': float(row['open']),
            'close': float(row['close']),
            'amount': float(row['amount']),
        }
        if td not in date_to_codes:
            date_to_codes[td] = []
        date_to_codes[td].append(ts_code)
t4 = time.time()
print(f'查找表构建完成: {len(date_to_codes)} 天, 耗时 {t4-t3:.1f}s')

cur.close()
conn.close()
print(f'总耗时: {t4-t0:.1f}s')

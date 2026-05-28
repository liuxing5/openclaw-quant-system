"""测试Layer1 SQL查询"""
import sys, os
sys.path.insert(0, 'd:/pythonProject/openclaw-quant-system')
os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '5432'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

import time
from core.db.connection import get_db_fresh
from datetime import date

conn = get_db_fresh()
cur = conn.cursor()

trade_date = date(2026, 4, 10)

t0 = time.time()
cur.execute("""
    SELECT ts_code FROM daily_quotes
    WHERE trade_date = %s AND amount > 50000000 AND pct_chg > -5.0
    ORDER BY pct_chg DESC
    LIMIT 50
""", (trade_date,))
pct_top = [r[0] for r in cur.fetchall()]
print(f"涨幅前50: {time.time()-t0:.2f}s, {len(pct_top)}只")
print(f"  前5: {pct_top[:5]}")

t0 = time.time()
cur.execute("""
    SELECT ts_code FROM daily_quotes
    WHERE trade_date = %s AND amount > 50000000 AND pct_chg > -5.0
    ORDER BY amount DESC
    LIMIT 50
""", (trade_date,))
amount_top = [r[0] for r in cur.fetchall()]
print(f"成交额前50: {time.time()-t0:.2f}s, {len(amount_top)}只")

active_codes = list(set(pct_top) | set(amount_top))
print(f"合计: {len(active_codes)}只")

# 检查000026是否在涨幅前50中
if '000026.SZ' in pct_top:
    print("000026.SZ 在涨幅前50中!")
else:
    print("000026.SZ 不在涨幅前50中")

# 获取历史数据
t0 = time.time()
batch = active_codes[:100]
placeholders = ','.join(['%s'] * len(batch))
start_date = trade_date - __import__('datetime').timedelta(days=120)
cur.execute(f"""
    SELECT ts_code, trade_date, open, high, low, close, volume, amount, pct_chg
    FROM daily_quotes
    WHERE ts_code IN ({placeholders})
      AND trade_date >= %s AND trade_date <= %s
    ORDER BY ts_code, trade_date;
""", batch + [start_date, trade_date])
rows = cur.fetchall()
print(f"历史数据查询: {time.time()-t0:.2f}s, {len(rows)}行")

cur.close()
conn.close()

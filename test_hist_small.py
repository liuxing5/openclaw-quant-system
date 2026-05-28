"""测试历史数据查询 - 小批量"""
import sys, os, time
sys.path.insert(0, 'd:/pythonProject/openclaw-quant-system')
os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '5432'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

from core.db.connection import get_db_fresh
from datetime import date, timedelta

conn = get_db_fresh()
cur = conn.cursor()

trade_date = date(2026, 4, 13)
start_date = trade_date - timedelta(days=120)

# 只查5只股票
codes = ['000026.SZ', '000155.SZ', '000062.SZ', '300308.SZ', '300502.SZ']
placeholders = ','.join(['%s'] * len(codes))

t0 = time.time()
cur.execute(f"""
    SELECT ts_code, trade_date, open, high, low, close, volume, amount, pct_chg
    FROM daily_quotes
    WHERE ts_code IN ({placeholders})
      AND trade_date >= %s AND trade_date <= %s
    ORDER BY ts_code, trade_date;
""", codes + [start_date, trade_date])
rows = cur.fetchall()
elapsed = time.time() - t0
print(f"5只股票历史数据: {elapsed:.2f}s, {len(rows)}行")

# 检查000026的数据
for r in rows:
    if r[0] == '000026.SZ':
        print(f"  000026.SZ: {r[1]} close={r[5]}")

cur.close()
conn.close()

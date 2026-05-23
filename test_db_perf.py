"""测试数据库查询性能"""
import os, time, sys
sys.stdout.reconfigure(line_buffering=True)
os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '6543'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

from core.db.connection import get_db
from psycopg2.extras import RealDictCursor

conn = get_db()

# Test 1: Get active stocks for one day
t0 = time.time()
cur = conn.cursor(cursor_factory=RealDictCursor)
cur.execute("""
    SELECT ts_code FROM daily_quotes
    WHERE trade_date = '2026-05-15'
      AND amount > 200000000 AND pct_chg > 0.5 AND pct_chg < 8
    ORDER BY amount DESC LIMIT 30
""")
active = cur.fetchall()
cur.close()
print(f"Active stocks query: {time.time()-t0:.2f}s, count: {len(active)}")

# Test 2: Single query for 30 stocks kline
t0 = time.time()
codes = [r['ts_code'] for r in active]
cur = conn.cursor(cursor_factory=RealDictCursor)
cur.execute("""
    SELECT ts_code, trade_date, open, high, low, close, volume, amount, pct_chg
    FROM daily_quotes
    WHERE trade_date >= '2026-03-01' AND trade_date <= '2026-05-15'
      AND ts_code = ANY(%s)
    ORDER BY ts_code, trade_date
""", (codes,))
all_rows = cur.fetchall()
cur.close()
print(f"Kline 30 stocks query: {time.time()-t0:.2f}s, rows: {len(all_rows)}")

# Test 3: Market breadth
t0 = time.time()
cur = conn.cursor(cursor_factory=RealDictCursor)
cur.execute("""
    SELECT COUNT(*) FILTER (WHERE pct_chg > 0) as adv, COUNT(*) as total
    FROM daily_quotes WHERE trade_date = '2026-05-15'
""")
row = cur.fetchone()
cur.close()
adv = row['adv']
total = row['total']
print(f"Market breadth query: {time.time()-t0:.2f}s, adv/total: {adv}/{total}")

# Estimate
per_day = 2.0 + 3.0 + 0.2 + 1.0  # active + kline + breadth + overhead
print(f"Estimated per day: {per_day:.1f}s")
print(f"Estimated total backtest time (230 days): {230 * per_day:.0f}s = {230 * per_day / 60:.1f}min")

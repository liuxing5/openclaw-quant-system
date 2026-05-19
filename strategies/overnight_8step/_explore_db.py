import psycopg2
from psycopg2.extras import RealDictCursor
import sys

DB_URL = "postgresql://postgres.qoakbxswwjqfsgbcgepr:wYFBB91zViSrk2vl@aws-1-ap-northeast-1.pooler.supabase.com:6543/postgres"

conn = psycopg2.connect(DB_URL, connect_timeout=30)
cur = conn.cursor(cursor_factory=RealDictCursor)

print("Connected!", flush=True)

cur.execute("SELECT COUNT(*) as cnt FROM daily_quotes WHERE trade_date >= '2025-01-01'")
r = cur.fetchone()
print(f"2025+ records: {r['cnt']}", flush=True)

cur.execute("""
    SELECT COUNT(*) as total,
           COUNT(volume_ratio) as has_vr,
           COUNT(turnover_rate) as has_tr,
           COUNT(circulating_market_cap) as has_cmc
    FROM daily_quotes
    WHERE trade_date >= '2025-06-01'
""")
r = cur.fetchone()
print(f"2025-06+ field coverage: total={r['total']} vr={r['has_vr']} tr={r['has_tr']} cmc={r['has_cmc']}", flush=True)

cur.execute("""
    SELECT COUNT(DISTINCT trade_date) as days,
           MIN(trade_date) as min_d,
           MAX(trade_date) as max_d
    FROM daily_quotes
    WHERE trade_date >= '2025-01-01'
""")
r = cur.fetchone()
print(f"2025+ trading days: {r['days']} from {r['min_d']} to {r['max_d']}", flush=True)

cur.execute("""
    SELECT trade_date, ts_code, pct_chg, turnover_rate, amount,
           circulating_market_cap, volume_ratio, close
    FROM daily_quotes
    WHERE trade_date = '2026-05-12'
      AND pct_chg BETWEEN 3 AND 10
      AND turnover_rate BETWEEN 5 AND 10
      AND amount >= 50000000
    ORDER BY pct_chg DESC
    LIMIT 10
""")
rows = cur.fetchall()
print(f"\n2026-05-12 candidates sample:", flush=True)
for r in rows:
    print(f"  {r['ts_code']} pct={r['pct_chg']} turn={r['turnover_rate']} "
          f"amt={float(r['amount'])/1e8:.1f}亿 vr={r['volume_ratio']} cmc={r['circulating_market_cap']} close={r['close']}", flush=True)

cur.close()
conn.close()
print("Done!", flush=True)

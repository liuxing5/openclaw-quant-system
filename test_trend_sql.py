import sys; sys.path.insert(0,'.')
import os
os.environ['POSTGRES_HOST']='aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT']='5432'
os.environ['POSTGRES_USER']='postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD']='wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB']='postgres'
os.environ['POSTGRES_SSLMODE']='require'
from core.db.connection import get_db
from psycopg2.extras import RealDictCursor
import datetime

conn = get_db(use_dict_cursor=True)
cur = conn.cursor(cursor_factory=RealDictCursor)

trade_date = datetime.date(2025, 5, 7)
print(f"Testing trend SQL for {trade_date}...")

try:
    cur.execute("""
        SELECT DISTINCT a.ts_code
        FROM daily_quotes a
        WHERE a.trade_date = %s
          AND a.pct_chg BETWEEN 1 AND 7
          AND a.amount > 50000000
          AND EXISTS (
            SELECT 1 FROM daily_quotes b
            WHERE b.ts_code = a.ts_code
              AND b.trade_date < %s
              AND b.trade_date >= %s - INTERVAL '5 days'
              AND b.pct_chg > 0
          )
        LIMIT 30
    """, (trade_date, trade_date, trade_date))
    rows = cur.fetchall()
    print(f"Result: {len(rows)} stocks")
    for r in rows[:5]:
        print(f"  {r['ts_code']}")
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()

cur.close()
conn.close()

with open('test_trend_sql_result.txt', 'w') as f:
    f.write(f"Done - {len(rows)} stocks\n")
    for r in rows:
        f.write(f"  {r['ts_code']}\n")
print("Done")

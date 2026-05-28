import sys; sys.path.insert(0,'.')
import os
os.environ['POSTGRES_HOST']='aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT']='5432'
os.environ['POSTGRES_USER']='postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD']='wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB']='postgres'
os.environ['POSTGRES_SSLMODE']='require'
from core.db.connection import get_db
conn=get_db()
cur=conn.cursor()

# Check 002565 in stock_basic_info
cur.execute("SELECT * FROM stock_basic_info WHERE ts_code='002565.SZ'")
r = cur.fetchone()
print(f"002565 in stock_basic_info: {r}")

# Check 301308
cur.execute("SELECT * FROM stock_basic_info WHERE ts_code='301308.SZ'")
r = cur.fetchone()
print(f"301308 in stock_basic_info: {r}")

# Check how many stocks in stock_basic_info have is_st=true or is_active=false
cur.execute("SELECT COUNT(*) FROM stock_basic_info WHERE is_st = true OR is_active = false")
r = cur.fetchone()
print(f"ST/inactive count: {r[0]}")

# Check the excluded codes logic - what happens if ts_code is NOT in stock_basic_info?
# The current logic: excluded_codes = set(r['ts_code'] for r in cur.fetchall())
# If 002565 is not in stock_basic_info, it won't be in excluded_codes, so it should pass

# Check Layer0 market risk on Jan 8
cur.execute("""
    SELECT COUNT(*) FILTER (WHERE pct_chg > 0) as advancers,
           COUNT(*) as total
    FROM daily_quotes WHERE trade_date = '2026-01-08'
""")
r = cur.fetchone()
print(f"Jan 8 market: advancers={r[0]}, total={r[1]}, ratio={r[0]/r[1]:.2f}")

# Check Layer0 on Sep 12 2025
cur.execute("""
    SELECT COUNT(*) FILTER (WHERE pct_chg > 0) as advancers,
           COUNT(*) as total
    FROM daily_quotes WHERE trade_date = '2025-09-12'
""")
r = cur.fetchone()
print(f"Sep 12 market: advancers={r[0]}, total={r[1]}, ratio={r[0]/r[1]:.2f}")

conn.close()

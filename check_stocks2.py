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

# Check if these stocks appear in Layer1 on key dates
# 002565 on Jan 8 (涨停日)
cur.execute("SELECT trade_date, pct_chg, amount FROM daily_quotes WHERE ts_code='002565.SZ' AND trade_date='2026-01-08'")
r = cur.fetchone()
print(f"002565 Jan 8: pct_chg={r[1]}, amount={r[2]}")

# 301308 on Sep 5
cur.execute("SELECT trade_date, pct_chg, amount FROM daily_quotes WHERE ts_code='301308.SZ' AND trade_date='2025-09-05'")
r = cur.fetchone()
print(f"301308 Sep 5: pct_chg={r[1]}, amount={r[2]}")

# 301308 on Sep 12 (涨停日)
cur.execute("SELECT trade_date, pct_chg, amount FROM daily_quotes WHERE ts_code='301308.SZ' AND trade_date='2025-09-12'")
r = cur.fetchone()
print(f"301308 Sep 12: pct_chg={r[1]}, amount={r[2]}")

# 002361 on March 25
cur.execute("SELECT trade_date, pct_chg, amount FROM daily_quotes WHERE ts_code='002361.SZ' AND trade_date='2026-03-25'")
r = cur.fetchone()
print(f"002361 Mar 25: pct_chg={r[1]}, amount={r[2]}")

# 002384 on April 1
cur.execute("SELECT trade_date, pct_chg, amount FROM daily_quotes WHERE ts_code='002384.SZ' AND trade_date='2026-04-01'")
r = cur.fetchone()
print(f"002384 Apr 1: pct_chg={r[1]}, amount={r[2]}")

# Check: are these in top 50 by pct_chg on those dates?
for date, code in [('2026-01-08', '002565.SZ'), ('2025-09-12', '301308.SZ'), ('2026-03-25', '002361.SZ'), ('2026-04-01', '002384.SZ')]:
    cur.execute("""
        SELECT COUNT(*) FROM daily_quotes
        WHERE trade_date = %s AND amount > 50000000 AND pct_chg > (SELECT pct_chg FROM daily_quotes WHERE ts_code=%s AND trade_date=%s)
    """, (date, code, date))
    rank = cur.fetchone()[0]
    print(f"{code} on {date}: pct_chg rank #{rank+1}")

# Check: is 301308 amount > 50M on Sep 5?
cur.execute("SELECT amount FROM daily_quotes WHERE ts_code='301308.SZ' AND trade_date='2025-09-05'")
r = cur.fetchone()
print(f"301308 Sep 5 amount: {r[0]}, >50M: {float(r[0]) > 50000000}")

# Check: is 002361 amount > 50M on March 25?
cur.execute("SELECT amount FROM daily_quotes WHERE ts_code='002361.SZ' AND trade_date='2026-03-25'")
r = cur.fetchone()
print(f"002361 Mar 25 amount: {r[0]}, >50M: {float(r[0]) > 50000000}")

conn.close()

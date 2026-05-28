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

# 002565 around Jan 13
cur.execute("SELECT trade_date, open, close, pct_chg, amount FROM daily_quotes WHERE ts_code='002565.SZ' AND trade_date>='2026-01-05' AND trade_date<='2026-01-20' ORDER BY trade_date")
for r in cur.fetchall(): print(f'002565: {r}')

# 301308 around Sep 2025 - Jan 2026
cur.execute("SELECT trade_date, open, close, pct_chg, amount FROM daily_quotes WHERE ts_code='301308.SZ' AND trade_date>='2025-09-01' AND trade_date<='2026-01-31' ORDER BY trade_date LIMIT 20")
for r in cur.fetchall(): print(f'301308: {r}')

# 002361 around March 25-26
cur.execute("SELECT trade_date, open, close, pct_chg, amount FROM daily_quotes WHERE ts_code='002361.SZ' AND trade_date>='2026-03-20' AND trade_date<='2026-04-25' ORDER BY trade_date")
for r in cur.fetchall(): print(f'002361: {r}')

# 002384 around April 2-4
cur.execute("SELECT trade_date, open, close, pct_chg, amount FROM daily_quotes WHERE ts_code='002384.SZ' AND trade_date>='2026-03-25' AND trade_date<='2026-04-25' ORDER BY trade_date")
for r in cur.fetchall(): print(f'002384: {r}')

conn.close()

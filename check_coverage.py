"""Check existing daily_candidates data coverage."""
import os, sys
sys.path.insert(0, '.')
from core.utils.env import load_project_env
load_project_env()

from core.db.connection import get_db, close_db_session

conn = get_db()
cur = conn.cursor()

cur.execute("""
    SELECT source, 
           MIN(snapshot_date) as min_date, 
           MAX(snapshot_date) as max_date, 
           COUNT(*) as total,
           COUNT(DISTINCT snapshot_date) as days
    FROM daily_candidates 
    GROUP BY source 
    ORDER BY source
""")
rows = cur.fetchall()
print("=== daily_candidates 概览 ===")
for row in rows:
    print(f"  {row[0]}: {row[1]} ~ {row[2]}, {row[3]} 条, {row[4]} 天")

print("\n=== overnight_8step 日期明细 ===")
cur.execute("""
    SELECT snapshot_date, COUNT(*) as cnt
    FROM daily_candidates 
    WHERE source = 'overnight_8step'
    GROUP BY snapshot_date
    ORDER BY snapshot_date
""")
rows = cur.fetchall()
for row in rows:
    print(f"  {row[0]}: {row[1]} 条")

print("\n=== llm_multisource 日期明细 ===")
cur.execute("""
    SELECT snapshot_date, COUNT(*) as cnt
    FROM daily_candidates 
    WHERE source = 'llm_multisource'
    GROUP BY snapshot_date
    ORDER BY snapshot_date
""")
rows = cur.fetchall()
for row in rows:
    print(f"  {row[0]}: {row[1]} 条")

cur.close()
close_db_session()

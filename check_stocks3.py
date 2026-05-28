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

# Check 002565 on Jan 8 - is it ST?
cur.execute("SELECT ts_code, is_st, is_active FROM stock_basic_info WHERE ts_code='002565.SZ'")
r = cur.fetchone()
print(f"002565 basic: {r}")

# Check 301308
cur.execute("SELECT ts_code, is_st, is_active FROM stock_basic_info WHERE ts_code='301308.SZ'")
r = cur.fetchone()
print(f"301308 basic: {r}")

# Check if 002565 was in the results file on Jan 8
import os
result_file = 'results/meta_strategy_2026-01-08.json'
if os.path.exists(result_file):
    import json
    with open(result_file, encoding='utf-8') as f:
        data = json.load(f)
    for item in data:
        if '002565' in str(item):
            print(f"002565 in Jan 8 results: {item}")
    print(f"Jan 8 results count: {len(data)}")
else:
    print(f"No results file for Jan 8")

# Check 301308 on Sep 12
result_file2 = 'results/meta_strategy_2025-09-12.json'
if os.path.exists(result_file2):
    with open(result_file2, encoding='utf-8') as f:
        data2 = json.load(f)
    for item in data2:
        if '301308' in str(item):
            print(f"301308 in Sep 12 results: {item}")
    print(f"Sep 12 results count: {len(data2)}")
else:
    print(f"No results file for Sep 12")

conn.close()

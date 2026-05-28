"""Quick DB test - write output to file."""
import os, sys, time
sys.path.insert(0, '.')
from core.utils.env import load_project_env
load_project_env()

import psycopg2

DB_URL = "postgresql://postgres.qoakbxswwjqfsgbcgepr:wYFBB91zViSrk2vl@aws-1-ap-northeast-1.pooler.supabase.com:6543/postgres"

out = open('test_db_result.txt', 'w', encoding='utf-8')

def log(msg):
    print(msg, flush=True)
    out.write(msg + '\n')
    out.flush()

log("Connecting...")
t0 = time.time()
try:
    conn = psycopg2.connect(DB_URL, connect_timeout=30)
    conn.autocommit = True
    log(f"Connected in {time.time()-t0:.1f}s")

    log("Executing SELECT 1...")
    t1 = time.time()
    cur = conn.cursor()
    cur.execute("SELECT 1")
    r = cur.fetchone()
    log(f"SELECT 1 in {time.time()-t1:.1f}s: {r}")

    log("Executing count query...")
    t2 = time.time()
    cur.execute("SELECT count(*) FROM daily_candidates WHERE source = 'overnight_8step'")
    r2 = cur.fetchone()
    log(f"Count in {time.time()-t2:.1f}s: {r2}")

    cur.close()
    conn.close()
    log("Done")
except Exception as e:
    log(f"Error: {e}")
    import traceback
    traceback.print_exc(file=out)
    out.flush()

out.close()

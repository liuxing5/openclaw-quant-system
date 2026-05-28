"""Quick DB connectivity test - minimal."""
import os, sys, time
sys.path.insert(0, '.')
from core.utils.env import load_project_env
load_project_env()

import psycopg2

DB_URL = "postgresql://postgres.qoakbxswwjqfsgbcgepr:wYFBB91zViSrk2vl@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres"

print("Connecting via DB_URL...", flush=True)
t0 = time.time()
try:
    conn = psycopg2.connect(DB_URL, connect_timeout=30)
    conn.autocommit = True
    print(f"Connected in {time.time()-t0:.1f}s", flush=True)

    cur = conn.cursor()
    t1 = time.time()
    cur.execute("SELECT 1")
    r = cur.fetchone()
    print(f"SELECT 1 in {time.time()-t1:.1f}s: {r}", flush=True)

    t2 = time.time()
    cur.execute("SELECT count(*) FROM daily_candidates WHERE source = 'overnight_8step'")
    r2 = cur.fetchone()
    print(f"Count in {time.time()-t2:.1f}s: {r2}", flush=True)

    cur.close()
    conn.close()
    print("Done", flush=True)
except Exception as e:
    print(f"Error: {e}", flush=True)
    import traceback
    traceback.print_exc()

"""Quick DB test - with explicit commit."""
import os, sys, time
sys.path.insert(0, '.')
from core.utils.env import load_project_env
load_project_env()

import psycopg2

DB_URL = "postgresql://postgres.qoakbxswwjqfsgbcgepr:wYFBB91zViSrk2vl@aws-1-ap-northeast-1.pooler.supabase.com:6543/postgres"

print("Connecting...", flush=True)
t0 = time.time()
try:
    conn = psycopg2.connect(DB_URL, connect_timeout=30)
    print(f"Connected in {time.time()-t0:.1f}s", flush=True)
    print(f"autocommit={conn.autocommit}", flush=True)

    conn.autocommit = True
    print(f"Set autocommit=True", flush=True)

    print("Executing SELECT 1...", flush=True)
    t1 = time.time()
    cur = conn.cursor()
    cur.execute("SELECT 1")
    print(f"Execute done in {time.time()-t1:.1f}s", flush=True)
    r = cur.fetchone()
    print(f"fetchone: {r}", flush=True)
    cur.close()

    conn.close()
    print("Done", flush=True)
except Exception as e:
    print(f"Error: {e}", flush=True)
    import traceback
    traceback.print_exc()

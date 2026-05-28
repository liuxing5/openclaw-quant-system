"""Test project DB connection using core.db.connection."""
import os, sys, time
sys.path.insert(0, '.')
from core.utils.env import load_project_env
load_project_env()

from core.db.connection import get_db, close_db_session

print("Testing get_db()...", flush=True)
t0 = time.time()
try:
    conn = get_db()
    print(f"get_db() connected in {time.time()-t0:.1f}s", flush=True)

    print("Executing SELECT 1...", flush=True)
    t1 = time.time()
    cur = conn.cursor()
    cur.execute("SELECT 1")
    r = cur.fetchone()
    print(f"SELECT 1 in {time.time()-t1:.1f}s: {r}", flush=True)

    print("Count daily_candidates...", flush=True)
    t2 = time.time()
    cur.execute("SELECT count(*), source FROM daily_candidates GROUP BY source ORDER BY source")
    rows = cur.fetchall()
    print(f"Count in {time.time()-t2:.1f}s:", flush=True)
    for row in rows:
        print(f"  {row}", flush=True)

    cur.close()
    close_db_session()
    print("Done", flush=True)
except Exception as e:
    print(f"Error: {e}", flush=True)
    import traceback
    traceback.print_exc()

"""Quick DB connectivity test."""
import os, sys, time
sys.path.insert(0, '.')
from core.utils.env import load_project_env
load_project_env()

import psycopg2

print("Connecting...", flush=True)
t0 = time.time()
conn = psycopg2.connect(
    host=os.getenv('POSTGRES_HOST'),
    port=int(os.getenv('POSTGRES_PORT') or 5432),
    user=os.getenv('POSTGRES_USER'),
    password=os.getenv('POSTGRES_PASSWORD'),
    dbname=os.getenv('POSTGRES_DB'),
    sslmode=os.getenv('POSTGRES_SSLMODE', 'require'),
    connect_timeout=30,
)
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

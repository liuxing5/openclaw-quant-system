import psycopg2
from psycopg2.extras import RealDictCursor
import time, sys

print("Trying to connect...", flush=True)

try:
    conn = psycopg2.connect(
        host='aws-1-ap-northeast-1.pooler.supabase.com',
        port=6543,
        user='postgres.qoakbxswwjqfsgbcgepr',
        password='wYFBB91zViSrk2vl',
        dbname='postgres',
        sslmode='require',
        connect_timeout=10,
    )
    conn.autocommit = True
    print("Connected!", flush=True)
    
    cur = conn.cursor(cursor_factory=RealDictCursor)
    print("Executing query...", flush=True)
    
    cur.execute("""
        SELECT snapshot_date, source, run_mode, COUNT(*) as cnt
        FROM daily_candidates
        WHERE selected = true
        GROUP BY snapshot_date, source, run_mode
        ORDER BY snapshot_date DESC
        LIMIT 5
    """)
    
    rows = cur.fetchall()
    print(f"Got {len(rows)} rows", flush=True)
    for r in rows:
        print(f"  {r['snapshot_date']} | {r['source']} | {r['run_mode']} | {r['cnt']}", flush=True)
    
    cur.close()
    conn.close()
    print("Done!", flush=True)
except Exception as e:
    print(f"ERROR: {e}", flush=True)
    import traceback
    traceback.print_exc()

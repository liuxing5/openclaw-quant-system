import psycopg2
import time

print("Testing connection...", flush=True)

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
    print("Connected!", flush=True)
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM daily_candidates WHERE selected = true")
    cnt = cur.fetchone()[0]
    print(f"Selected candidates count: {cnt}", flush=True)
    cur.close()
    conn.close()
except Exception as e:
    print(f"Error: {e}", flush=True)

import psycopg2
from psycopg2.extras import RealDictCursor
import socket

print("Step 1: DNS lookup", flush=True)
try:
    ips = socket.getaddrinfo('aws-1-ap-northeast-1.pooler.supabase.com', 6543)
    print(f"  Resolved: {ips[0][4]}", flush=True)
except Exception as e:
    print(f"  DNS failed: {e}", flush=True)
    exit(1)

print("Step 2: TCP connect", flush=True)
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(10)
try:
    sock.connect(('13.114.6.6', 6543))
    print("  TCP connected", flush=True)
except Exception as e:
    print(f"  TCP failed: {e}", flush=True)
    exit(1)
sock.close()

print("Step 3: psycopg2 connect", flush=True)
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
    print("  DB connected!", flush=True)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT count(*) FROM daily_candidates WHERE selected = true")
    cnt = cur.fetchone()[0]
    print(f"  Selected candidates: {cnt}", flush=True)
    cur.close()
    conn.close()
except Exception as e:
    print(f"  DB connect failed: {e}", flush=True)

print("Done", flush=True)

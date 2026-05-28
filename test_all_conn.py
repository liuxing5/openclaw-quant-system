import psycopg2
import time
import sys

# Try different connection methods
configs = [
    ("6543-transaction", "postgresql://postgres.qoakbxswwjqfsgbcgepr:wYFBB91zViSrk2vl@aws-1-ap-northeast-1.pooler.supabase.com:6543/postgres"),
    ("5432-session", "postgresql://postgres.qoakbxswwjqfsgbcgepr:wYFBB91zViSrk2vl@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres"),
]

for name, url in configs:
    print(f"\nTrying {name}...")
    for attempt in range(5):
        try:
            conn = psycopg2.connect(url, connect_timeout=30)
            cur = conn.cursor()
            cur.execute("SELECT 1")
            print(f"SUCCESS on {name} at attempt {attempt+1}!")
            cur.execute("SELECT COUNT(*) FROM daily_quotes WHERE trade_date >= '2025-04-01'")
            print(f"Count: {cur.fetchone()[0]}")
            conn.close()
            sys.exit(0)
        except Exception as e:
            print(f"  Attempt {attempt+1} failed: {str(e)[:100]}")
            time.sleep(15)

print("\nAll connection attempts failed")
sys.exit(1)

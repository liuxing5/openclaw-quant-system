"""
使用更激进的连接策略 - 短超时、快速重试
"""
import psycopg2
import time
import sys

# Try with very short timeout
url = "postgresql://postgres.qoakbxswwjqfsgbcgepr:wYFBB91zViSrk2vl@aws-1-ap-northeast-1.pooler.supabase.com:6543/postgres"

print("Testing connection with short timeout...")
for i in range(20):
    try:
        conn = psycopg2.connect(url, connect_timeout=5)
        cur = conn.cursor()
        cur.execute("SELECT 1")
        print(f"SUCCESS on attempt {i+1}!")
        cur.execute("SELECT COUNT(*) FROM daily_quotes WHERE trade_date >= '2025-04-01'")
        print(f"Count: {cur.fetchone()[0]}")
        conn.close()
        sys.exit(0)
    except Exception as e:
        err = str(e)
        if "max clients" in err:
            print(f"Attempt {i+1}: Pool full, waiting 10s...")
            time.sleep(10)
        else:
            print(f"Attempt {i+1}: {err[:100]}")
            time.sleep(5)

print("Failed after 20 attempts")
sys.exit(1)

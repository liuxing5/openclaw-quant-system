import psycopg2
import sys
import time
from datetime import datetime

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

DB_URL = "postgresql://postgres.qoakbxswwjqfsgbcgepr:wYFBB91zViSrk2vl@aws-1-ap-northeast-1.pooler.supabase.com:6543/postgres"

log("开始测试...")

try:
    log("连接...")
    conn = psycopg2.connect(DB_URL, sslmode='require', connect_timeout=10)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SET statement_timeout = '30s'")
    log("连接成功!")
    
    # Test 1: Simple query
    log("Test 1: SELECT 1...")
    cur.execute("SELECT 1")
    log(f"Result: {cur.fetchone()}")
    
    # Test 2: Count all
    log("Test 2: Count all rows...")
    t0 = time.time()
    cur.execute("SELECT count(*) FROM daily_quotes")
    count = cur.fetchone()[0]
    log(f"Total rows: {count}, time: {time.time()-t0:.1f}s")
    
    # Test 3: Count by month
    log("Test 3: Count May 2025...")
    t0 = time.time()
    cur.execute("SELECT count(*) FROM daily_quotes WHERE trade_date >= '2025-05-01' AND trade_date < '2025-06-01'")
    count = cur.fetchone()[0]
    log(f"May 2025 rows: {count}, time: {time.time()-t0:.1f}s")
    
    # Test 4: Sample data
    log("Test 4: Sample 3 rows...")
    cur.execute("SELECT ts_code, trade_date, close FROM daily_quotes WHERE trade_date >= '2025-05-01' LIMIT 3")
    for row in cur.fetchall():
        log(f"  {row}")
    
    cur.close()
    conn.close()
    log("完成!")
except Exception as e:
    log(f"错误: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

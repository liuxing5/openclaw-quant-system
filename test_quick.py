import psycopg2
import time
from datetime import datetime

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

DB_URL = os.environ.get('DATABASE_URL', '')

log("开始测试...")
conn = None
for attempt in range(5):
    try:
        log(f"尝试 {attempt+1}/5...")
        conn = psycopg2.connect(DB_URL, sslmode='require', connect_timeout=10)
        log("连接成功!")
        break
    except Exception as e:
        log(f"失败: {str(e)[:80]}")
        conn = None
        time.sleep(10)

if conn:
    log("OK")
    conn.close()
else:
    log("FAIL")

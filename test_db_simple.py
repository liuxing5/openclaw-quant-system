import psycopg2
from psycopg2.extras import RealDictCursor
import sys

DB_URL = "postgresql://postgres.qoakbxswwjqfsgbcgepr:wYFBB91zViSrk2vl@aws-1-ap-northeast-1.pooler.supabase.com:6543/postgres"

with open('db_test_result.txt', 'w', encoding='utf-8') as f:
    try:
        f.write("Testing connection...\n")
        f.flush()
        
        conn = psycopg2.connect(DB_URL, connect_timeout=60)
        f.write("Connected!\n")
        f.flush()
        
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT COUNT(*) as cnt FROM daily_quotes WHERE trade_date >= '2026-05-01' AND trade_date <= '2026-05-25'")
        row = cur.fetchone()
        f.write(f"Row count: {row['cnt']}\n")
        f.flush()
        cur.close()
        conn.close()
        f.write("Done!\n")
        f.flush()
    except Exception as e:
        f.write(f"Error: {e}\n")
        import traceback
        f.write(traceback.format_exc())
        f.flush()

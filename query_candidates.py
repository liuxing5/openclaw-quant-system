"""查询 daily_candidates 表数据"""
import psycopg2
import sys

# 直连数据库（绕过 pooler）
DB_URL = "postgresql://postgres:wYFBB91zViSrk2vl@db.qoakbxswwjqfsgbcgepr.supabase.co:5432/postgres"

print("Connecting (direct)...", flush=True)
try:
    conn = psycopg2.connect(DB_URL, connect_timeout=30)
    print("Connected!", flush=True)
    conn.autocommit = True
    cur = conn.cursor()

    # 测试1: SELECT 1
    print("Test 1: SELECT 1...", flush=True)
    cur.execute("SELECT 1")
    result = cur.fetchone()
    print(f"  Result: {result}", flush=True)

    # 测试2: 估算行数
    print("Test 2: Estimated row count...", flush=True)
    cur.execute("SELECT reltuples::bigint FROM pg_class WHERE relname='daily_candidates'")
    count = cur.fetchone()[0]
    print(f"  Estimated rows: {count}", flush=True)

    # 测试3: 最新 5 条
    print("Test 3: Latest 5 rows...", flush=True)
    cur.execute("SELECT source, snapshot_date FROM daily_candidates ORDER BY snapshot_date DESC LIMIT 5")
    rows = cur.fetchall()
    for row in rows:
        print(f"  {row[0]} | {row[1]}", flush=True)

    # 测试4: 各策略数据量
    print("\nTest 4: Count by source...", flush=True)
    cur.execute("""
        SELECT source, COUNT(*), MIN(snapshot_date), MAX(snapshot_date)
        FROM daily_candidates
        GROUP BY source ORDER BY source
    """)
    print("=== 各策略数据量 ===")
    for row in cur.fetchall():
        print(f"  {row[0]}: {row[1]} 条, {row[2]} ~ {row[3]}")

    # 终止所有卡住的连接
    print("\nTerminating stale connections...", flush=True)
    cur.execute("""
        SELECT count(pg_terminate_backend(pid))
        FROM pg_stat_activity
        WHERE datname = 'postgres'
          AND pid != pg_backend_pid()
          AND state = 'active'
          AND query_start < now() - interval '60 seconds'
    """)
    terminated = cur.fetchone()[0]
    print(f"  Terminated {terminated} stale connections", flush=True)

    cur.close()
    conn.close()
    print("\nDone!", flush=True)
except Exception as e:
    print(f"Error: {e}", flush=True)
    sys.exit(1)

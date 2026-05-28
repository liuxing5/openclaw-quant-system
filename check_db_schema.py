#!/usr/bin/env python3
"""检查数据库中的指数数据"""
import psycopg2

conn = psycopg2.connect(
    host='aws-1-ap-northeast-1.pooler.supabase.com',
    port=5432,
    user='postgres.qoakbxswwjqfsgbcgepr',
    password='wYFBB91zViSrk2vl',
    dbname='postgres',
    sslmode='require',
    connect_timeout=30,
)
cur = conn.cursor()

# 检查指数数据
cur.execute("SELECT count(*) FROM daily_quotes WHERE ts_code = '000001.SH'")
print(f"000001.SH records: {cur.fetchone()[0]}")

# 检查有哪些ts_code以000001开头
cur.execute("SELECT DISTINCT ts_code FROM daily_quotes WHERE ts_code LIKE '000001%' LIMIT 10")
print(f"000001* codes: {cur.fetchall()}")

# 检查表结构
cur.execute("""
    SELECT column_name FROM information_schema.columns 
    WHERE table_name = 'daily_quotes' ORDER BY ordinal_position
""")
print(f"Columns: {[r[0] for r in cur.fetchall()]}")

# 检查是否有index_quotes表
cur.execute("""
    SELECT table_name FROM information_schema.tables 
    WHERE table_name LIKE '%index%'
""")
print(f"Index tables: {cur.fetchall()}")

# 检查有哪些表
cur.execute("""
    SELECT table_name FROM information_schema.tables 
    WHERE table_schema = 'public' ORDER BY table_name
""")
print(f"All tables: {[r[0] for r in cur.fetchall()]}")

conn.close()

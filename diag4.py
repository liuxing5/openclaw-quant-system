#!/usr/bin/env python3
"""DB连接测试 - 带超时"""
import os
os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '6543'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

import psycopg2

f = open('diag4.txt', 'w', encoding='utf-8')
try:
    f.write("Connecting with timeout...\n"); f.flush()
    conn = psycopg2.connect(
        host='aws-1-ap-northeast-1.pooler.supabase.com',
        port=6543,
        user='postgres.qoakbxswwjqfsgbcgepr',
        password='wYFBB91zViSrk2vl',
        dbname='postgres',
        sslmode='require',
        connect_timeout=30,
        options='-c statement_timeout=30000',  # 30s query timeout
    )
    f.write("Connected!\n"); f.flush()
    
    cur = conn.cursor()
    f.write("Executing simple query...\n"); f.flush()
    cur.execute("SELECT 1")
    f.write(f"Result: {cur.fetchone()}\n"); f.flush()
    
    f.write("Executing count query...\n"); f.flush()
    cur.execute("SELECT count(*) FROM daily_quotes WHERE trade_date = '2026-04-01'")
    count = cur.fetchone()[0]
    f.write(f"Count: {count}\n"); f.flush()
    
    conn.close()
    f.write("Done!\n"); f.flush()
except Exception as e:
    import traceback
    f.write(f"ERROR: {e}\n"); f.flush()
    f.write(traceback.format_exc()); f.flush()
f.close()

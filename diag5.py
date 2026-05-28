#!/usr/bin/env python3
"""DB连接测试 - 直连模式"""
import psycopg2

f = open('diag5.txt', 'w', encoding='utf-8')
try:
    f.write("Connecting direct (port 5432)...\n"); f.flush()
    conn = psycopg2.connect(
        host='aws-1-ap-northeast-1.pooler.supabase.com',
        port=5432,  # 直连模式，绕过连接池
        user='postgres.qoakbxswwjqfsgbcgepr',
        password='wYFBB91zViSrk2vl',
        dbname='postgres',
        sslmode='require',
        connect_timeout=30,
    )
    f.write("Connected!\n"); f.flush()
    
    cur = conn.cursor()
    cur.execute("SELECT 1")
    f.write(f"Result: {cur.fetchone()}\n"); f.flush()
    
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

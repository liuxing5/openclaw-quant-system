#!/usr/bin/env python3
"""最简单的DB连接测试"""
import os

import psycopg2

f = open('diag3.txt', 'w', encoding='utf-8')
try:
    f.write("Connecting...\n"); f.flush()
    conn = psycopg2.connect(
        host='aws-1-ap-northeast-1.pooler.supabase.com',
        port=6543,
        user='postgres.qoakbxswwjqfsgbcgepr',
        password='REDACTED_SET_POSTGRES_PASSWORD_ENV',
        dbname='postgres',
        sslmode='require',
        connect_timeout=30,
    )
    f.write("Connected!\n"); f.flush()
    cur = conn.cursor()
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

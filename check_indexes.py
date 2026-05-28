"""检查索引是否创建成功"""
import sys, os, time
sys.path.insert(0, 'd:/pythonProject/openclaw-quant-system')

import psycopg2

conn = psycopg2.connect(
    host='aws-1-ap-northeast-1.pooler.supabase.com',
    port=5432,
    user='postgres.qoakbxswwjqfsgbcgepr',
    password='wYFBB91zViSrk2vl',
    dbname='postgres',
    sslmode='require',
)
cur = conn.cursor()

cur.execute("""
    SELECT indexname FROM pg_indexes WHERE tablename = 'daily_quotes';
""")
indexes = [r[0] for r in cur.fetchall()]
print(f'Indexes: {indexes}')

# 测试查询速度
t0 = time.time()
cur.execute("""
    SELECT COUNT(*) FROM daily_quotes
    WHERE trade_date >= %s AND trade_date <= %s;
""", ('2025-06-01', '2026-05-15'))
row = cur.fetchone()
print(f'Count query took {time.time()-t0:.2f}s, result: {row[0]}')

cur.close()
conn.close()

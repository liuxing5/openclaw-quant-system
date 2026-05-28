#!/usr/bin/env python3
"""测试查询速度"""
import time
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

# Test 1: AVG(pct_chg) GROUP BY trade_date
t0 = time.time()
cur.execute("""
    SELECT trade_date, AVG(pct_chg) as avg_pct
    FROM daily_quotes
    WHERE trade_date BETWEEN '2026-01-01' AND '2026-05-22'
    GROUP BY trade_date
    ORDER BY trade_date
""")
rows = cur.fetchall()
t1 = time.time()
print(f"AVG(pct_chg) GROUP BY: {len(rows)} rows, {t1-t0:.2f}s")

# Test 2: COUNT with CASE (market overview)
t0 = time.time()
cur.execute("""
    SELECT 
        SUM(CASE WHEN pct_chg > 0 THEN 1 ELSE 0 END) as advancers,
        SUM(CASE WHEN pct_chg < 0 THEN 1 ELSE 0 END) as decliners,
        COUNT(*) as total
    FROM daily_quotes
    WHERE trade_date = '2026-04-02'
""")
row = cur.fetchone()
t1 = time.time()
print(f"Market overview: adv={row[0]}, dec={row[1]}, total={row[2]}, {t1-t0:.2f}s")

# Test 3: Single stock batch query
t0 = time.time()
cur.execute("""
    SELECT ts_code, trade_date, open, high, low, close, 
           volume, amount, pct_chg, turnover_rate, amplitude,
           volume_ratio, pe_ratio, pb_ratio, main_force_net
    FROM daily_quotes
    WHERE ts_code IN ('000001.SZ', '000002.SZ', '000063.SZ')
      AND trade_date BETWEEN '2026-03-01' AND '2026-04-02'
    ORDER BY ts_code, trade_date
""")
rows = cur.fetchall()
t1 = time.time()
print(f"3 stocks batch: {len(rows)} rows, {t1-t0:.2f}s")

conn.close()
print("Done!")

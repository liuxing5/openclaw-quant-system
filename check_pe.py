#!/usr/bin/env python3
"""检查PE数据分布"""
import psycopg2
import pandas as pd

conn = psycopg2.connect(
    host='aws-1-ap-northeast-1.pooler.supabase.com',
    port=5432,
    user='postgres.qoakbxswwjqfsgbcgepr',
    password='wYFBB91zViSrk2vl',
    dbname='postgres',
    sslmode='require',
    connect_timeout=30,
)
conn.autocommit = True
cur = conn.cursor()

# Check PE distribution for a few dates
for td in ['2026-04-02', '2026-04-08', '2026-05-07', '2026-05-13', '2026-05-18']:
    cur.execute("""
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN pe_ratio IS NULL THEN 1 ELSE 0 END) as null_pe,
            SUM(CASE WHEN pe_ratio = 0 THEN 1 ELSE 0 END) as zero_pe,
            SUM(CASE WHEN pe_ratio < 0 THEN 1 ELSE 0 END) as neg_pe,
            SUM(CASE WHEN pe_ratio > 0 AND pe_ratio < 100 THEN 1 ELSE 0 END) as ok_pe,
            SUM(CASE WHEN pe_ratio >= 100 THEN 1 ELSE 0 END) as high_pe
        FROM daily_quotes WHERE trade_date = %s
    """, (td,))
    row = cur.fetchone()
    print(f"{td}: total={row[0]}, null={row[1]}, zero={row[2]}, neg={row[3]}, ok(0-100)={row[4]}, high(>100)={row[5]}")

# Check a specific stock's PE over time
print("\n--- 002560.SZ PE over time ---")
cur.execute("""
    SELECT trade_date, pe_ratio, pb_ratio FROM daily_quotes
    WHERE ts_code = '002560.SZ' AND trade_date >= '2026-04-01'
    ORDER BY trade_date LIMIT 10
""")
for row in cur.fetchall():
    print(f"  {row[0]}: PE={row[1]}, PB={row[2]}")

# Check a random stock with high L1 score
print("\n--- Top L1 stocks PE on 2026-04-08 ---")
cur.execute("""
    SELECT ts_code, pe_ratio, pb_ratio, pct_chg, amount
    FROM daily_quotes 
    WHERE trade_date = '2026-04-08' AND amount > 50000000
    ORDER BY pct_chg DESC LIMIT 10
""")
for row in cur.fetchall():
    print(f"  {row[0]}: PE={row[1]}, PB={row[2]}, pct_chg={row[3]:.2f}%, amount={row[4]:.0f}")

conn.close()

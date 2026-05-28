#!/usr/bin/env python3
"""测试查询速度 - 输出到文件"""
import time, sys

f = open('query_result.txt', 'w', encoding='utf-8')
def p(msg):
    f.write(msg + '\n')
    f.flush()

try:
    p("Importing psycopg2...")
    import psycopg2

    p("Connecting...")
    conn = psycopg2.connect(
        host='aws-1-ap-northeast-1.pooler.supabase.com',
        port=5432,
        user='postgres.qoakbxswwjqfsgbcgepr',
        password='wYFBB91zViSrk2vl',
        dbname='postgres',
        sslmode='require',
        connect_timeout=30,
    )
    p("Connected!")
    cur = conn.cursor()

    # Test 1: Simple query
    t0 = time.time()
    cur.execute("SELECT 1")
    p(f"SELECT 1: {cur.fetchone()}, {time.time()-t0:.2f}s")

    # Test 2: Market overview (single day)
    t0 = time.time()
    cur.execute("""
        SELECT SUM(CASE WHEN pct_chg > 0 THEN 1 ELSE 0 END),
               SUM(CASE WHEN pct_chg < 0 THEN 1 ELSE 0 END),
               COUNT(*)
        FROM daily_quotes WHERE trade_date = '2026-04-02'
    """)
    row = cur.fetchone()
    p(f"Market overview: adv={row[0]}, dec={row[1]}, total={row[2]}, {time.time()-t0:.2f}s")

    # Test 3: AVG pct_chg (short range)
    t0 = time.time()
    cur.execute("""
        SELECT trade_date, AVG(pct_chg) FROM daily_quotes
        WHERE trade_date BETWEEN '2026-04-01' AND '2026-05-22'
        GROUP BY trade_date ORDER BY trade_date
    """)
    rows = cur.fetchall()
    p(f"AVG(pct_chg) 2mo: {len(rows)} rows, {time.time()-t0:.2f}s")

    # Test 4: Full range index proxy
    t0 = time.time()
    cur.execute("""
        SELECT trade_date, AVG(pct_chg) FROM daily_quotes
        WHERE trade_date BETWEEN '2026-01-01' AND '2026-05-22'
        GROUP BY trade_date ORDER BY trade_date
    """)
    rows = cur.fetchall()
    p(f"AVG(pct_chg) 5mo: {len(rows)} rows, {time.time()-t0:.2f}s")

    # Test 5: Batch stock query
    t0 = time.time()
    cur.execute("""
        SELECT ts_code, trade_date, open, close, pct_chg, turnover_rate,
               volume_ratio, pe_ratio, pb_ratio, main_force_net, amplitude
        FROM daily_quotes
        WHERE ts_code IN ('000001.SZ','000002.SZ','000063.SZ','600000.SH','600519.SH')
          AND trade_date BETWEEN '2026-03-01' AND '2026-04-02'
        ORDER BY ts_code, trade_date
    """)
    rows = cur.fetchall()
    p(f"5 stocks batch: {len(rows)} rows, {time.time()-t0:.2f}s")

    # Test 6: Large batch (50 stocks)
    t0 = time.time()
    cur.execute("""
        SELECT ts_code FROM daily_quotes
        WHERE trade_date = '2026-04-02' AND amount > 50000000
        ORDER BY amount DESC LIMIT 50
    """)
    codes = [r[0] for r in cur.fetchall()]
    p(f"Top 50 by amount: {len(codes)} codes, {time.time()-t0:.2f}s")

    if codes:
        t0 = time.time()
        placeholders = ','.join(['%s'] * len(codes))
        cur.execute(f"""
            SELECT ts_code, trade_date, open, close, pct_chg, turnover_rate,
                   volume_ratio, pe_ratio, pb_ratio, main_force_net, amplitude
            FROM daily_quotes
            WHERE ts_code IN ({placeholders})
              AND trade_date BETWEEN '2026-01-01' AND '2026-04-02'
            ORDER BY ts_code, trade_date
        """, codes)
        rows = cur.fetchall()
        p(f"50 stocks history: {len(rows)} rows, {time.time()-t0:.2f}s")

    conn.close()
    p("Done!")
except Exception as e:
    import traceback
    p(f"ERROR: {e}")
    p(traceback.format_exc())

f.close()

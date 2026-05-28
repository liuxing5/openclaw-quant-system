"""诊断用户反馈的4只股票为何入场偏晚 - 直接连接"""
import os, traceback
os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '5432'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

import psycopg2
from psycopg2.extras import RealDictCursor

try:
    conn = psycopg2.connect(
        host='aws-1-ap-northeast-1.pooler.supabase.com',
        port=5432,
        user='postgres.qoakbxswwjqfsgbcgepr',
        password='wYFBB91zViSrk2vl',
        dbname='postgres',
        sslmode='require',
        connect_timeout=30,
    )
    cur = conn.cursor(cursor_factory=RealDictCursor)

    with open('d:/pythonProject/openclaw-quant-system/diag_stocks_result.txt', 'w', encoding='utf-8') as f:
        stocks = [
            ('002361.SZ', '2026-03-20', '2026-04-10'),
            ('002384.SZ', '2026-03-25', '2026-04-10'),
            ('301308.SZ', '2025-09-01', '2026-02-01'),
            ('002565.SZ', '2026-01-06', '2026-01-15'),
        ]

        for ts_code, start, end in stocks:
            cur.execute("""
                SELECT trade_date, open, close, pct_chg, amount
                FROM daily_quotes
                WHERE ts_code = %s AND trade_date BETWEEN %s AND %s
                ORDER BY trade_date
            """, (ts_code, start, end))
            rows = cur.fetchall()
            f.write(f"\n=== {ts_code} ({start} ~ {end}) ===\n")
            for r in rows:
                amt_wan = r['amount'] / 10000 if r['amount'] else 0
                f.write(f"  {r['trade_date']} C={r['close']:.2f} pct={r['pct_chg']:.2f}% amt={amt_wan:.0f}万\n")

        # 趋势启动条件检查
        f.write("\n\n=== 趋势启动条件检查 ===\n")
        for ts_code, start, end in stocks:
            cur.execute("""
                SELECT trade_date, pct_chg, amount
                FROM daily_quotes
                WHERE ts_code = %s
                  AND pct_chg BETWEEN 1 AND 7
                  AND amount > 50000000
                  AND trade_date BETWEEN %s AND %s
                ORDER BY trade_date
            """, (ts_code, start, end))
            rows = cur.fetchall()
            f.write(f"\n{ts_code} 满足趋势启动条件的日期:\n")
            for r in rows:
                f.write(f"  {r['trade_date']} pct={r['pct_chg']:.2f}% amt={r['amount']/10000:.0f}万\n")

        # 002361 3月23-27日详细
        f.write("\n\n=== 002361 3月23-27日详细 ===\n")
        cur.execute("""
            SELECT trade_date, open, close, high, low, pct_chg, amount
            FROM daily_quotes
            WHERE ts_code = '002361.SZ'
              AND trade_date BETWEEN '2026-03-23' AND '2026-03-27'
            ORDER BY trade_date
        """)
        for r in cur.fetchall():
            f.write(f"  {r['trade_date']} O={r['open']:.2f} C={r['close']:.2f} H={r['high']:.2f} L={r['low']:.2f} pct={r['pct_chg']:.2f}% amt={r['amount']/10000:.0f}万\n")

        # 002384 3月28日-4月8日详细
        f.write("\n\n=== 002384 3月28日-4月8日详细 ===\n")
        cur.execute("""
            SELECT trade_date, open, close, high, low, pct_chg, amount
            FROM daily_quotes
            WHERE ts_code = '002384.SZ'
              AND trade_date BETWEEN '2026-03-28' AND '2026-04-08'
            ORDER BY trade_date
        """)
        for r in cur.fetchall():
            f.write(f"  {r['trade_date']} O={r['open']:.2f} C={r['close']:.2f} H={r['high']:.2f} L={r['low']:.2f} pct={r['pct_chg']:.2f}% amt={r['amount']/10000:.0f}万\n")

        # 301308 月度走势
        f.write("\n\n=== 301308 月度走势 ===\n")
        cur.execute("""
            SELECT DATE_TRUNC('month', trade_date) as m,
                   MIN(close) as low_c, MAX(close) as high_c,
                   AVG(pct_chg) as avg_pct, SUM(amount) as total_amt
            FROM daily_quotes
            WHERE ts_code = '301308.SZ'
              AND trade_date BETWEEN '2025-09-01' AND '2026-05-26'
            GROUP BY DATE_TRUNC('month', trade_date)
            ORDER BY m
        """)
        for r in cur.fetchall():
            f.write(f"  {r['m']} 低={r['low_c']:.2f} 高={r['high_c']:.2f} 均涨={r['avg_pct']:.2f}% 总额={r['total_amt']/100000000:.1f}亿\n")

        # 002361 在3月25-26日的排名
        f.write("\n\n=== 002361 在3月25-26日的涨幅排名 ===\n")
        for dt in ['2026-03-25', '2026-03-26']:
            cur.execute("""
                SELECT pct_chg FROM daily_quotes WHERE ts_code='002361.SZ' AND trade_date=%s
            """, (dt,))
            row = cur.fetchone()
            if row:
                pct = row['pct_chg']
                cur.execute("""
                    SELECT COUNT(*)+1 as rank FROM daily_quotes
                    WHERE trade_date = %s AND pct_chg > %s
                """, (dt, pct))
                rank = cur.fetchone()['rank']
                f.write(f"  {dt}: 002361涨幅={pct:.2f}%, 排名约第{rank}名\n")

        # 002384 在4月2-4日的排名
        f.write("\n\n=== 002384 在4月2-4日的涨幅排名 ===\n")
        for dt in ['2026-04-02', '2026-04-03', '2026-04-04']:
            cur.execute("""
                SELECT pct_chg FROM daily_quotes WHERE ts_code='002384.SZ' AND trade_date=%s
            """, (dt,))
            row = cur.fetchone()
            if row:
                pct = row['pct_chg']
                cur.execute("""
                    SELECT COUNT(*)+1 as rank FROM daily_quotes
                    WHERE trade_date = %s AND pct_chg > %s
                """, (dt, pct))
                rank = cur.fetchone()['rank']
                f.write(f"  {dt}: 002384涨幅={pct:.2f}%, 排名约第{rank}名\n")

        # 301308 在2025年9月-2026年1月满足趋势启动的日期
        f.write("\n\n=== 301308 满足趋势启动条件的日期(2025-09~2026-02) ===\n")
        cur.execute("""
            SELECT trade_date, pct_chg, amount
            FROM daily_quotes
            WHERE ts_code = '301308.SZ'
              AND pct_chg BETWEEN 1 AND 7
              AND amount > 50000000
              AND trade_date BETWEEN '2025-09-01' AND '2026-02-01'
            ORDER BY trade_date
        """)
        for r in cur.fetchall():
            f.write(f"  {r['trade_date']} pct={r['pct_chg']:.2f}% amt={r['amount']/10000:.0f}万\n")

        f.write("\nDone\n")

    conn.close()
    print("Done")

except Exception as e:
    with open('d:/pythonProject/openclaw-quant-system/diag_stocks_result.txt', 'w', encoding='utf-8') as f:
        f.write(f"ERROR: {e}\n")
        traceback.print_exc(file=f)

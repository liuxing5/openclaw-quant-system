"""诊断用户反馈的4只股票为何入场偏晚"""
import sys, traceback, os

# 设置数据库环境变量
os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '5432'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

# 先写一个标记文件确认脚本开始执行
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'diag_start.txt'), 'w') as f:
    f.write('started\n')

try:
    from core.db.connection import get_db, close_db_session
    close_db_session()  # 清除可能缓存的旧连接
    # 验证环境变量
    with open('d:/pythonProject/openclaw-quant-system/diag_env.txt', 'w') as ef:
        ef.write(f"HOST={os.environ.get('POSTGRES_HOST')}\n")
        ef.write(f"PORT={os.environ.get('POSTGRES_PORT')}\n")
        ef.write(f"DB={os.environ.get('POSTGRES_DB')}\n")
    db = get_db()
    cur = db.cursor()

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

        # 002361详细
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

        # 301308月度走势
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

        # 检查Layer1选股条件：涨幅前50
        f.write("\n\n=== 002361 是否满足涨幅前50 ===\n")
        for dt in ['2026-03-25', '2026-03-26', '2026-03-27']:
            cur.execute("""
                SELECT COUNT(*) as cnt FROM daily_quotes
                WHERE trade_date = %s AND pct_chg > (SELECT pct_chg FROM daily_quotes WHERE ts_code='002361.SZ' AND trade_date=%s)
                AND ts_code NOT IN (SELECT ts_code FROM stock_basic WHERE name LIKE '%%ST%%' OR name LIKE '%%退%%')
            """, (dt, dt))
            cnt = cur.fetchone()['cnt']
            cur.execute("SELECT pct_chg FROM daily_quotes WHERE ts_code='002361.SZ' AND trade_date=%s", (dt,))
            pct = cur.fetchone()
            pct_val = pct['pct_chg'] if pct else 0
            f.write(f"  {dt}: 002361涨幅={pct_val:.2f}%, 排名约第{cnt+1}名\n")

        f.write("\nDone\n")

except Exception as e:
    with open('d:/pythonProject/openclaw-quant-system/diag_stocks_result.txt', 'w', encoding='utf-8') as f:
        f.write(f"ERROR: {e}\n")
        traceback.print_exc(file=f)

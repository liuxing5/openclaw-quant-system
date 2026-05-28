#!/usr/bin/env python3
"""逐步验证回测中的每个查询"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg2
import pandas as pd
from datetime import date, timedelta

# 输出到文件
import builtins
out = open('test_bt_step_output.txt', 'w', encoding='utf-8')
def p(msg):
    builtins.print(msg, flush=True)
    out.write(msg + '\n')
    out.flush()

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

# 1. 交易日
t0 = time.time()
cur.execute("SELECT DISTINCT trade_date FROM daily_quotes WHERE trade_date BETWEEN %s AND %s ORDER BY trade_date",
            (date(2026,4,1), date(2026,5,22)))
days = [r[0] for r in cur.fetchall()]
p(f"1. 交易日: {len(days)} 天, {time.time()-t0:.1f}s")
p(f"   前5天: {days[:5]}")
p(f"   后5天: {days[-5:]}")

# 2. 指数代理
t0 = time.time()
cur.execute("""
    SELECT trade_date, AVG(pct_chg) as avg_pct FROM daily_quotes
    WHERE trade_date BETWEEN %s AND %s
    GROUP BY trade_date ORDER BY trade_date
""", (date(2026,1,1), date(2026,5,22)))
idx_rows = cur.fetchall()
p(f"2. 指数代理: {len(idx_rows)} 天, {time.time()-t0:.1f}s")

# 3. 某天的市场宽度
td = days[0] if days else date(2026,4,2)
t0 = time.time()
cur.execute("""
    SELECT SUM(CASE WHEN pct_chg > 0 THEN 1 ELSE 0 END), COUNT(*)
    FROM daily_quotes WHERE trade_date = %s
""", (td,))
row = cur.fetchone()
breadth = (row[0] or 0) / (row[1] or 1)
p(f"3. {td} 市场宽度: {breadth:.2%} ({row[0]}/{row[1]}), {time.time()-t0:.1f}s")

# 4. 某天的日线数据
t0 = time.time()
cur.execute("""
    SELECT ts_code, open, close, pct_chg, turnover_rate, amplitude,
           volume_ratio, pe_ratio, pb_ratio, main_force_net, amount
    FROM daily_quotes WHERE trade_date = %s AND amount >= %s
    ORDER BY amount DESC LIMIT 50
""", (td, 5e7))
daily_rows = cur.fetchall()
p(f"4. {td} 日线数据: {len(daily_rows)} 只, {time.time()-t0:.1f}s")
if daily_rows:
    p(f"   Top3: {[(r[0], float(r[3]) if r[3] else 0, float(r[10]) if r[10] else 0) for r in daily_rows[:3]]}")

# 5. 次日开盘价
if len(days) > 1:
    next_td = days[1]
    t0 = time.time()
    codes = [r[0] for r in daily_rows[:10]]
    for code in codes:
        cur.execute("SELECT open FROM daily_quotes WHERE ts_code=%s AND trade_date=%s", (code, next_td))
        price_row = cur.fetchone()
        p(f"   {code} 次日开盘价: {price_row[0] if price_row else 'N/A'}")
    p(f"5. 次日开盘价查询: {time.time()-t0:.1f}s")

# 6. 测试完整的一天回测流程
p("\n=== 测试完整1天回测 ===")
td = days[0]
next_td = days[1] if len(days) > 1 else None
t0 = time.time()

# L0
cur.execute("SELECT SUM(CASE WHEN pct_chg > 0 THEN 1 ELSE 0 END), COUNT(*) FROM daily_quotes WHERE trade_date = %s", (td,))
row = cur.fetchone()
breadth = (row[0] or 0) / (row[1] or 1)
p(f"  L0: breadth={breadth:.2%} {'PASS' if breadth >= 0.3 else 'REJECT'}")

# L1
cur.execute("""
    SELECT ts_code, pct_chg, turnover_rate, volume_ratio, main_force_net, amount
    FROM daily_quotes WHERE trade_date = %s AND amount >= %s
    ORDER BY amount DESC LIMIT 50
""", (td, 5e7))
l1_rows = cur.fetchall()
p(f"  L1: {len(l1_rows)} 只通过")

# L2 (简单过滤)
l2_codes = [r[0] for r in l1_rows if r[0]]  # 不过滤PE
p(f"  L2: {len(l2_codes)} 只通过")

# L3+L5 (简化评分)
scores = []
for r in l1_rows:
    code, pct, turn, vr, mf, amt = r[0], float(r[1] or 0), float(r[2] or 0), float(r[3] or 0), float(r[4] or 0), float(r[5] or 0)
    ov_base = 30 if pct > 5 else (25 if pct > 3 else (20 if pct > 1 else (15 if pct > 0 else 5)))
    meta = ov_base + min(pct * 2, 40)
    scores.append((code, meta, pct))

scores.sort(key=lambda x: x[1], reverse=True)
p(f"  Top5: {scores[:5]}")

# 开仓
if next_td:
    opened = 0
    for code, meta, pct in scores[:5]:
        cur.execute("SELECT open FROM daily_quotes WHERE ts_code=%s AND trade_date=%s", (code, next_td))
        price_row = cur.fetchone()
        if price_row and price_row[0]:
            p(f"  开仓: {code} @ {price_row[0]} meta={meta:.1f}")
            opened += 1
        else:
            p(f"  无价格: {code}")
    p(f"  开仓: {opened} 只")

p(f"  1天耗时: {time.time()-t0:.1f}s")

conn.close()
p("\nDONE!")

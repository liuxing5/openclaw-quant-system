#!/usr/bin/env python3
"""诊断漏斗 v4 - 单连接复用"""
import sys, os, time
import psycopg2
import pandas as pd
import numpy as np
from datetime import date, timedelta

out = open('diag_v4.txt', 'w', encoding='utf-8')
def p(msg):
    out.write(msg + '\n')
    out.flush()

try:
    p("Connecting...")
    t0 = time.time()
    conn = psycopg2.connect(
        host='aws-1-ap-northeast-1.pooler.supabase.com',
        port=5432,
        user='postgres.qoakbxswwjqfsgbcgepr',
        password='wYFBB91zViSrk2vl',
        dbname='postgres',
        sslmode='require',
        connect_timeout=30,
    )
    conn.autocommit = True  # 避免事务积累
    p(f"  Connected in {time.time()-t0:.1f}s")
    cur = conn.cursor()

    # Step 1: Trading days
    p("Step 1: Trading days...")
    t0 = time.time()
    cur.execute("SELECT DISTINCT trade_date FROM daily_quotes WHERE trade_date BETWEEN '2026-04-01' AND '2026-05-22' ORDER BY trade_date")
    trading_days = [r[0] for r in cur.fetchall()]
    p(f"  {len(trading_days)} days, {time.time()-t0:.1f}s")

    # Step 2: Index proxy (shorter range first)
    p("Step 2: Index proxy (2 months)...")
    t0 = time.time()
    cur.execute("""
        SELECT trade_date, AVG(pct_chg) FROM daily_quotes
        WHERE trade_date BETWEEN '2026-03-01' AND '2026-05-22'
        GROUP BY trade_date ORDER BY trade_date
    """)
    idx_rows = cur.fetchall()
    p(f"  {len(idx_rows)} days, {time.time()-t0:.1f}s")

    # Step 3: Market overview for 2026-04-02
    p("Step 3: Market overview 2026-04-02...")
    t0 = time.time()
    cur.execute("""
        SELECT SUM(CASE WHEN pct_chg > 0 THEN 1 ELSE 0 END),
               SUM(CASE WHEN pct_chg < 0 THEN 1 ELSE 0 END),
               COUNT(*) FROM daily_quotes WHERE trade_date = '2026-04-02'
    """)
    row = cur.fetchone()
    adv, dec, total = int(row[0]), int(row[1]), int(row[2])
    breadth = adv / total
    p(f"  adv={adv}, dec={dec}, breadth={breadth:.3f}, {time.time()-t0:.1f}s")

    # Step 4: Daily data
    p("Step 4: Daily data 2026-04-02...")
    t0 = time.time()
    cur.execute("""
        SELECT ts_code, open, close, pct_chg, turnover_rate, amplitude,
               volume_ratio, pe_ratio, pb_ratio, main_force_net, amount
        FROM daily_quotes WHERE trade_date = '2026-04-02' AND amount > 50000000
        ORDER BY amount DESC
    """)
    rows = cur.fetchall()
    daily_df = pd.DataFrame(rows, columns=[
        'ts_code','open','close','pct_chg','turnover_rate','amplitude',
        'volume_ratio','pe_ratio','pb_ratio','main_force_net','amount'])
    for c in daily_df.columns[1:]:
        daily_df[c] = pd.to_numeric(daily_df[c], errors='coerce')
    p(f"  {len(daily_df)} stocks, {time.time()-t0:.1f}s")

    # Step 5: L1 scoring
    p("Step 5: L1 scoring...")
    t0 = time.time()
    scores = pd.DataFrame()
    scores['ts_code'] = daily_df['ts_code']
    for col in ['pct_chg', 'turnover_rate', 'volume_ratio']:
        vals = daily_df[col].fillna(0)
        scores[col+'_n'] = (vals - vals.min()) / (vals.max() - vals.min() + 1e-10)
    vals = daily_df['main_force_net'].fillna(0)
    scores['mf_n'] = (vals - vals.min()) / (vals.max() - vals.min() + 1e-10)
    scores['total'] = scores['pct_chg_n']*0.3 + scores['turnover_rate_n']*0.25 + scores['volume_ratio_n']*0.25 + scores['mf_n']*0.2
    l1 = scores.nlargest(50, 'total')
    l1_codes = l1['ts_code'].tolist()
    p(f"  Top 50, {time.time()-t0:.1f}s")
    p(f"  Top5 scores: {l1['total'].head(5).tolist()}")
    p(f"  Top5 codes: {l1_codes[:5]}")

    # Step 6: L2 filter
    p("Step 6: L2 filter...")
    l2_df = daily_df[daily_df['ts_code'].isin(l1_codes)]
    l2_df = l2_df[(l2_df['pe_ratio'] > 0) & (l2_df['pe_ratio'] < 100)]
    l2_codes = l2_df['ts_code'].tolist()
    p(f"  {len(l2_codes)} passed L2")

    # Step 7: L3 launch signals
    p("Step 7: L3 launch signals...")
    t0 = time.time()
    if l2_codes:
        ph = ','.join(['%s'] * len(l2_codes))
        cur.execute(f"""
            SELECT ts_code, trade_date, close, pct_chg, volume
            FROM daily_quotes
            WHERE ts_code IN ({ph}) AND trade_date BETWEEN '2026-02-01' AND '2026-04-02'
            ORDER BY ts_code, trade_date
        """, l2_codes)
        hist = pd.DataFrame(cur.fetchall(), columns=['ts_code','trade_date','close','pct_chg','volume'])
        for c in ['close','pct_chg','volume']:
            hist[c] = pd.to_numeric(hist[c], errors='coerce')
        p(f"  History: {len(hist)} rows, {time.time()-t0:.1f}s")
        
        today = daily_df[daily_df['ts_code'].isin(l2_codes)]
        launch = today[(today['pct_chg'] > 3) & (today['volume_ratio'] > 1.5)]
        p(f"  Launch signals: {len(launch)} stocks")
        if not launch.empty:
            p(f"  Launch codes: {launch['ts_code'].tolist()[:10]}")
            p(f"  Launch pct_chg: {launch['pct_chg'].tolist()[:10]}")
    else:
        p("  No L2 stocks")

    # Step 8: Overnight scoring
    p("Step 8: Overnight scoring...")
    if l2_codes:
        today = daily_df[daily_df['ts_code'].isin(l2_codes)].copy()
        today['ov_score'] = (
            (today['pct_chg'].clip(0,10)/10)*40 +
            (today['turnover_rate'].clip(0,20)/20)*30 +
            (today['main_force_net'].clip(-5,5)+5)/10*30
        )
        ov = today[today['ov_score'] > 30]
        p(f"  {len(ov)} stocks with ov_score > 30")
        if not ov.empty:
            top5 = ov.nlargest(5, 'ov_score')
            p(f"  Top5: {top5['ts_code'].tolist()}")
            p(f"  Top5 scores: {top5['ov_score'].tolist()}")

    # Step 9: Summary
    p("\n=== SUMMARY ===")
    p(f"L0 passed: breadth={breadth:.3f} (>0.40 = {'YES' if breadth > 0.40 else 'NO'})")
    p(f"L1: {len(l1_codes)} stocks")
    p(f"L2: {len(l2_codes)} stocks")
    p(f"L3: {len(launch) if l2_codes else 0} launch signals")
    p(f"L5: {len(ov) if l2_codes else 0} overnight candidates")
    
    # Key issue: if L0 blocks or L3 has no signals
    if breadth <= 0.40:
        p("\n*** L0 BLOCKS! Market breadth too low. Need to lower L0 threshold. ***")
    if l2_codes and len(launch) == 0:
        p("\n*** L3 has no launch signals! pct_chg>3% AND volume_ratio>1.5 is too strict. ***")
        # Check how many have pct_chg > 1%
        today = daily_df[daily_df['ts_code'].isin(l2_codes)]
        p(f"  Stocks with pct_chg > 1%: {len(today[today['pct_chg'] > 1])}")
        p(f"  Stocks with pct_chg > 2%: {len(today[today['pct_chg'] > 2])}")
        p(f"  Stocks with pct_chg > 0%: {len(today[today['pct_chg'] > 0])}")

    conn.close()
    p("\nDone!")

except Exception as e:
    import traceback
    p(f"ERROR: {e}")
    p(traceback.format_exc())

out.close()

#!/usr/bin/env python3
"""诊断漏斗 v3 - 直接SQL"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '5432'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

import time
import psycopg2
import pandas as pd
import numpy as np
from datetime import date, timedelta

out = open('diag_v3.txt', 'w', encoding='utf-8')
def p(msg):
    out.write(msg + '\n')
    out.flush()

try:
    p("Step 1: Connect to DB...")
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
    p(f"  Connected in {time.time()-t0:.1f}s")
    cur = conn.cursor()

    # Step 2: Get trading days
    p("Step 2: Get trading days...")
    t0 = time.time()
    cur.execute("""
        SELECT DISTINCT trade_date FROM daily_quotes
        WHERE trade_date BETWEEN '2026-04-01' AND '2026-05-22'
        ORDER BY trade_date
    """)
    trading_days = [r[0] for r in cur.fetchall()]
    p(f"  {len(trading_days)} trading days, {time.time()-t0:.1f}s")

    # Step 3: Get index proxy data
    p("Step 3: Get index proxy...")
    t0 = time.time()
    cur.execute("""
        SELECT trade_date, AVG(pct_chg) as avg_pct
        FROM daily_quotes
        WHERE trade_date BETWEEN '2026-01-01' AND '2026-05-22'
        GROUP BY trade_date
        ORDER BY trade_date
    """)
    rows = cur.fetchall()
    idx_df = pd.DataFrame(rows, columns=['trade_date', 'avg_pct'])
    idx_df['avg_pct'] = pd.to_numeric(idx_df['avg_pct'], errors='coerce')
    p(f"  {len(idx_df)} days, {time.time()-t0:.1f}s")

    # Step 4: Test L0 for 2026-04-02
    p("Step 4: L0 market risk for 2026-04-02...")
    td = date(2026, 4, 2)
    cur.execute("""
        SELECT SUM(CASE WHEN pct_chg > 0 THEN 1 ELSE 0 END),
               SUM(CASE WHEN pct_chg < 0 THEN 1 ELSE 0 END),
               COUNT(*)
        FROM daily_quotes WHERE trade_date = %s
    """, (td,))
    row = cur.fetchone()
    advancers, decliners, total = int(row[0]), int(row[1]), int(row[2])
    breadth_ratio = advancers / total if total > 0 else 0
    p(f"  advancers={advancers}, decliners={decliners}, breadth={breadth_ratio:.3f}")
    l0_passed = breadth_ratio > 0.40  # L0 threshold
    p(f"  L0 passed: {l0_passed}")

    # Step 5: Get daily data for 2026-04-02
    p("Step 5: Get daily data for 2026-04-02...")
    t0 = time.time()
    cur.execute("""
        SELECT ts_code, open, close, pct_chg, turnover_rate, amplitude,
               volume_ratio, pe_ratio, pb_ratio, main_force_net, amount, volume
        FROM daily_quotes
        WHERE trade_date = %s AND amount > 50000000
        ORDER BY amount DESC
    """, (td,))
    rows = cur.fetchall()
    daily_df = pd.DataFrame(rows, columns=[
        'ts_code', 'open', 'close', 'pct_chg', 'turnover_rate', 'amplitude',
        'volume_ratio', 'pe_ratio', 'pb_ratio', 'main_force_net', 'amount', 'volume'
    ])
    for c in ['open','close','pct_chg','turnover_rate','amplitude','volume_ratio','pe_ratio','pb_ratio','main_force_net','amount','volume']:
        daily_df[c] = pd.to_numeric(daily_df[c], errors='coerce')
    p(f"  {len(daily_df)} active stocks, {time.time()-t0:.1f}s")

    # Step 6: Simple L1 scoring (top 50 by composite score)
    p("Step 6: L1 multi-factor scoring...")
    t0 = time.time()
    # Score components: pct_chg, turnover_rate, volume_ratio, main_force_net
    scores = pd.DataFrame()
    scores['ts_code'] = daily_df['ts_code']
    # Normalize each factor to 0-1
    for col in ['pct_chg', 'turnover_rate', 'volume_ratio']:
        if col in daily_df.columns:
            vals = daily_df[col].fillna(0)
            scores[col + '_norm'] = (vals - vals.min()) / (vals.max() - vals.min() + 1e-10)
    
    # main_force_net: positive is good
    if 'main_force_net' in daily_df.columns:
        vals = daily_df['main_force_net'].fillna(0)
        scores['mf_norm'] = (vals - vals.min()) / (vals.max() - vals.min() + 1e-10)
    else:
        scores['mf_norm'] = 0.5

    scores['total_score'] = (
        scores.get('pct_chg_norm', 0.5) * 0.30 +
        scores.get('turnover_rate_norm', 0.5) * 0.25 +
        scores.get('volume_ratio_norm', 0.5) * 0.25 +
        scores.get('mf_norm', 0.5) * 0.20
    )
    
    l1 = scores.nlargest(50, 'total_score')
    l1_codes = l1['ts_code'].tolist()
    p(f"  Top 50 stocks, {time.time()-t0:.1f}s")
    p(f"  Top 5 scores: {l1['total_score'].head(5).tolist()}")
    p(f"  Top 5 codes: {l1_codes[:5]}")

    # Step 7: L2 fundamental filter
    p("Step 7: L2 fundamental filter...")
    t0 = time.time()
    # Filter: PE > 0 and < 100, not ST
    l2_df = daily_df[daily_df['ts_code'].isin(l1_codes)]
    l2_df = l2_df[(l2_df['pe_ratio'] > 0) & (l2_df['pe_ratio'] < 100)]
    l2_codes = l2_df['ts_code'].tolist()
    p(f"  {len(l2_codes)} passed L2 (filtered {len(l1_codes)-len(l2_codes)}), {time.time()-t0:.1f}s")

    # Step 8: L3 launch signals (simplified)
    p("Step 8: L3 launch signals...")
    t0 = time.time()
    if l2_codes:
        placeholders = ','.join(['%s'] * len(l2_codes))
        cur.execute(f"""
            SELECT ts_code, trade_date, close, pct_chg, volume, amount
            FROM daily_quotes
            WHERE ts_code IN ({placeholders})
              AND trade_date BETWEEN '2026-01-01' AND %s
            ORDER BY ts_code, trade_date
        """, l2_codes + [td])
        rows = cur.fetchall()
        hist_df = pd.DataFrame(rows, columns=['ts_code','trade_date','close','pct_chg','volume','amount'])
        for c in ['close','pct_chg','volume','amount']:
            hist_df[c] = pd.to_numeric(hist_df[c], errors='coerce')
        p(f"  History: {len(hist_df)} rows, {time.time()-t0:.1f}s")
        
        # Simple launch signal: pct_chg > 3% and volume_ratio > 1.5
        today_data = daily_df[daily_df['ts_code'].isin(l2_codes)]
        launch = today_data[(today_data['pct_chg'] > 3) & (today_data['volume_ratio'] > 1.5)]
        p(f"  Launch signals: {len(launch)} stocks")
        if not launch.empty:
            p(f"  Launch codes: {launch['ts_code'].tolist()[:5]}")
    else:
        p("  No L2 stocks, skip L3")

    # Step 9: L5 overnight scoring (simplified)
    p("Step 9: L5 overnight scoring...")
    t0 = time.time()
    if l2_codes:
        # Simplified: score based on today's close position and volume
        today_data = daily_df[daily_df['ts_code'].isin(l2_codes)].copy()
        # Score: higher pct_chg + higher turnover + positive main_force = better overnight
        today_data['overnight_score'] = (
            (today_data['pct_chg'].clip(0, 10) / 10) * 40 +
            (today_data['turnover_rate'].clip(0, 20) / 20) * 30 +
            (today_data['main_force_net'].clip(-5, 5) + 5) / 10 * 30
        )
        overnight = today_data[today_data['overnight_score'] > 30]
        p(f"  {len(overnight)} stocks with overnight score > 30, {time.time()-t0:.1f}s")
        if not overnight.empty:
            top5 = overnight.nlargest(5, 'overnight_score')
            p(f"  Top5 scores: {top5['overnight_score'].tolist()}")
            p(f"  Top5 codes: {top5['ts_code'].tolist()}")
    else:
        p("  No L2 stocks, skip L5")

    conn.close()
    p("\nAll steps completed!")

except Exception as e:
    import traceback
    p(f"ERROR: {e}")
    p(traceback.format_exc())

out.close()

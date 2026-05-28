#!/usr/bin/env python3
"""检查多个日期的市场breadth和L2通过率"""
import sys, os, time
import psycopg2
import pandas as pd

out = open('diag_v5.txt', 'w', encoding='utf-8')
def p(msg):
    out.write(msg + '\n')
    out.flush()

try:
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
    conn.autocommit = True
    cur = conn.cursor()
    p("Connected!")

    # Get all trading days in range
    cur.execute("""
        SELECT DISTINCT trade_date FROM daily_quotes
        WHERE trade_date BETWEEN '2026-04-01' AND '2026-05-22'
        ORDER BY trade_date
    """)
    trading_days = [r[0] for r in cur.fetchall()]
    p(f"Trading days: {len(trading_days)}")

    # For each day, check breadth and L2 pass rate
    p("\nDate       | Breadth | Adv | Dec | Active | L1>0.3 | L2_pass | L2_PE_neg | L2_PE_high")
    p("-" * 95)

    for td in trading_days:
        # Market breadth
        cur.execute("""
            SELECT SUM(CASE WHEN pct_chg > 0 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN pct_chg < 0 THEN 1 ELSE 0 END),
                   COUNT(*) FROM daily_quotes WHERE trade_date = %s
        """, (td,))
        row = cur.fetchone()
        adv, dec, total = int(row[0]), int(row[1]), int(row[2])
        breadth = adv / total if total > 0 else 0

        # Active stocks (amount > 50M)
        cur.execute("""
            SELECT ts_code, pct_chg, turnover_rate, volume_ratio, 
                   pe_ratio, pb_ratio, main_force_net
            FROM daily_quotes 
            WHERE trade_date = %s AND amount > 50000000
        """, (td,))
        rows = cur.fetchall()
        active_df = pd.DataFrame(rows, columns=[
            'ts_code','pct_chg','turnover_rate','volume_ratio',
            'pe_ratio','pb_ratio','main_force_net'])
        for c in active_df.columns[1:]:
            active_df[c] = pd.to_numeric(active_df[c], errors='coerce')

        # L1 scoring (simplified)
        scores = pd.DataFrame()
        scores['ts_code'] = active_df['ts_code']
        for col in ['pct_chg', 'turnover_rate', 'volume_ratio']:
            vals = active_df[col].fillna(0)
            scores[col+'_n'] = (vals - vals.min()) / (vals.max() - vals.min() + 1e-10)
        vals = active_df['main_force_net'].fillna(0)
        scores['mf_n'] = (vals - vals.min()) / (vals.max() - vals.min() + 1e-10)
        scores['total'] = scores['pct_chg_n']*0.3 + scores['turnover_rate_n']*0.25 + scores['volume_ratio_n']*0.25 + scores['mf_n']*0.2

        l1_count = len(scores[scores['total'] > 0.30])
        
        # L2: PE filter
        l1_codes = scores[scores['total'] > 0.30]['ts_code'].tolist()
        l2_df = active_df[active_df['ts_code'].isin(l1_codes)]
        pe_neg = len(l2_df[l2_df['pe_ratio'] <= 0])
        pe_high = len(l2_df[l2_df['pe_ratio'] > 100])
        l2_pass = len(l2_df[(l2_df['pe_ratio'] > 0) & (l2_df['pe_ratio'] < 100)])

        p(f"{td} | {breadth:.3f} | {adv:4d} | {dec:4d} | {len(active_df):5d} | {l1_count:5d} | {l2_pass:5d} | {pe_neg:5d} | {pe_high:5d}")

    conn.close()
    p("\nDone!")

except Exception as e:
    import traceback
    p(f"ERROR: {e}")
    p(traceback.format_exc())

out.close()

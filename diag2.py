#!/usr/bin/env python3
"""简单数据库测试"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '6543'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

f = open('diag2.txt', 'w', encoding='utf-8')

try:
    f.write("Step 1: DB connection...\n"); f.flush()
    from core.db.connection import get_db_fresh
    conn = get_db_fresh()
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM daily_quotes WHERE trade_date = '2026-04-01'")
    count = cur.fetchone()[0]
    f.write(f"  2026-04-01: {count} records\n"); f.flush()
    conn.close()

    f.write("Step 2: Data adapter...\n"); f.flush()
    from datetime import date
    from strategies.meta_strategy.db_data_adapter import get_trading_days, get_daily_quotes_for_date, get_market_overview
    
    days = get_trading_days(date(2026, 4, 1), date(2026, 5, 22))
    f.write(f"  Trading days: {len(days)}\n"); f.flush()
    
    daily = get_daily_quotes_for_date(date(2026, 4, 2), min_amount=5e7)
    f.write(f"  Active stocks 2026-04-02: {len(daily)}\n"); f.flush()
    
    overview = get_market_overview(date(2026, 4, 2))
    f.write(f"  Market: adv={overview['advancers']}, dec={overview['decliners']}, ratio={overview['breadth_ratio']:.3f}\n"); f.flush()

    f.write("Step 3: Layer0...\n"); f.flush()
    from strategies.meta_strategy.db_data_adapter import get_index_quotes
    from strategies.meta_strategy.db_backtester import layer0_market_risk, MetaBacktestConfig
    
    cfg = MetaBacktestConfig()
    idx_data = get_index_quotes('000001.SH', date(2026, 1, 1), date(2026, 5, 22))
    f.write(f"  Index data: {len(idx_data)} rows\n"); f.flush()
    
    idx_slice = idx_data[idx_data['trade_date'] <= date(2026, 4, 2)].tail(70)
    l0 = layer0_market_risk(date(2026, 4, 2), cfg, idx_slice)
    f.write(f"  L0: passed={l0['passed']}, regime={l0['regime']}, breadth={l0.get('breadth_ratio', 'N/A')}\n"); f.flush()

    f.write("Step 4: Layer1...\n"); f.flush()
    from strategies.meta_strategy.db_backtester import layer1_multi_factor_scan
    
    factor_df = layer1_multi_factor_scan(date(2026, 4, 2), cfg, daily_df=daily)
    f.write(f"  L1: {len(factor_df)} stocks\n"); f.flush()
    if not factor_df.empty:
        f.write(f"  Top3 scores: {factor_df['total_score'].head(3).tolist()}\n"); f.flush()
        f.write(f"  Top3 codes: {factor_df['ts_code'].head(3).tolist()}\n"); f.flush()

    f.write("DONE!\n"); f.flush()
except Exception as e:
    import traceback
    f.write(f"ERROR: {e}\n"); f.flush()
    f.write(traceback.format_exc()); f.flush()

f.close()

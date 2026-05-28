#!/usr/bin/env python3
"""最小化数据库回测测试"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '6543'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

print("Step 1: Testing DB connection...", flush=True)
from core.db.connection import get_db_fresh
conn = get_db_fresh()
cur = conn.cursor()
cur.execute("SELECT count(*) FROM daily_quotes WHERE trade_date = '2026-04-01'")
count = cur.fetchone()[0]
print(f"  2026-04-01: {count} records", flush=True)
conn.close()

print("Step 2: Testing data adapter...", flush=True)
from strategies.meta_strategy.db_data_adapter import get_trading_days, get_daily_quotes_for_date, get_market_overview
from datetime import date

trading_days = get_trading_days(date(2026, 4, 1), date(2026, 4, 10))
print(f"  Trading days: {len(trading_days)}", flush=True)

daily = get_daily_quotes_for_date(date(2026, 4, 1), min_amount=5e7)
print(f"  Active stocks: {len(daily)}", flush=True)

overview = get_market_overview(date(2026, 4, 1))
print(f"  Market overview: advancers={overview['advancers']}, decliners={overview['decliners']}", flush=True)

print("Step 3: Testing backtester import...", flush=True)
from strategies.meta_strategy.db_backtester import DbBacktester, MetaBacktestConfig
from strategies.meta_strategy.position_manager import PositionManagerConfig

print("Step 4: Running minimal backtest...", flush=True)
cfg = MetaBacktestConfig()
cfg.start_date = '2026-04-01'
cfg.end_date = '2026-04-10'  # Only 1 week!
cfg.strategy_compare_enabled = False
cfg.layer1_top_n = 10
cfg.layer1_min_total_score = 0.50

bt = DbBacktester(cfg, PositionManagerConfig())
result = bt.run()

if result:
    print(result['summary'], flush=True)
    print("SUCCESS!", flush=True)
else:
    print("FAILED - no result!", flush=True)

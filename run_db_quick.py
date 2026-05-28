#!/usr/bin/env python3
"""快速数据库回测 - 短区间验证"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '6543'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

from strategies.meta_strategy.db_backtester import DbBacktester, MetaBacktestConfig
from strategies.meta_strategy.position_manager import PositionManagerConfig

cfg = MetaBacktestConfig()
cfg.start_date = '2026-04-01'
cfg.end_date = '2026-05-22'
cfg.strategy_compare_enabled = False  # 先不跑对比
cfg.layer1_top_n = 30  # 减少扫描量
cfg.layer1_min_total_score = 0.45  # 提高门槛

print("Quick DB backtest v2.0...")
print(f"  Period: {cfg.start_date} ~ {cfg.end_date}")

bt = DbBacktester(cfg, PositionManagerConfig())
result = bt.run()

if result:
    print(result['summary'])

    from pathlib import Path
    import pandas as pd
    from datetime import datetime, timedelta, timezone
    BEIJING_TZ = timezone(timedelta(hours=8))
    out_dir = Path('./results')
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(BEIJING_TZ).strftime('%Y%m%d_%H%M')

    if result['trades']:
        trades_df = pd.DataFrame(result['trades'])
        trades_path = out_dir / f'db_bt_v2_trades_quick.csv'
        trades_df.to_csv(trades_path, index=False, encoding='utf-8-sig')
        print(f'\n  Trades: {trades_path}')

    if not result['daily_equity'].empty:
        eq_path = out_dir / f'db_bt_v2_equity_quick.csv'
        result['daily_equity'].to_csv(eq_path, index=False, encoding='utf-8-sig')
        print(f'  Equity: {eq_path}')

    report_path = out_dir / f'db_bt_v2_report_quick.txt'
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(result['summary'])
    print(f'  Report: {report_path}')
else:
    print("Backtest failed!")
    sys.exit(1)

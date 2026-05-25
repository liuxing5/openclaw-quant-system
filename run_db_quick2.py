#!/usr/bin/env python3
"""运行数据库回测 v2.0 - 输出到文件"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '5432'  # 直连模式
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

import logging

# 日志同时输出到文件和控制台
log_file = 'db_backtest.log'
file_handler = logging.FileHandler(log_file, mode='w', encoding='utf-8')
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                    handlers=[file_handler, logging.StreamHandler(sys.stdout)])

from strategies.meta_strategy.db_backtester import DbBacktester, MetaBacktestConfig
from strategies.meta_strategy.position_manager import PositionManagerConfig

cfg = MetaBacktestConfig()
cfg.start_date = '2026-04-01'
cfg.end_date = '2026-05-22'
cfg.strategy_compare_enabled = False
cfg.layer1_top_n = 50
cfg.layer1_min_total_score = 0.35
cfg.layer0_min_advancers_ratio = 0.30

print(f"Quick DB backtest v2.0... Period: {cfg.start_date} ~ {cfg.end_date}", flush=True)

bt = DbBacktester(cfg, PositionManagerConfig())
try:
    result = bt.run()
except Exception as e:
    import traceback
    print(f"ERROR: {e}", flush=True)
    print(traceback.format_exc(), flush=True)
    result = None

if result:
    summary = result['summary']
    print(summary, flush=True)

    from pathlib import Path
    import pandas as pd

    out_dir = Path('./results')
    out_dir.mkdir(parents=True, exist_ok=True)

    if result['trades']:
        trades_df = pd.DataFrame(result['trades'])
        trades_path = out_dir / 'db_bt_v2_trades_quick.csv'
        trades_df.to_csv(trades_path, index=False, encoding='utf-8-sig')
        print(f'  Trades: {trades_path}', flush=True)

    if not result['daily_equity'].empty:
        eq_path = out_dir / 'db_bt_v2_equity_quick.csv'
        result['daily_equity'].to_csv(eq_path, index=False, encoding='utf-8-sig')
        print(f'  Equity: {eq_path}', flush=True)

    report_path = out_dir / 'db_bt_v2_report_quick.txt'
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(summary)
    print(f'  Report: {report_path}', flush=True)

    print("DONE!", flush=True)
else:
    print("Backtest failed!", flush=True)
    sys.exit(1)

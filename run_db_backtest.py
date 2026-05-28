#!/usr/bin/env python3
"""运行数据库回测 v2.0"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 设置数据库环境变量
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
cfg.start_date = '2026-01-01'
cfg.end_date = '2026-05-25'
cfg.strategy_compare_enabled = False  # 关闭对比加速

print("Starting DB backtest v2.0...")
print(f"  Period: {cfg.start_date} ~ {cfg.end_date}")

bt = DbBacktester(cfg, PositionManagerConfig())
result = bt.run()

if result:
    print(result['summary'])

    from pathlib import Path
    import pandas as pd
    import json
    from datetime import datetime, timedelta, timezone

    BEIJING_TZ = timezone(timedelta(hours=8))
    out_dir = Path('./results')
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(BEIJING_TZ).strftime('%Y%m%d_%H%M')

    if result['trades']:
        trades_df = pd.DataFrame(result['trades'])
        trades_path = out_dir / f'db_bt_v2_trades_{timestamp}.csv'
        trades_df.to_csv(trades_path, index=False, encoding='utf-8-sig')
        print(f'\n  Trades: {trades_path}')

    if not result['daily_equity'].empty:
        eq_path = out_dir / f'db_bt_v2_equity_{timestamp}.csv'
        result['daily_equity'].to_csv(eq_path, index=False, encoding='utf-8-sig')
        print(f'  Equity: {eq_path}')

    report_path = out_dir / f'db_bt_v2_report_{timestamp}.txt'
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(result['summary'])
    print(f'  Report: {report_path}')

    if result.get('compare_results'):
        compare_path = out_dir / f'db_bt_v2_compare_{timestamp}.json'
        compare_data = {}
        for sn, cmp in result['compare_results'].items():
            trades = cmp.get('trades', [])
            if trades:
                pnls = [t['pnl_pct'] for t in trades]
                import numpy as np
                compare_data[sn] = {
                    'total_trades': len(trades),
                    'win_rate': round(len([p for p in pnls if p > 0]) / len(pnls), 4) if pnls else 0,
                    'avg_return': round(float(np.mean(pnls)), 4) if pnls else 0,
                    'total_return': round(float(sum(pnls)), 4) if pnls else 0,
                }
            else:
                compare_data[sn] = {'total_trades': 0}
        with open(compare_path, 'w', encoding='utf-8') as f:
            json.dump(compare_data, f, ensure_ascii=False, indent=2)
        print(f'  Compare: {compare_path}')
else:
    print("Backtest failed!")
    sys.exit(1)

#!/usr/bin/env python3
"""快速回测脚本 - v2.0验证"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

from strategies.meta_strategy.baostock_backtester import (
    BaostockBacktester, MetaBacktestConfig
)
from strategies.meta_strategy.position_manager import PositionManagerConfig
import pandas as pd
from pathlib import Path

# 短区间快速验证
cfg = MetaBacktestConfig()
cfg.start_date = '2026-04-01'
cfg.end_date = '2026-05-15'
cfg.strategy_compare_enabled = False  # 先不跑对比，加速
cfg.layer1_top_n = 30  # 减少扫描量
cfg.layer1_min_total_score = 0.45  # 提高门槛减少候选

print("=" * 50)
print("  融合元策略回测 v2.0 - 快速验证")
print(f"  区间: {cfg.start_date} ~ {cfg.end_date}")
print("=" * 50)

bt = BaostockBacktester(cfg, PositionManagerConfig())
result = bt.run()

if result:
    print(result['summary'])

    out_dir = Path('./results')
    out_dir.mkdir(parents=True, exist_ok=True)

    if not result['daily_equity'].empty:
        eq_path = out_dir / 'meta_bt_v2_equity_quick.csv'
        result['daily_equity'].to_csv(eq_path, index=False, encoding='utf-8-sig')
        print(f"\n  权益曲线: {eq_path}")

    report_path = out_dir / 'meta_bt_v2_report_quick.txt'
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(result['summary'])
    print(f"  回测报告: {report_path}")

    if result.get('trades'):
        trades_df = pd.DataFrame(result['trades'])
        trades_path = out_dir / 'meta_bt_v2_trades_quick.csv'
        trades_df.to_csv(trades_path, index=False, encoding='utf-8-sig')
        print(f"  交易记录: {trades_path}")
else:
    print("回测失败，无结果")

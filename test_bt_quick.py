"""快速回测测试 - 缩短时间范围"""
import sys, os, time
sys.path.insert(0, 'd:/pythonProject/openclaw-quant-system')

os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '5432'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

print('Step 1: importing meta_engine...')
sys.stdout.flush()
from strategies.meta_strategy import meta_engine
print('Step 1 OK')
sys.stdout.flush()

print('Step 2: importing fast_backtester...')
sys.stdout.flush()
from strategies.meta_strategy import fast_backtester
print('Step 2 OK')
sys.stdout.flush()

# 使用更短的回测区间
from strategies.meta_strategy.fast_backtester import FastBacktester, BacktestConfig
from strategies.meta_strategy.meta_engine import MetaStrategyConfig
from strategies.meta_strategy.position_manager import PositionManagerConfig

meta_cfg = MetaStrategyConfig(
    layer0_min_advancers=2000,
    layer1_min_total_score=0.45,
    layer1_rsi_max=70.0,
    layer2_min_avg_amount_20d=5e7,
    layer2_turn_rate_min=2.0,
    layer2_turn_rate_max=20.0,
    layer3_volume_breakout_mult=2.0,
    layer3_min_launch_score=0.25,
    layer5_min_quant_score=75,
    layer6_hard_stop_loss_pct=0.05,
    layer6_overnight_stop_pct=0.02,
    layer6_trailing_activate_pct=0.05,
    layer6_trailing_stop_pct=0.03,
    layer6_max_holding_days=10,
    max_final_candidates=8,
)

pm_cfg = PositionManagerConfig(
    hard_stop_loss_pct=0.05,
    trailing_activate_pct=0.05,
    trailing_stop_pct=0.03,
    max_holding_days=10,
    overnight_stop_pct=0.02,
    max_positions=8,
    single_position_pct=0.125,
)

bt_cfg = BacktestConfig(
    start_date="2026-01-01",  # 只测4个月
    end_date="2026-05-15",
    initial_capital=1_000_000.0,
    max_positions=8,
    single_position_pct=0.125,
)

print(f'Step 3: running backtest ({bt_cfg.start_date} ~ {bt_cfg.end_date})...')
sys.stdout.flush()

backtester = FastBacktester(meta_cfg, pm_cfg, bt_cfg)
result = backtester.run()

if result:
    print(result['summary'])
    print('Step 3 OK')
sys.stdout.flush()

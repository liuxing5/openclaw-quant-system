"""回测 - 写输出到文件"""
import sys, os, time, logging, traceback
sys.path.insert(0, 'd:/pythonProject/openclaw-quant-system')

# 日志写文件
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler('bt_output.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ],
    force=True
)

os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '5432'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

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
    start_date="2026-04-01",
    end_date="2026-05-15",
    initial_capital=1_000_000.0,
    max_positions=8,
    single_position_pct=0.125,
)

print(f'Running backtest ({bt_cfg.start_date} ~ {bt_cfg.end_date})...', flush=True)

try:
    backtester = FastBacktester(meta_cfg, pm_cfg, bt_cfg)
    result = backtester.run()

    if result:
        with open('bt_result.txt', 'w', encoding='utf-8') as f:
            f.write(result['summary'])
        print(result['summary'])
        print('DONE', flush=True)
    else:
        print('NO RESULT', flush=True)
except Exception as e:
    print(f'FATAL ERROR: {e}', flush=True)
    traceback.print_exc()

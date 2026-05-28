"""回测 v9: 修复信号衰减+涨停板保护(仅排除已涨停)+ST过滤+速度优化"""
import sys, os, time, logging, traceback, json
sys.path.insert(0, 'd:/pythonProject/openclaw-quant-system')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler('d:/pythonProject/openclaw-quant-system/bt_output_v9.log', encoding='utf-8', mode='w'),
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
    layer0_min_advancers=1200,
    layer0_partial_cap=0.60,
    layer1_min_total_score=0.45,
    layer1_rsi_max=70.0,
    layer1_top_n=500,
    layer2_min_avg_amount_20d=5e7,
    layer2_turn_rate_min=2.0,
    layer2_turn_rate_max=20.0,
    layer2_min_circulating_mcap=2e9,
    layer3_volume_breakout_mult=2.0,
    layer3_min_launch_score=0.25,
    layer5_min_quant_score=75,
    layer5_pct_range_low=2.0,
    layer5_pct_range_high=6.0,
    layer6_hard_stop_loss_pct=0.05,
    layer6_overnight_stop_pct=0.02,
    layer6_trailing_activate_pct=0.08,
    layer6_trailing_stop_pct=0.05,
    layer6_max_holding_days=15,
    weights_bull={'factor': 0.35, 'launch': 0.25, 'llm': 0.10, 'overnight': 0.30},
    weights_oscillate={'factor': 0.25, 'launch': 0.15, 'llm': 0.20, 'overnight': 0.40},
    weights_bear={'factor': 0.20, 'launch': 0.10, 'llm': 0.25, 'overnight': 0.45},
    max_final_candidates=8,
)

pm_cfg = PositionManagerConfig(
    hard_stop_loss_pct=0.05,
    trailing_activate_pct=0.08,
    trailing_stop_pct=0.05,
    max_holding_days=15,
    overnight_stop_pct=0.02,
    max_positions=8,
    single_position_pct=0.125,
    breakdown_vol_ratio_min=1.8,
    high_vol_ratio_min=3.5,
)

bt_cfg = BacktestConfig(
    start_date="2026-04-01",
    end_date="2026-05-15",
    initial_capital=1_000_000.0,
    max_positions=8,
    single_position_pct=0.125,
)

print(f'Running backtest v9 ({bt_cfg.start_date} ~ {bt_cfg.end_date})...', flush=True)
print(f'v9 fixes: 信号衰减3%+涨停板保护(仅排除已涨停)+ST过滤+batch_size=50+价格预加载', flush=True)

try:
    t0 = time.time()
    backtester = FastBacktester(meta_cfg, pm_cfg, bt_cfg)
    result = backtester.run()
    elapsed = time.time() - t0
    print(f"Backtest elapsed: {elapsed:.0f}s", flush=True)

    if result:
        with open('bt_result_v9.txt', 'w', encoding='utf-8') as f:
            f.write(result['summary'])
        if result['trades']:
            with open('bt_trades_v9.json', 'w', encoding='utf-8') as f:
                json.dump(result['trades'], f, ensure_ascii=False, indent=2, default=str)
        print(result['summary'])
        print('DONE', flush=True)
    else:
        print('NO RESULT', flush=True)
except Exception as e:
    print(f'FATAL ERROR: {e}', flush=True)
    traceback.print_exc()

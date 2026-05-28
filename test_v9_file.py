"""快速测试v9修改：只跑1天"""
import sys, os, time, logging
sys.path.insert(0, 'd:/pythonProject/openclaw-quant-system')

OUT = open('d:/pythonProject/openclaw-quant-system/v9_quick_output.txt', 'w', encoding='utf-8')

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.StreamHandler(OUT)], force=True)

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
    layer6_trailing_activate_pct=0.08,
    layer6_trailing_stop_pct=0.05,
    layer6_max_holding_days=15,
    max_final_candidates=8,
)

pm_cfg = PositionManagerConfig(
    hard_stop_loss_pct=0.05,
    trailing_activate_pct=0.08,
    trailing_stop_pct=0.05,
    max_holding_days=15,
    max_positions=8,
)

bt_cfg = BacktestConfig(
    start_date="2026-04-16",
    end_date="2026-04-18",
    initial_capital=1_000_000.0,
    max_positions=8,
)

print(f'Quick test v9...', file=OUT, flush=True)
t0 = time.time()
try:
    backtester = FastBacktester(meta_cfg, pm_cfg, bt_cfg)
    result = backtester.run()
    elapsed = time.time() - t0
    print(f"Elapsed: {elapsed:.0f}s", file=OUT, flush=True)
    if result:
        print(result['summary'], file=OUT, flush=True)
        print(f"Trades: {len(result['trades'])}", file=OUT, flush=True)
    else:
        print("NO RESULT", file=OUT, flush=True)
except Exception as e:
    import traceback
    print(f"ERROR: {e}", file=OUT, flush=True)
    traceback.print_exc(file=OUT)

OUT.close()

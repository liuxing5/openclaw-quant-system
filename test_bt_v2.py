"""回测 - 优化参数 v2"""
import sys, os, time, logging, traceback
sys.path.insert(0, 'd:/pythonProject/openclaw-quant-system')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler('bt_output_v2.log', encoding='utf-8'),
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

# === 优化参数 ===
# v1结果: 3.28%收益, -0.65%回撤, 30%胜率, 10笔交易
# 问题: L0拒绝太多(61%), L3太严, L5太严, 持仓太短

meta_cfg = MetaStrategyConfig(
    # L0: 降低上涨家数门槛 2000→1500，增加交易机会
    layer0_min_advancers=1500,
    layer0_partial_cap=0.60,  # 风控不通过时允许60%仓位

    # L1: 放宽因子扫描
    layer1_min_total_score=0.35,  # 0.45→0.35
    layer1_rsi_max=80.0,  # 70→80，允许更多强势股
    layer1_top_n=500,

    # L2: 放宽流动性和换手率
    layer2_min_avg_amount_20d=3e7,  # 5e7→3e7
    layer2_turn_rate_min=1.5,  # 2.0→1.5
    layer2_turn_rate_max=25.0,  # 20→25
    layer2_min_circulating_mcap=1e9,  # 2e9→1e9

    # L3: 放宽启动信号
    layer3_volume_breakout_mult=1.5,  # 2.0→1.5
    layer3_min_launch_score=0.15,  # 0.25→0.15
    layer3_turnover_min=3.0,  # 5.0→3.0

    # L5: 降低八步法门槛
    layer5_min_quant_score=65,  # 75→65
    layer5_pct_range_low=1.5,  # 2.0→1.5
    layer5_pct_range_high=7.0,  # 6.0→7.0
    layer5_vol_ratio_min=1.2,  # 1.5→1.2

    # L6: 放宽止损，让利润奔跑
    layer6_hard_stop_loss_pct=0.07,  # 0.05→0.07
    layer6_overnight_stop_pct=0.03,  # 0.02→0.03
    layer6_trailing_activate_pct=0.08,  # 0.05→0.08，浮盈8%才激活移动止盈
    layer6_trailing_stop_pct=0.04,  # 0.03→0.04
    layer6_max_holding_days=15,  # 10→15

    # 动态权重：增加因子和启动信号权重
    weights_bull={'factor': 0.40, 'launch': 0.30, 'llm': 0.05, 'overnight': 0.25},
    weights_oscillate={'factor': 0.35, 'launch': 0.25, 'llm': 0.10, 'overnight': 0.30},
    weights_bear={'factor': 0.25, 'launch': 0.15, 'llm': 0.20, 'overnight': 0.40},

    max_final_candidates=10,  # 8→10
)

pm_cfg = PositionManagerConfig(
    hard_stop_loss_pct=0.07,  # 0.05→0.07
    trailing_activate_pct=0.08,  # 0.05→0.08
    trailing_stop_pct=0.04,  # 0.03→0.04
    max_holding_days=15,  # 10→15
    overnight_stop_pct=0.03,  # 0.02→0.03
    max_positions=10,  # 8→10
    single_position_pct=0.10,  # 0.125→0.10
)

bt_cfg = BacktestConfig(
    start_date="2026-04-01",
    end_date="2026-05-15",
    initial_capital=1_000_000.0,
    max_positions=10,
    single_position_pct=0.10,
)

print(f'Running backtest v2 ({bt_cfg.start_date} ~ {bt_cfg.end_date})...', flush=True)

try:
    backtester = FastBacktester(meta_cfg, pm_cfg, bt_cfg)
    result = backtester.run()

    if result:
        with open('bt_result_v2.txt', 'w', encoding='utf-8') as f:
            f.write(result['summary'])
        print(result['summary'])
        print('DONE', flush=True)
    else:
        print('NO RESULT', flush=True)
except Exception as e:
    print(f'FATAL ERROR: {e}', flush=True)
    traceback.print_exc()

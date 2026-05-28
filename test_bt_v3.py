"""回测 - v3: 保持选股精度+增加交易机会+优化止损"""
import sys, os, time, logging, traceback, json
sys.path.insert(0, 'd:/pythonProject/openclaw-quant-system')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler('bt_output_v3.log', encoding='utf-8'),
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

# === v3 策略 ===
# v1: 3.28%收益, 0.65%回撤, 30%胜率, 18.94盈亏比, 10笔
# v2: -1.02%收益, 2.28%回撤, 13.3%胜率, 4.10盈亏比, 15笔
# 
# v3核心思路:
# 1. 保持v1的选股精度（L3/L5不放宽）
# 2. 降低L0风控门槛（1500→1200），增加交易机会
# 3. 放宽L2换手率范围（1.5-30），减少误杀
# 4. 移动止盈激活从5%→6%，给利润更多空间
# 5. 硬止损从5%→6%，避免被洗出
# 6. 持仓天数从10→12天

meta_cfg = MetaStrategyConfig(
    # L0: 降低上涨家数门槛，增加交易机会
    layer0_min_advancers=1200,  # v1=2000, v2=1500, v3=1200
    layer0_partial_cap=0.60,

    # L1: 保持v1精度
    layer1_min_total_score=0.40,  # v1=0.45, v3=0.40（微调）
    layer1_rsi_max=75.0,  # 保持v1
    layer1_top_n=500,

    # L2: 放宽换手率范围，减少误杀好股票
    layer2_min_avg_amount_20d=5e7,  # 保持v1
    layer2_turn_rate_min=1.5,  # v1=2.0, v3=1.5
    layer2_turn_rate_max=30.0,  # v1=20, v3=30
    layer2_min_circulating_mcap=2e9,  # 保持v1

    # L3: 保持v1精度
    layer3_volume_breakout_mult=2.0,  # 保持v1
    layer3_min_launch_score=0.25,  # 保持v1

    # L5: 保持v1精度
    layer5_min_quant_score=70,  # v1=75, v3=70（微调）
    layer5_pct_range_low=1.5,  # v1=2.0, v3=1.5
    layer5_pct_range_high=7.0,  # v1=6.0, v3=7.0

    # L6: 优化止损
    layer6_hard_stop_loss_pct=0.06,  # v1=0.05, v3=0.06
    layer6_overnight_stop_pct=0.025,  # v1=0.02, v3=0.025
    layer6_trailing_activate_pct=0.06,  # v1=0.05, v3=0.06
    layer6_trailing_stop_pct=0.035,  # v1=0.03, v3=0.035
    layer6_max_holding_days=12,  # v1=10, v3=12

    # 动态权重：增加因子权重
    weights_bull={'factor': 0.40, 'launch': 0.25, 'llm': 0.05, 'overnight': 0.30},
    weights_oscillate={'factor': 0.35, 'launch': 0.20, 'llm': 0.10, 'overnight': 0.35},
    weights_bear={'factor': 0.25, 'launch': 0.10, 'llm': 0.20, 'overnight': 0.45},

    max_final_candidates=8,
)

pm_cfg = PositionManagerConfig(
    hard_stop_loss_pct=0.06,
    trailing_activate_pct=0.06,
    trailing_stop_pct=0.035,
    max_holding_days=12,
    overnight_stop_pct=0.025,
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

print(f'Running backtest v3 ({bt_cfg.start_date} ~ {bt_cfg.end_date})...', flush=True)

try:
    backtester = FastBacktester(meta_cfg, pm_cfg, bt_cfg)
    result = backtester.run()

    if result:
        with open('bt_result_v3.txt', 'w', encoding='utf-8') as f:
            f.write(result['summary'])
        if result['trades']:
            with open('bt_trades_v3.json', 'w', encoding='utf-8') as f:
                json.dump(result['trades'], f, ensure_ascii=False, indent=2, default=str)
        print(result['summary'])
        print('DONE', flush=True)
    else:
        print('NO RESULT', flush=True)
except Exception as e:
    print(f'FATAL ERROR: {e}', flush=True)
    traceback.print_exc()
